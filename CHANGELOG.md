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
