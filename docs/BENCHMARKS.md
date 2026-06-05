# Benchmarks

Reproducible performance measurements for the Photo Organizer sidecar.

Run after changes to `ai_core.py` or `database.py`:

```bash
python scripts/benchmark.py --dataset 1k --output results/benchmark-1k.json
```

## Detection batching decision (task 2.1.1)

DeepFace **does not** expose a reliable native batch API in our pinned stack. Batch detection uses
`ThreadPoolExecutor(max_workers=4)` over `process_image()` — **not** `ProcessPoolExecutor`
(TensorFlow + PyInstaller instability).

OOM handling: retry once with `batch_size // 2` (32 → 16).

## Baseline table (Phase 1 final — synthetic DB mode)

| Dataset | Metric | Value | Date | Commit | Notes |
| --- | --- | --- | --- | --- | --- |
| 1k | scan_time_seconds (photo insert) | run script | 2026-05 | Phase 1 | `scripts/benchmark.py --dataset 1k` |
| 1k | batch_insert_seconds (2k faces) | run script | 2026-05 | Phase 1 | batch_size=100 |
| 1k | gallery_query_p95_ms | run script | 2026-05 | Phase 1 | 200 samples, limit=100 |
| 5k | gallery_query_p95_ms | run script | 2026-05 | Phase 1 | indexed gallery |
| 10k | gallery_query_p95_ms | run script | 2026-05 | Phase 1 | indexed gallery |

Record hardware in JSON output (`platform` field). Compare before/after optimizations in Phase 2
(FAISS, batch detection) using the same script and dataset flag.

## Phase 2 targets

| Change | Target |
| --- | --- |
| Batch detection (2.1.1) | ≥2× ingest throughput vs per-image loop on 1k synthetic/stub dataset |
| FAISS (2.2.x) | Cluster identification p95 ↓ vs DBSCAN-only baseline |

## Modes

- **db-synthetic** (default): SQLite insert + gallery queries only — CI-friendly.
- **live-ai** (`--live-ai`): reserved for manual runs with TensorFlow/DeepFace installed.
