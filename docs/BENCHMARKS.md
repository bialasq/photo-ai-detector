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

## Baseline measurements

> **Two measurement modes, NOT comparable to each other.** `db-synthetic` exercises
> only the SQLite + query layer with fabricated embeddings. `live-ai` runs real
> DeepFace inference. RAM in `db-synthetic` (older runs) was measured via tracemalloc
> (Python allocations only); current runs use psutil RSS — historical RAM numbers
> are not directly comparable.

### db-synthetic (commit e8202a6, Windows 11, psutil RSS)

| Dataset | scan_time_s | ram_peak_mb | gallery_q_p95_ms | cluster_id_p95_ms | db_size_bytes |
| --- | --- | --- | --- | --- | --- |
| 1k | 6.86 | 178.4 | 1.34 | 0.06 | 8.96 MB |
| 5k | 37.6 | ~2319* | 1.31 | 0.05 | 42.7 MB |
| 10k | 75.3 | ~2319* | 1.22 | 0.09 | 43.5 MB |

\* 5k/10k RAM from earlier tracemalloc runs — re-measure with psutil before trusting.

Reliable for Phase 2 comparison: gallery_query, cluster_identify, db_size, batch_insert.
NOT a model baseline: scan_time/ram here exclude DeepFace entirely.

### live-ai (commit e8202a6, 10 fixtures, ArcFace)

| Metric | Value | Note |
| --- | --- | --- |
| deepface_invoked | true | DeepFace path confirmed executing |
| scan_time_s (9 imgs) | 0.945 | process_image only, excl. cold start |
| cold_start_s | 2.10 | one-time TF + model load |
| ram_peak_mb | 993 | resident TensorFlow (vs 178 db-synthetic) |
| faces_detected | 10 | **false positives** — fixtures are 64×64 solid colors, no real faces |
| model_name | ArcFace | embedding model |
| detector_backend | **opencv** | see RetinaFace issue below |

**Known issue — detector degraded to OpenCV.** On the pinned stack
(TensorFlow 2.21 / Keras 3), RetinaFace fails to build (`KerasTensor cannot be
used as input to a TensorFlow function`, `tf.shape` in `retinaface_model.py`).
The probe falls back to OpenCV Haar cascades — lower recall than RetinaFace.
**All live-ai numbers above reflect OpenCV, not production-intended RetinaFace.**
Tracked separately (see docs/AUDIT_LOG.md / TECH_DEBT). live-ai measures inference
COST, not detection QUALITY — fixtures contain no real faces.

## Phase 1 reference (synthetic DB mode)

| Dataset | Metric | Notes |
| --- | --- | --- |
| 1k | batch_insert_seconds (2k faces) | batch_size=100 |
| 1k | gallery_query_p95_ms | 200 samples, limit=100 |

Record hardware in JSON output (`platform` field). Compare before/after optimizations in Phase 2
(FAISS, batch detection) using the same script and dataset flag.

## Phase 2 targets

| Change | Target |
| --- | --- |
| Batch detection (2.1.1) | ≥2× ingest throughput vs per-image loop on 1k synthetic/stub dataset |
| FAISS (2.2.x) | Cluster identification p95 ↓ vs DBSCAN-only baseline |

## Modes
- **db-synthetic** (default): SQLite insert + gallery/cluster queries with fabricated
  embeddings. No DeepFace. CI-friendly, fast. `deepface_invoked=false`.
- **live-ai** (`--live-ai`): real DeepFace inference via `AICoreEngine.process_image`
  on fixture JPEGs. Requires TensorFlow/DeepFace + psutil (hard fail otherwise).
  Use `--live-ai-limit N` (default 200) to cap images. `deepface_invoked=true`.
  Current fixtures are solid-color squares — measures inference cost/RAM, not quality.
