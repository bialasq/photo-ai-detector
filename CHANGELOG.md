# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Production-ready roadmap: `docs/PRODUCTION_ROADMAP.md` (12-week plan, phases 0–4, release gate) and `docs/ARCHITECTURE.md` (target architecture diagrams).
- Cursor task prompt plan: `tasks/` — 63 task specs with acceptance criteria and ready-to-paste Agent prompts for the production-ready delivery track.
- `.cursorrules` — Cursor operating contract (anti-hallucination, workflow, pre-PR checklist).
- GitHub issue import scripts: `scripts/import_issues.sh`, `scripts/setup_labels.sh`.
- `docs/PRODUCT_SCOPE.md` — MVP v1.0 scope freeze: must-have user stories, explicit out-of-scope list, Windows 10/11 x64-only platform decision, uninstall / `%AppData%` data retention policy (task 0.1).
- **Security (task 1.2.1):** `/api/dev/*` routes registered only when `PHOTO_ORGANIZER_DEV=1`. Tauri debug builds pass the variable to the sidecar; release builds do not.
- **Observability (task 1.1.2):** Structured JSON logging with rotating files in `%AppData%\com.photo.organizer\logs\` (`logging_config.py`). Scan lifecycle events (`scan.start`, `scan.file.done`, `scan.complete`). User paths hashed at INFO level.
- **API errors (task 1.1.3):** Unified `ErrorResponse` JSON (`error`, `code`, `hint`, `details`) via `errors.py` + global handlers. Frontend `src/api/errors.ts`. Documented in `docs/API_ERROR_CODES.md`.
- **Data (task 1.2.2):** SQLite database resolved via `get_db_path()` — production default `%AppData%\com.photo.organizer\organizer.db` (macOS/Linux under platform app-support dirs). Override with `PHOTO_ORGANIZER_DB_PATH`. Tauri injects the path when spawning the sidecar. One-time copy from legacy CWD/repo `organizer.db` when AppData is empty (original preserved).
- **Security (task 1.2.4):** Swagger (`/docs`), ReDoc (`/redoc`), and `/openapi.json` disabled in release; available only when `PHOTO_ORGANIZER_DEV=1`. Support bundle schema via `scripts/export_openapi.py` → `docs/api/openapi.json`.
- **Security (task 1.2.5):** Sidecar refuses non-loopback bind hosts; startup logs `Listening on 127.0.0.1:<port> (loopback-only)`.
- **Database (task 1.3.2):** Numbered SQL migrations in `migrations/` with `schema_migrations` tracking, pre-migrate backup (`organizer.db.bak.{version}`), rollback on failure, dry-run via `PHOTO_ORGANIZER_MIGRATE_DRY_RUN=1`.
- **Performance (task 1.3.1):** Gallery/cluster indexes in `migrations/003_indexes.sql` (EXPLAIN-verified). Documented in `docs/DB_INDEXES.md`.
- **Performance (task 1.3.3):** Batched face inserts (`insert_faces_batch`, `FaceInsertBuffer`) — 100 rows per SQLite transaction during folder scans; ≥5× faster than per-row inserts in benchmark.
- **Database (task 1.3.4):** `require_database_integrity()` at sidecar startup; FK enforcement verified per connection.
- **API (task 1.4.1):** `/api/v1/health` router, `src/api/client.ts`, `docs/API_VERSIONING.md` (legacy `/api/*` unchanged).
- **API (task 1.4.2):** Strict POST request schemas in `schemas.py` with field constraints; 422 validation handler sanitizes error context.
- **Performance (task 2.0.2):** `scripts/benchmark.py` + `docs/BENCHMARKS.md` for reproducible DB/gallery benchmarks.
- **ML (task 2.1.1):** `detect_faces_batch` via ThreadPoolExecutor (32 images, OOM retry); scan loop processes detection batches before DB ingest.
- **Security (task 1.2.3):** `path_validation.validate_scan_path` — rejects `..`, symlink escapes, missing paths, files, and whole-disk roots unless `X-Confirm-Whole-Disk: 1`. Errors expose `path_hash` only.
- **Stability (task 1.1.4):** React `ErrorBoundary` (app / gallery / people scopes), `POST /api/v1/log-error`, Vitest coverage.
- **UX (task 1.4.3):** `POST /api/v1/scan-cancel`, cooperative cancellation between detection batches, Stop button in scan overlay.
- **Release (task 0.2):** `docs/RELEASE.md` — Authenticode procurement checklist and MSI signing steps.
- **Performance (task 2.0.1):** Extended `scripts/benchmark.py` — 1k/5k/10k datasets, RAM peak, FAISS identify latency, silhouette; fixture generator under `tests/fixtures/benchmarks/`.
- **ML (task 2.1.3):** Opt-in GPU via `PHOTO_ORGANIZER_GPU=1` (`gpu.py`, `docs/GPU_SETUP.md`).
- **ML (task 2.2.2):** `incremental_assign()` + `vector_store.py` (FAISS IndexFlatIP, rebuild/save after scan).
- **ML (task 2.2.3):** Cluster quality metrics (`cluster_health` table, `GET /api/v1/clusters/health`).
- **Frontend (tasks 2.3.1 / 2.3.2):** `react-window` gallery virtualization (>200 photos), `LazyThumbnail` with IntersectionObserver.
