# Photo AI Detector / Photo Organizer

**100% offline desktop photo organizer** with face detection, 512-dimensional embeddings, incremental DBSCAN clustering, and a Tauri 2 + React UI. All inference and storage run on your machine — no cloud APIs, no accounts.

| Property | Value |
|----------|--------|
| **Backend** | Python 3.12, FastAPI, SQLite, DeepFace, TensorFlow, scikit-learn |
| **Desktop shell** | Tauri 2, React 18, TypeScript, Vite, Tailwind CSS |
| **Default API** | `http://127.0.0.1:8000` (loopback only) |
| **Database** | `organizer.db` (created locally, not committed) |

---

## Table of contents (English)

1. [Product overview](#product-overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [End-to-end development lifecycle](#end-to-end-development-lifecycle)
5. [Repository layout](#repository-layout)
6. [Prerequisites](#prerequisites)
7. [Python environment (3.12)](#python-environment-312)
8. [Frontend & Tauri setup](#frontend--tauri-setup)
9. [Running the application](#running-the-application)
10. [HTTP API reference](#http-api-reference)
11. [Release build & sidecar packaging](#release-build--sidecar-packaging)
12. [Environment variables](#environment-variables)
13. [Data, privacy & `.gitignore`](#data-privacy--gitignore)
14. [Troubleshooting](#troubleshooting)

---

## Product overview

Photo Organizer ingests folders of JPG/PNG images on disk, detects faces, stores ArcFace embeddings in SQLite, groups unknown faces with DBSCAN, and lets you assign display names to clusters or individual “noise” faces. The gallery supports filtering by named people and AI processing status.

The project is a **monorepo at the repository root**: Python modules (`main.py`, `ai_core.py`, `database.py`) live beside the Vite/React app (`src/`) and the Tauri crate (`src-tauri/`). There is no separate `backend/` or `frontend/` folder.

---

## Features

- **Folder scan** — recursive ingestion with background progress (`phase`: `idle` → `scanning` → `clustering`).
- **Face pipeline** — DeepFace (RetinaFace / OpenCV detectors, ArcFace embeddings), cosine similarity thresholds, incremental DBSCAN.
- **People Profiles** — unnamed clusters, noise faces (`cluster_id` NULL or negative), merge duplicate people, assign names via `POST /api/clusters/identify`.
- **Gallery** — grid with person filters (intersection), AI status filter (`all` / `processed` / `unprocessed`).
- **Search** — comma-separated person names (AND / intersection).
- **Thumbnails** — on-demand resized JPEGs for photos, clusters, people, and individual faces.
- **Dev helpers** — optional simulate-scan and reset-library endpoints for local testing.

---

## Architecture

```mermaid
flowchart TB
  subgraph desktop["Tauri 2 desktop (src-tauri + src/)"]
    UI["React UI\nGallery · People Profiles"]
    Tauri["lib.rs lifecycle\nspawn / kill backend"]
  end

  subgraph backend["Python FastAPI (main.py)"]
    API["REST /api/*\n127.0.0.1:8000"]
    AI["ai_core.py\nDeepFace · DBSCAN"]
    DB["database.py\nSQLite organizer.db"]
  end

  UI -->|"fetch()"| API
  Tauri -->|"sidecar or external uvicorn"| API
  API --> AI
  AI --> DB
  API --> DB
```

**Process models**

| Mode | How the backend starts |
|------|-------------------------|
| **`run_app.bat` (recommended dev)** | Separate `cmd` window: venv + `uvicorn main:app`. Tauri gets `PHOTO_ORGANIZER_EXTERNAL_BACKEND=1` so it does not spawn a second server. |
| **`npm run tauri:dev` alone** | Tauri spawns `photo-ai-backend` sidecar (PyInstaller binary) or, in debug, falls back to `python main.py` if the binary is missing. |
| **`python main.py`** | Direct Uvicorn entrypoint for backend-only debugging. |

---

## End-to-end development lifecycle

This section documents how the application was built — useful for onboarding and future contributors.

### Phase 1 — Persistence & domain model (`database.py`)

- Designed SQLite schema: `photos`, `faces`, `people`, embeddings as BLOBs, `cluster_id` / `person_id` linkage.
- Implemented `DatabaseManager` with validation, migrations-style helpers, gallery/search queries, noise-face queries, and merge semantics.
- Local DB path: **`organizer.db`** at project root (gitignored).

### Phase 2 — Offline AI core (`ai_core.py`)

- Integrated **DeepFace** for detection + 512-d ArcFace embeddings.
- Implemented cosine-distance matching, boundary queue, and **DBSCAN** incremental clustering (scikit-learn).
- Noise faces persist with `cluster_id` NULL (not a dedicated “-1” row convention in storage).
- Hardened image I/O for Windows paths (including non-ASCII filenames).

### Phase 3 — Local HTTP API (`main.py`)

- FastAPI app with `/health` and `/api/*` routes.
- Background asyncio scan tasks with thread-safe `ScanProgressState` (`phase`, `current_file`, `last_error`).
- Thumbnail generation (Pillow) and static file streaming for full-resolution viewing.
- Dev-only routes: `POST /api/dev/reset-library`, `POST /api/dev/simulate-scan`.

### Phase 4 — Desktop shell (Tauri 2 + React)

- Vite + React + TypeScript + Tailwind UI (`src/`).
- `AppContext` — health polling, scan overlay (500 ms poll), `dataRefreshToken` for gallery/people refresh.
- Tauri plugins: `dialog` (folder picker), `shell` (sidecar spawn).
- CSP allows `connect-src` to `http://127.0.0.1:8000` only.

### Phase 5 — Sidecar packaging & dev launcher

- `scripts/package-sidecar.ps1` — PyInstaller → `src-tauri/binaries/photo-ai-backend-<triple>.exe`.
- `src-tauri/backend-launcher/` — Rust stub built by `build.rs` when the PyInstaller binary is absent (satisfies Tauri `externalBin` at compile time).
- `PHOTO_ORGANIZER_EXTERNAL_BACKEND` — skip duplicate backend when using `run_app.bat`.

### Phase 6 — Environment hardening (Python 3.12)

- **TensorFlow** and **DeepFace** do not support Python 3.14 on Windows; the project standardizes on **Python 3.12.10** via `py -3.12 -m venv venv`.
- `PYTHONNOUSERSITE=1` in `run_app.bat` prevents accidental imports from the user site-packages directory under `%AppData%`.
- Use **PowerShell** (or `cmd`) for `pip` — avoid Git Bash `pip`, which may target the wrong interpreter.

---

## Repository layout

| Path | Role |
|------|------|
| `main.py` | FastAPI application, route registration, scan orchestration |
| `ai_core.py` | Face detection, embeddings, DBSCAN clustering engine |
| `database.py` | SQLite access layer |
| `requirements.txt` | Python dependencies (TensorFlow, DeepFace, FastAPI, …) |
| `run_app.bat` | One-click dev: backend window + `npm run tauri:dev` |
| `src/` | React UI (`components/`, `context/`, `services/api.ts`) |
| `src-tauri/` | Tauri 2 Rust project, `tauri.conf.json`, capabilities |
| `src-tauri/backend-launcher/` | Dev-only sidecar stub (Rust) |
| `scripts/package-sidecar.ps1` | PyInstaller packaging for release sidecar |
| `reset_db.py` | Local utility to wipe ingestion tables (uses local DB) |
| `check_db.py` | Local SQLite inspection script (dev) |

---

## Prerequisites

| Tool | Notes |
|------|--------|
| **Python 3.12** | `py -3.12 --version` — required for TensorFlow wheels on Windows |
| **Node.js 18+** | For Vite and Tauri CLI |
| **Rust (rustup)** | For `tauri dev` / `tauri build` |
| **Microsoft Visual C++ Redistributable** | Often required by TensorFlow native DLLs on Windows |

Optional: **PyInstaller** (installed automatically by `npm run sidecar:package`).

---

## Python environment (3.12)

From the repository root in **PowerShell**:

```powershell
cd C:\path\to\photo-ai-detector

# 1. Create venv with the 3.12 launcher (not plain python if it points to 3.14)
py -3.12 -m venv venv

# 2. Activate
.\venv\Scripts\Activate.ps1

# 3. Block global user-site packages
$env:PYTHONNOUSERSITE = "1"

# 4. Upgrade installer tooling
python -m pip install --upgrade pip setuptools wheel

# 5. Install project dependencies (no cache = clean wheels)
python -m pip install --no-cache-dir --no-user -r requirements.txt

# 6. Verify AI stack
python -c "import numpy; import PIL; import tensorflow; import deepface; print('FULL AI STACK OK')"
```

**Important:** Do not use Python 3.14 for this project. `pip` will not find TensorFlow wheels, and mixed `cp312` / `cp314` binaries in `site-packages` cause import failures.

---

## Frontend & Tauri setup

```powershell
npm install
```

Type-check and build the web assets:

```powershell
npm run build
```

---

## Running the application

### Recommended — `run_app.bat`

Double-click or run from `cmd`:

```bat
run_app.bat
```

Steps performed:

1. Validates `venv\Scripts\activate.bat`
2. Opens **Photo Organizer - Backend** with `PYTHONNOUSERSITE=1` and `uvicorn main:app --host 127.0.0.1 --port 8000`
3. Waits 5 seconds for model load
4. Sets `PHOTO_ORGANIZER_EXTERNAL_BACKEND=1` and runs `npm run tauri:dev`
5. On exit, kills the backend window and any process on port 8000

### Backend only

```powershell
.\venv\Scripts\Activate.ps1
$env:PYTHONNOUSERSITE = "1"
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info
```

OpenAPI docs: **http://127.0.0.1:8000/docs**

### Desktop without `run_app.bat`

```powershell
npm run tauri:dev
```

---

## HTTP API reference

Base URL: **`http://127.0.0.1:8000`**. The React client is implemented in `src/services/api.ts`.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness (`{ "status": "ok" }`) |
| `POST` | `/api/scan-folder` | Start background folder ingestion |
| `GET` | `/api/scan-status` | Poll `processed`, `total`, `is_active`, `phase`, `current_file`, `last_error` |
| `GET` | `/api/gallery` | Gallery list (`person_ids`, `ai_status`) |
| `GET` | `/api/search` | Intersection search by comma-separated names |
| `GET` | `/api/people` | Named people summaries |
| `POST` | `/api/people/merge` | Merge source person into target |
| `GET` | `/api/clusters/unnamed` | Unnamed cluster IDs |
| `GET` | `/api/clusters/noise` | Noise / unclustered faces |
| `POST` | `/api/clusters/identify` | Name cluster or assign noise face (new or existing person) |
| `GET` | `/api/photos/{id}/thumbnail` | Resized photo thumbnail |
| `GET` | `/api/photos/{id}/file` | Full image bytes |
| `GET` | `/api/clusters/{id}/thumbnail` | Cluster representative thumbnail |
| `GET` | `/api/faces/{id}/thumbnail` | Single face crop thumbnail |
| `GET` | `/api/people/{id}/thumbnail` | Person avatar thumbnail |
| `POST` | `/api/dev/reset-library` | Clear ingestion data (dev) |
| `POST` | `/api/dev/simulate-scan` | Scan a test folder (dev) |

**Naming rule:** There is no `/api/people/name` route. Assigning display names always goes through **`POST /api/clusters/identify`** with `{ cluster_id, name }` or `{ face_id, name | person_id }`.

---

## Release build & sidecar packaging

```powershell
.\venv\Scripts\Activate.ps1
$env:PYTHONNOUSERSITE = "1"
npm run sidecar:package   # PyInstaller → src-tauri/binaries/photo-ai-backend-<triple>.exe
npm run tauri:build
```

Packaged binaries matching `photo-ai-backend-*` are **gitignored**; only sources and `src-tauri/binaries/README.md` are tracked.

---

## Environment variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `PYTHONNOUSERSITE=1` | Python | Ignore user site-packages (set in `run_app.bat`) |
| `PHOTO_ORGANIZER_EXTERNAL_BACKEND=1` | Tauri | Do not spawn sidecar; use existing server |
| `PHOTO_ORGANIZER_HOST` | `main.py` / sidecar | Bind host (default `127.0.0.1`) |
| `PHOTO_ORGANIZER_PORT` | `main.py` / sidecar | Bind port (default `8000`) |
| `PHOTO_AI_PROJECT_ROOT` | Sidecar | Project root for `python main.py` fallback |
| `PHOTO_AI_PYTHON` | `package-sidecar.ps1` | Override Python executable for PyInstaller |
| `TF_ENABLE_ONEDNN_OPTS=0` | TensorFlow | Optional: disable oneDNN info messages |

---

## Data, privacy & `.gitignore`

The following are **never committed** (see `.gitignore`):

- `venv/`, `node_modules/`, `src-tauri/target/`
- `organizer.db`, `*.db`, `data/`
- `.env`, `.env.*`
- `.thumbnail_cache/`
- PyInstaller outputs: `dist-sidecar/`, `build-sidecar/`, `src-tauri/binaries/photo-ai-backend-*`
- Logs and IDE folders

Your photo library paths and face embeddings remain on disk only under your local database file.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| `No matching distribution found for tensorflow` | Python 3.14+ | Recreate venv with `py -3.12 -m venv venv` |
| `DLL load failed` / wrong `.pyd` tag (`cp312` vs `cp314`) | Mixed interpreters or user-site pollution | Delete `venv`, reinstall with `PYTHONNOUSERSITE=1`, use PowerShell `python -m pip` |
| Port 8000 already in use | Stale backend | Close “Photo Organizer - Backend” or run `run_app.bat` cleanup |
| Tauri window blank / API errors | Backend not ready | Wait for `/health`, check backend console |
| `pip` installs to AppData | Git Bash or wrong `python` | Activate venv; use `python -m pip install --no-user` |

---

## License

Private project — all rights reserved unless stated otherwise.

---

---

# Photo AI Detector / Photo Organizer — dokumentacja po polsku

**Offline’owy organizer zdjęć na desktopie** z detekcją twarzy, embeddingami 512-wymiarowymi, klasteryzacją DBSCAN i interfejsem Tauri 2 + React. Całe przetwarzanie i baza danych działają lokalnie — bez chmury i kont.

| Właściwość | Wartość |
|------------|---------|
| **Backend** | Python 3.12, FastAPI, SQLite, DeepFace, TensorFlow, scikit-learn |
| **Aplikacja desktop** | Tauri 2, React 18, TypeScript, Vite, Tailwind CSS |
| **Domyślne API** | `http://127.0.0.1:8000` (tylko loopback) |
| **Baza** | `organizer.db` (tworzona lokalnie, nie trafia do Gita) |

---

## Spis treści (polski)

1. [Opis produktu](#opis-produktu)
2. [Funkcje](#funkcje)
3. [Architektura](#architektura-1)
4. [Cykl tworzenia aplikacji](#cykl-tworzenia-aplikacji)
5. [Struktura repozytorium](#struktura-repozytorium)
6. [Wymagania](#wymagania)
7. [Środowisko Python 3.12](#środowisko-python-312)
8. [Frontend i Tauri](#frontend-i-tauri)
9. [Uruchamianie](#uruchamianie)
10. [API HTTP](#api-http)
11. [Build produkcyjny i sidecar](#build-produkcyjny-i-sidecar)
12. [Zmienne środowiskowe](#zmienne-środowiskowe)
13. [Dane, prywatność i `.gitignore`](#dane-prywatność-i-gitignore)
14. [Rozwiązywanie problemów](#rozwiązywanie-problemów)

---

## Opis produktu

Photo Organizer skanuje foldery ze zdjęciami JPG/PNG, wykrywa twarze, zapisuje embeddingi ArcFace w SQLite, grupuje nieznane twarze algorytmem DBSCAN i pozwala przypisać imiona do klastrów lub pojedynczych twarzy „szumu” (noise). Galeria obsługuje filtrowanie po osobach i statusie przetworzenia AI.

Projekt to **monorepo w katalogu głównym**: moduły Pythona (`main.py`, `ai_core.py`, `database.py`) leżą obok aplikacji Vite/React (`src/`) i crate’a Tauri (`src-tauri/`).

---

## Funkcje

- **Skan folderów** — rekurencyjna ingesta z postępem w tle (`phase`: `idle` → `scanning` → `clustering`).
- **Pipeline twarzy** — DeepFace (RetinaFace / OpenCV, embeddingi ArcFace), progi cosinusowe, inkrementalny DBSCAN.
- **People Profiles** — bezimienne klastry, twarze noise, scalanie osób, nadawanie imion przez `POST /api/clusters/identify`.
- **Galeria** — siatka z filtrami osób (przecięcie AND) i filtrem AI (`all` / `processed` / `unprocessed`).
- **Wyszukiwanie** — lista imion rozdzielona przecinkami (przecięcie).
- **Miniatury** — skalowane JPEG dla zdjęć, klastrów, osób i pojedynczych twarzy.
- **Narzędzia dev** — opcjonalny simulate-scan i reset biblioteki.

---

## Architektura

Diagram i tryby uruchomienia — jak w sekcji angielskiej [Architecture](#architecture):

- **`run_app.bat`** — osobne okno uvicorn + Tauri z `PHOTO_ORGANIZER_EXTERNAL_BACKEND=1`.
- **`npm run tauri:dev`** — sidecar PyInstaller lub fallback `python main.py`.
- **`python main.py`** — sam backend do debugowania.

---

## Cykl tworzenia aplikacji

Krótki opis faz rozwoju projektu (onboarding):

### Faza 1 — Warstwa danych (`database.py`)

Schemat SQLite (`photos`, `faces`, `people`), embeddingi jako BLOB, zapytania galerii/wyszukiwania, twarze noise, scalanie osób. Plik **`organizer.db`** jest lokalny i gitignorowany.

### Faza 2 — Silnik AI (`ai_core.py`)

DeepFace, podobieństwo cosinusowe, kolejka graniczna, DBSCAN (scikit-learn). Twarze noise: `cluster_id` NULL w bazie. Obsługa ścieżek Windows (w tym znaków spoza ASCII).

### Faza 3 — API HTTP (`main.py`)

FastAPI, skan w tle, `ScanProgressState`, miniatury Pillow, endpointy dev.

### Faza 4 — UI desktop (Tauri 2 + React)

`AppContext`, overlay skanu, odświeżanie galerii i profili, plugin `dialog` (wybór folderu), CSP ograniczające połączenia do `127.0.0.1:8000`.

### Faza 5 — Sidecar i launcher

`scripts/package-sidecar.ps1`, `backend-launcher` w Rust (stub na czas dev), zmienna `PHOTO_ORGANIZER_EXTERNAL_BACKEND`.

### Faza 6 — Utwardzenie środowiska (Python 3.12)

TensorFlow i DeepFace **nie wspierają Pythona 3.14** na Windows. Standard projektu: **`py -3.12 -m venv venv`**, `PYTHONNOUSERSITE=1` w `run_app.bat`, instalacje `pip` z PowerShell (nie Git Bash).

---

## Struktura repozytorium

| Ścieżka | Rola |
|---------|------|
| `main.py` | Aplikacja FastAPI, trasy, orchestracja skanu |
| `ai_core.py` | Detekcja, embeddingi, DBSCAN |
| `database.py` | SQLite |
| `requirements.txt` | Zależności Pythona |
| `run_app.bat` | Dev jednym kliknięciem |
| `src/` | React UI |
| `src-tauri/` | Tauri 2, konfiguracja, uprawnienia |
| `scripts/package-sidecar.ps1` | Pakowanie sidecara PyInstaller |

---

## Wymagania

| Narzędzie | Uwagi |
|-----------|--------|
| **Python 3.12** | `py -3.12 --version` |
| **Node.js 18+** | Vite, Tauri CLI |
| **Rust (rustup)** | `tauri dev` / `tauri build` |
| **VC++ Redistributable** | Często wymagany przez TensorFlow na Windows |

---

## Środowisko Python 3.12

W **PowerShell** z katalogu projektu:

```powershell
cd C:\ścieżka\do\photo-ai-detector
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
$env:PYTHONNOUSERSITE = "1"
python -m pip install --upgrade pip setuptools wheel
python -m pip install --no-cache-dir --no-user -r requirements.txt
python -c "import numpy; import PIL; import tensorflow; import deepface; print('FULL AI STACK OK')"
```

**Nie używaj Pythona 3.14** — brak wheeli TensorFlow i ryzyko pomieszania rozszerzeń `cp312` / `cp314`.

---

## Frontend i Tauri

```powershell
npm install
npm run build    # opcjonalnie: weryfikacja TypeScript + Vite
```

---

## Uruchamianie

### Zalecane — `run_app.bat`

Uruchamia okno backendu z `PYTHONNOUSERSITE=1` i uvicorn, czeka 5 s, ustawia `PHOTO_ORGANIZER_EXTERNAL_BACKEND=1`, startuje `npm run tauri:dev`, przy zamknięciu czyści port 8000.

### Sam backend

```powershell
.\venv\Scripts\Activate.ps1
$env:PYTHONNOUSERSITE = "1"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Dokumentacja OpenAPI: **http://127.0.0.1:8000/docs**

### Samo Tauri

```powershell
npm run tauri:dev
```

---

## API HTTP

Bazowy URL: **`http://127.0.0.1:8000`**. Klient: `src/services/api.ts`.

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `GET` | `/health` | Sprawdzenie żywotności |
| `POST` | `/api/scan-folder` | Start skanu folderu |
| `GET` | `/api/scan-status` | Postęp skanu |
| `GET` | `/api/gallery` | Galeria (`person_ids`, `ai_status`) |
| `GET` | `/api/search` | Wyszukiwanie po imionach (AND) |
| `GET` | `/api/people` | Lista osób |
| `POST` | `/api/people/merge` | Scalanie profili |
| `GET` | `/api/clusters/unnamed` | Bezimienne klastry |
| `GET` | `/api/clusters/noise` | Twarze noise |
| `POST` | `/api/clusters/identify` | Nadanie imienia klastrowi / twarzy |
| `GET` | `/api/photos/{id}/thumbnail` | Miniatura zdjęcia |
| `GET` | `/api/photos/{id}/file` | Pełny plik |
| `GET` | `/api/clusters/{id}/thumbnail` | Miniatura klastra |
| `GET` | `/api/faces/{id}/thumbnail` | Miniatura twarzy |
| `GET` | `/api/people/{id}/thumbnail` | Awatar osoby |
| `POST` | `/api/dev/reset-library` | Reset danych (dev) |
| `POST` | `/api/dev/simulate-scan` | Testowy skan (dev) |

**Ważne:** Nie ma endpointu `/api/people/name` — nazwy zawsze przez **`POST /api/clusters/identify`**.

---

## Build produkcyjny i sidecar

```powershell
npm run sidecar:package
npm run tauri:build
```

Binaria `photo-ai-backend-*` są w `.gitignore`.

---

## Zmienne środowiskowe

| Zmienna | Opis |
|---------|------|
| `PYTHONNOUSERSITE=1` | Blokada pakietów z user site (AppData) |
| `PHOTO_ORGANIZER_EXTERNAL_BACKEND=1` | Tauri nie uruchamia drugiego backendu |
| `PHOTO_ORGANIZER_HOST` / `PORT` | Adres serwera API |
| `PHOTO_AI_PROJECT_ROOT` | Korzeń projektu dla fallbacku Pythona |
| `PHOTO_AI_PYTHON` | Interpreter do PyInstaller |

---

## Dane, prywatność i `.gitignore`

Do repozytorium **nie trafiają**: `venv/`, bazy `*.db`, `organizer.db`, `.env`, `.thumbnail_cache/`, zdjęcia użytkownika, binaria sidecara, `node_modules/`, artefakty build Rust.

Ścieżki do Twoich albumów i embeddingi twarzy pozostają wyłącznie na dysku lokalnym.

---

## Rozwiązywanie problemów

| Objaw | Przyczyna | Rozwiązanie |
|-------|-----------|-------------|
| Brak pakietu `tensorflow` | Python 3.14 | `py -3.12 -m venv venv` i czysta instalacja |
| Błąd DLL / zły tag `.pyd` | Pomieszane wersje Pythona | Usuń `venv`, `PYTHONNOUSERSITE=1`, `python -m pip` w PowerShell |
| Port 8000 zajęty | Stary proces | Zamknij okno backendu lub użyj cleanup z `run_app.bat` |
| Puste okno Tauri | Backend nie działa | Sprawdź `/health` i logi uvicorn |

---

## Licencja

Projekt prywatny — wszelkie prawa zastrzeżone, o ile nie określono inaczej.
