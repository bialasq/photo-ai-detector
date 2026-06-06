#!/usr/bin/env python3
"""
Reproducible performance benchmarks (tasks 2.0.1 / 2.0.2).

Default mode exercises the database layer with synthetic data (no DeepFace).
Use ``--live-ai`` to run real DeepFace inference via ``AICoreEngine.process_image``.

Example:
  python scripts/benchmark.py --dataset 1k --output results/benchmark-1k.json
  python scripts/benchmark.py --dataset 1k --live-ai --live-ai-limit 50
  python scripts/benchmark.py --dataset 5k --seed 42 --generate-fixtures
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import subprocess
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET_SIZES = {"1k": 1_000, "5k": 5_000, "10k": 10_000}
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "benchmarks"
DEFAULT_RANDOM_SEED = 42
DEFAULT_LIVE_AI_LIMIT = 200
LIVE_AI_FACE_BATCH_SIZE = 100

if TYPE_CHECKING:
    from ai_core import AICoreEngine
    from database import DatabaseManager, PendingFaceInsert


@dataclass
class BenchmarkResult:
    dataset: str
    photo_count: int
    scan_time_seconds: float
    ram_peak_mb: float
    gallery_query_p50_ms: float
    gallery_query_p95_ms: float
    cluster_identify_p50_ms: float
    cluster_identify_p95_ms: float
    silhouette_score: float | None
    db_size_bytes: int
    batch_insert_seconds: float | None
    commit_hash: str
    platform: str
    mode: str
    random_seed: int
    deepface_invoked: bool
    faces_detected: int
    model_name: str | None
    detector_backend: str | None
    cold_start_seconds: float | None


def _git_commit() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=ROOT,
                text=True,
            )
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[index]


def _current_rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        if tracemalloc.is_tracing():
            _current, peak = tracemalloc.get_traced_memory()
            return peak / (1024 * 1024)
        return 0.0


def _require_psutil_for_live_ai() -> None:
    try:
        import psutil  # noqa: F401
    except ImportError:
        print(
            "live-ai requires psutil for accurate RSS measurement (pip install psutil)",
            file=sys.stderr,
        )
        sys.exit(1)


def _live_ai_rss_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / (1024 * 1024)


def generate_fixture_dataset(dataset: str, *, seed: int = DEFAULT_RANDOM_SEED) -> Path:
    """Create synthetic JPEG fixtures (CC0-style generated noise, task 2.0.1)."""
    from PIL import Image

    count = DATASET_SIZES[dataset]
    target_dir = FIXTURE_ROOT / dataset
    target_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    for index in range(count):
        path = target_dir / f"bench_{index:06d}.jpg"
        if path.is_file():
            continue
        color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        Image.new("RGB", (64, 64), color=color).save(path, format="JPEG")

    return target_dir


def _list_fixture_images(dataset: str, *, seed: int) -> list[Path]:
    fixture_dir = FIXTURE_ROOT / dataset
    paths = sorted(fixture_dir.glob("bench_*.jpg"))
    if not paths:
        generate_fixture_dataset(dataset, seed=seed)
        paths = sorted(fixture_dir.glob("bench_*.jpg"))
    return paths


def _cluster_identify_samples(
    manager,
    *,
    sample_count: int,
    seed: int,
) -> tuple[list[float], float | None]:
    """Measure FAISS top-1 search latency and optional silhouette on synthetic clusters."""
    from ai_core import l2_normalize
    from vector_store import FaceVectorStore

    rng = random.Random(seed)
    store = FaceVectorStore()
    face_ids: list[int] = []

    for cluster_index in range(5):
        base = [0.0] * 512
        base[cluster_index] = 1.0
        base_vector = l2_normalize(base)
        for member in range(20):
            noise = [value + rng.uniform(-0.02, 0.02) for value in base_vector]
            embedding = l2_normalize(noise)
            photo_id = manager.insert_photo(
                f"/benchmark/cluster_{cluster_index}/face_{member}.jpg"
            )
            face_id = manager.insert_face(
                photo_id=photo_id,
                embedding=embedding,
                bounding_box={"x": 0, "y": 0, "w": 32, "h": 32},
            )
            store.add(face_id, embedding)
            face_ids.append(face_id)

    timings: list[float] = []
    query_vector = l2_normalize([0.01] * 512)
    for _ in range(sample_count):
        started = time.perf_counter()
        store.search(query_vector, k=5)
        timings.append((time.perf_counter() - started) * 1000)

    silhouette: float | None = None
    try:
        import numpy as np
        from sklearn.cluster import DBSCAN
        from sklearn.metrics import silhouette_score

        rows = manager.iter_all_faces_with_embeddings()
        matrix = np.asarray([row.embedding for row in rows], dtype=np.float64)
        labels = DBSCAN(eps=0.35, min_samples=2, metric="cosine", n_jobs=1).fit_predict(
            matrix
        )
        named = labels >= 0
        if len(set(int(label) for label in labels[named])) >= 2:
            silhouette = float(
                silhouette_score(matrix[named], labels[named], metric="cosine")
            )
    except Exception:
        silhouette = None

    return timings, silhouette


def _append_detections_to_buffer(
    *,
    photo_id: int,
    detections: list[dict],
    buffer: list[PendingFaceInsert],
) -> int:
    from database import PendingFaceInsert

    for detection in detections:
        buffer.append(
            PendingFaceInsert(
                photo_id=photo_id,
                embedding=detection["embedding"],
                bounding_box=detection["bounding_box"],
            )
        )
    return len(detections)


def _flush_face_buffer(manager: DatabaseManager, buffer: list[PendingFaceInsert]) -> None:
    if not buffer:
        return
    manager.insert_faces_batch(buffer, batch_size=LIVE_AI_FACE_BATCH_SIZE)
    buffer.clear()


def _run_live_ai_ingest(
    manager: DatabaseManager,
    engine: AICoreEngine,
    image_paths: list[Path],
) -> tuple[float, float, int, bool, float]:
    """
    Run DeepFace detection on fixture JPEGs.

    Returns:
        scan_time_seconds (process_image loop only, excluding cold start),
        cold_start_seconds,
        faces_detected,
        deepface_invoked.
    """
    from ai_core import FaceDetectionError

    if not image_paths:
        print("live-ai: no fixture images to process", file=sys.stderr)
        sys.exit(1)

    peak_rss = _live_ai_rss_mb()
    pending_faces: list[PendingFaceInsert] = []
    faces_detected = 0
    deepface_invoked = False

    first_path = image_paths[0].resolve()
    first_photo_id = manager.insert_photo(str(first_path))
    cold_started = time.perf_counter()
    try:
        first_detections = engine.process_image(first_path)
    except FaceDetectionError as exc:
        print(f"live-ai: DeepFace failed during cold start: {exc}", file=sys.stderr)
        sys.exit(1)
    cold_start_seconds = time.perf_counter() - cold_started
    deepface_invoked = True
    peak_rss = max(peak_rss, _live_ai_rss_mb())
    faces_detected += _append_detections_to_buffer(
        photo_id=first_photo_id,
        detections=first_detections,
        buffer=pending_faces,
    )
    if len(pending_faces) >= LIVE_AI_FACE_BATCH_SIZE:
        _flush_face_buffer(manager, pending_faces)
        peak_rss = max(peak_rss, _live_ai_rss_mb())

    scan_elapsed = 0.0
    for image_path in image_paths[1:]:
        resolved = image_path.resolve()
        photo_id = manager.insert_photo(str(resolved))
        infer_started = time.perf_counter()
        detections = engine.process_image(resolved)
        scan_elapsed += time.perf_counter() - infer_started
        peak_rss = max(peak_rss, _live_ai_rss_mb())
        faces_detected += _append_detections_to_buffer(
            photo_id=photo_id,
            detections=detections,
            buffer=pending_faces,
        )
        if len(pending_faces) >= LIVE_AI_FACE_BATCH_SIZE:
            _flush_face_buffer(manager, pending_faces)
            peak_rss = max(peak_rss, _live_ai_rss_mb())

    _flush_face_buffer(manager, pending_faces)
    peak_rss = max(peak_rss, _live_ai_rss_mb())

    if not deepface_invoked:
        print("live-ai requested but DeepFace path not executed", file=sys.stderr)
        sys.exit(1)

    return scan_elapsed, cold_start_seconds, faces_detected, deepface_invoked, peak_rss


def _run_db_synthetic_ingest(
    manager: DatabaseManager,
    *,
    photo_count: int,
) -> tuple[float, float, float]:
    from database import PendingFaceInsert

    scan_started = time.perf_counter()
    photo_ids: list[int] = []
    for index in range(photo_count):
        photo_ids.append(manager.insert_photo(f"/benchmark/photo_{index:06d}.jpg"))
    scan_elapsed = time.perf_counter() - scan_started

    batch_started = time.perf_counter()
    pending = [
        PendingFaceInsert(
            photo_id=photo_ids[index % len(photo_ids)],
            embedding=[0.01] * 512,
            bounding_box={"x": 1, "y": 2, "w": 10, "h": 10},
        )
        for index in range(min(photo_count * 2, 10_000))
    ]
    manager.insert_faces_batch(pending, batch_size=100)
    batch_elapsed = time.perf_counter() - batch_started

    return scan_elapsed, batch_elapsed, 0.0


def run_benchmark(
    *,
    dataset: str,
    live_ai: bool,
    seed: int,
    live_ai_limit: int = DEFAULT_LIVE_AI_LIMIT,
) -> BenchmarkResult:
    from database import DatabaseManager

    db_path = ROOT / f"_benchmark_{dataset}.db"
    if db_path.is_file():
        db_path.unlink()

    manager = DatabaseManager(str(db_path))
    manager.create_tables()

    deepface_invoked = False
    faces_detected = 0
    model_name: str | None = None
    detector_backend: str | None = None
    cold_start_seconds: float | None = None
    batch_elapsed: float | None

    if live_ai:
        from ai_core import AICoreEngine, FaceDetectionError, verify_ai_runtime_dependencies

        _require_psutil_for_live_ai()
        try:
            verify_ai_runtime_dependencies()
        except FaceDetectionError as exc:
            print(f"live-ai: AI runtime check failed: {exc}", file=sys.stderr)
            sys.exit(1)

        fixture_paths = _list_fixture_images(dataset, seed=seed)
        if not fixture_paths:
            print(
                f"live-ai: no fixtures under {FIXTURE_ROOT / dataset} after generation",
                file=sys.stderr,
            )
            sys.exit(1)

        dataset_cap = DATASET_SIZES[dataset]
        image_paths = fixture_paths[: min(live_ai_limit, dataset_cap, len(fixture_paths))]
        photo_count = len(image_paths)

        engine = AICoreEngine()
        scan_elapsed, cold_start_seconds, faces_detected, deepface_invoked, peak_rss = (
            _run_live_ai_ingest(manager, engine, image_paths)
        )
        model_name = engine.model_name
        detector_backend = engine.active_detector_backend
        batch_elapsed = None
        mode = "live-ai"
    else:
        photo_count = DATASET_SIZES[dataset]
        tracemalloc.start()
        peak_rss = 0.0

        scan_elapsed, batch_elapsed, _ = _run_db_synthetic_ingest(
            manager,
            photo_count=photo_count,
        )
        peak_rss = max(peak_rss, _current_rss_mb())
        tracemalloc.stop()
        mode = "db-synthetic"

    query_samples: list[float] = []
    for _ in range(200):
        started = time.perf_counter()
        manager.get_all_photos(processed_only=True, limit=100)
        query_samples.append((time.perf_counter() - started) * 1000)

    identify_samples, silhouette = _cluster_identify_samples(
        manager,
        sample_count=100,
        seed=seed,
    )
    if live_ai:
        peak_rss = max(peak_rss, _live_ai_rss_mb())
    else:
        peak_rss = max(peak_rss, _current_rss_mb())

    if live_ai and not deepface_invoked:
        print("live-ai requested but DeepFace path not executed", file=sys.stderr)
        sys.exit(1)

    return BenchmarkResult(
        dataset=dataset,
        photo_count=photo_count,
        scan_time_seconds=round(scan_elapsed, 3),
        ram_peak_mb=round(peak_rss, 2),
        gallery_query_p50_ms=round(_percentile(query_samples, 50), 3),
        gallery_query_p95_ms=round(_percentile(query_samples, 95), 3),
        cluster_identify_p50_ms=round(_percentile(identify_samples, 50), 3),
        cluster_identify_p95_ms=round(_percentile(identify_samples, 95), 3),
        silhouette_score=round(silhouette, 4) if silhouette is not None else None,
        db_size_bytes=db_path.stat().st_size,
        batch_insert_seconds=(
            round(batch_elapsed, 3) if batch_elapsed is not None else None
        ),
        commit_hash=_git_commit(),
        platform=platform.platform(),
        mode=mode,
        random_seed=seed,
        deepface_invoked=deepface_invoked,
        faces_detected=faces_detected,
        model_name=model_name,
        detector_backend=detector_backend,
        cold_start_seconds=(
            round(cold_start_seconds, 3) if cold_start_seconds is not None else None
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Photo Organizer benchmark runner")
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASET_SIZES),
        default="1k",
        help="Synthetic dataset size",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path",
    )
    parser.add_argument(
        "--live-ai",
        action="store_true",
        help="Run DeepFace inference on fixture JPEGs via AICoreEngine (requires ML stack)",
    )
    parser.add_argument(
        "--live-ai-limit",
        type=int,
        default=DEFAULT_LIVE_AI_LIMIT,
        help="Max fixture images to process in --live-ai mode (default: 200)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help="Random seed for reproducible synthetic fixtures",
    )
    parser.add_argument(
        "--generate-fixtures",
        action="store_true",
        help="Generate JPEG fixtures under tests/fixtures/benchmarks/",
    )
    args = parser.parse_args()

    if args.live_ai_limit <= 0:
        print("--live-ai-limit must be a positive integer", file=sys.stderr)
        sys.exit(1)

    if args.generate_fixtures:
        path = generate_fixture_dataset(args.dataset, seed=args.seed)
        print(f"Generated fixtures in {path}", file=sys.stderr)

    result = run_benchmark(
        dataset=args.dataset,
        live_ai=args.live_ai,
        seed=args.seed,
        live_ai_limit=args.live_ai_limit,
    )
    payload = asdict(result)
    print(json.dumps(payload, indent=2))

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
