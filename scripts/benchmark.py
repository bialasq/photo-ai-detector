#!/usr/bin/env python3
"""
Reproducible performance benchmarks (tasks 2.0.1 / 2.0.2).

Default mode exercises the database layer with synthetic data (no DeepFace).
Use ``--live-ai`` to include face detection when a venv with TensorFlow is active.

Example:
  python scripts/benchmark.py --dataset 1k --output benchmark-1k.json
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET_SIZES = {"1k": 1_000, "5k": 5_000, "10k": 10_000}


@dataclass
class BenchmarkResult:
    dataset: str
    photo_count: int
    scan_time_seconds: float
    gallery_query_p50_ms: float
    gallery_query_p95_ms: float
    db_size_bytes: int
    batch_insert_seconds: float
    commit_hash: str
    platform: str
    mode: str


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


def run_benchmark(*, dataset: str, live_ai: bool) -> BenchmarkResult:
    from database import DatabaseManager, PendingFaceInsert

    photo_count = DATASET_SIZES[dataset]
    db_path = ROOT / f"_benchmark_{dataset}.db"
    if db_path.is_file():
        db_path.unlink()

    manager = DatabaseManager(str(db_path))
    manager.create_tables()

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

    query_samples: list[float] = []
    for _ in range(200):
        started = time.perf_counter()
        manager.get_all_photos(processed_only=True, limit=100)
        query_samples.append((time.perf_counter() - started) * 1000)

    return BenchmarkResult(
        dataset=dataset,
        photo_count=photo_count,
        scan_time_seconds=round(scan_elapsed, 3),
        gallery_query_p50_ms=round(_percentile(query_samples, 50), 3),
        gallery_query_p95_ms=round(_percentile(query_samples, 95), 3),
        db_size_bytes=db_path.stat().st_size,
        batch_insert_seconds=round(batch_elapsed, 3),
        commit_hash=_git_commit(),
        platform=platform.platform(),
        mode="live-ai" if live_ai else "db-synthetic",
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
        help="Include DeepFace detection benchmarks (requires ML stack)",
    )
    args = parser.parse_args()

    result = run_benchmark(dataset=args.dataset, live_ai=args.live_ai)
    payload = asdict(result)
    print(json.dumps(payload, indent=2))

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
