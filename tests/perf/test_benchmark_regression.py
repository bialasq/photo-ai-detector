"""Task 3.2.4 — db-synthetic benchmark regression thresholds (loose, anti-flake)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_BENCHMARK_DB = ROOT / "_benchmark_1k.db"

# Baseline: docs/BENCHMARKS.md db-synthetic 1k (commit e8202a6, Windows 11).
# Ceilings catch ~2–8× regressions; wide enough for slow CI runners.
THRESHOLD_SCAN_TIME_SECONDS = 25.0
THRESHOLD_BATCH_INSERT_SECONDS = 15.0
THRESHOLD_GALLERY_QUERY_P95_MS = 10.0
THRESHOLD_CLUSTER_IDENTIFY_P95_MS = 2.0
THRESHOLD_DB_SIZE_BYTES = 25_000_000


def _load_run_benchmark():
    spec = importlib.util.spec_from_file_location(
        "photo_benchmark_runner",
        ROOT / "scripts" / "benchmark.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"scripts/benchmark.py not found under {ROOT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.run_benchmark


@pytest.mark.slow
def test_db_synthetic_1k_regression_thresholds(capsys: pytest.CaptureFixture[str]) -> None:
    run_benchmark = _load_run_benchmark()
    try:
        result = run_benchmark(dataset="1k", live_ai=False, seed=42)

        assert result.mode == "db-synthetic"
        assert result.deepface_invoked is False
        assert result.photo_count == 1_000

        metrics = {
            "scan_time_seconds": (result.scan_time_seconds, THRESHOLD_SCAN_TIME_SECONDS),
            "batch_insert_seconds": (
                result.batch_insert_seconds,
                THRESHOLD_BATCH_INSERT_SECONDS,
            ),
            "gallery_query_p95_ms": (
                result.gallery_query_p95_ms,
                THRESHOLD_GALLERY_QUERY_P95_MS,
            ),
            "cluster_identify_p95_ms": (
                result.cluster_identify_p95_ms,
                THRESHOLD_CLUSTER_IDENTIFY_P95_MS,
            ),
            "db_size_bytes": (result.db_size_bytes, THRESHOLD_DB_SIZE_BYTES),
        }

        print("\n--- db-synthetic 1k benchmark vs regression ceilings ---")
        for name, (actual, ceiling) in metrics.items():
            if actual is None:
                print(f"  {name}: skipped (None)")
                continue
            margin_x = actual / ceiling if ceiling else 0.0
            headroom_pct = (1.0 - margin_x) * 100.0
            print(
                f"  {name}: {actual} < {ceiling} "
                f"(using {margin_x * 100:.1f}% of ceiling, {headroom_pct:.1f}% headroom)"
            )
        print("---")

        assert result.scan_time_seconds < THRESHOLD_SCAN_TIME_SECONDS
        assert result.batch_insert_seconds is not None
        assert result.batch_insert_seconds < THRESHOLD_BATCH_INSERT_SECONDS
        assert result.gallery_query_p95_ms < THRESHOLD_GALLERY_QUERY_P95_MS
        assert result.cluster_identify_p95_ms < THRESHOLD_CLUSTER_IDENTIFY_P95_MS
        assert result.db_size_bytes < THRESHOLD_DB_SIZE_BYTES
    finally:
        if _BENCHMARK_DB.is_file():
            _BENCHMARK_DB.unlink()
