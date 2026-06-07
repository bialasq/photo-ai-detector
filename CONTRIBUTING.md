# Contributing — Photo AI Detector

Thank you for improving the offline photo organizer. This repo is a **monorepo at the root** — Python sidecar modules (`main.py`, `ai_core.py`, `database.py`, …) sit next to `src/` (React) and `src-tauri/` (Tauri 2). There is no `backend/` or `frontend/` subdirectory.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|--------|
| **Windows** | 10/11 x64 | Primary target for v1.0 |
| **Python** | 3.12 | `py -3.12 --version` — TensorFlow wheels on Windows |
| **Node.js** | 20 | Matches CI |
| **Rust** | stable via rustup | `tauri dev` / `tauri build` |
| **Git** | recent | PRs target `main` |

Inference is **CPU-only by default** on Windows (TensorFlow). Scans on large folders can take minutes — expected behavior.

---

## Development setup

From the repository root in **PowerShell**:

```powershell
cd C:\path\to\photo-ai-detector

py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
$env:PYTHONNOUSERSITE = "1"

python -m pip install --upgrade pip setuptools wheel
python -m pip install --no-cache-dir --no-user -r requirements.txt -r requirements-dev.txt

npm ci
```

First clone without a lockfile sync: use `npm install` instead of `npm ci`.

### Run the app

**Recommended:** `run_app.bat` — backend window (uvicorn) + `npm run tauri:dev`.

**Backend only:**

```powershell
.\venv\Scripts\Activate.ps1
$env:PYTHONNOUSERSITE = "1"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**Desktop only:** `npm run tauri:dev` (spawns sidecar or uses external backend if `PHOTO_ORGANIZER_EXTERNAL_BACKEND=1`).

Local data (DB, thumbnails, logs): `%AppData%\com.photo.organizer\`. Override with `PHOTO_ORGANIZER_APP_DATA` or `PHOTO_ORGANIZER_DB_PATH` for tests.

---

## Running tests

With venv activated:

```powershell
venv\Scripts\python.exe -m pytest -m "not slow" -q
npm test
```

Optional:

```powershell
npm run build
venv\Scripts\python.exe -m pytest -m "not slow and not integration_slow" -q
```

The `integration_slow` marker runs a real sidecar subprocess + TensorFlow startup (slower; used in CI on Ubuntu).

---

## Repository layout (root)

| Path | Role |
|------|------|
| `main.py` | FastAPI app, routes, scan orchestration, `ThumbnailEngine`, middleware |
| `ai_core.py` | DeepFace detection, embeddings, DBSCAN |
| `database.py` | SQLite, migrations, queries |
| `log_privacy.py` | Path hashing for logs |
| `path_validation.py` | Scan path hardening |
| `routes/v1.py` | Versioned `/api/v1/*` routes |
| `schemas.py` | Pydantic request/response models |
| `src/` | React + TypeScript UI |
| `src-tauri/` | Tauri 2 Rust shell, `tauri.conf.json` (CSP) |
| `tests/` | pytest (unit + `tests/integration/`) |
| `docs/` | Architecture, security audit, benchmarks — see [README.md](README.md) |

---

## Branch & CI workflow

1. Branch from `main` (e.g. `feat/3.x-description`).
2. Make focused changes; avoid unrelated refactors.
3. Run local tests (commands above).
4. Open a PR to `main`. CI (`.github/workflows/ci.yml`) runs:

| Job | What it does |
|-----|----------------|
| **python-tests** | Ubuntu, Python 3.12, `pip install -r requirements.txt -r requirements-dev.txt`, `pytest -m "not slow and not integration_slow"`, then `integration_slow` |
| **frontend-build** | Node 20, `npm ci`, `npm run build` |
| **lint** | warning-only: TypeScript check, Vitest |
| **security-audit** | warning-only: `pip-audit`, `npm audit` |
| **rust** | Windows, `cargo clippy` (Tauri crate) |

PRs should keep **python-tests** and **frontend-build** green.

---

## Documentation pointers

- [README.md](README.md) — quick start, API summary, env vars
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — diagrams, scan flow, AppData layout
- [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) — OWASP L1 self-audit
- [docs/API_ERROR_CODES.md](docs/API_ERROR_CODES.md) — error contract
- [docs/API_VERSIONING.md](docs/API_VERSIONING.md) — `/api/v1/*` policy

Do not duplicate full API or benchmark tables in README — link to these files instead.
