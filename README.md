# Photo AI Detector

100% offline desktop photo organizer with face detection, embedding extraction, and incremental clustering.

## Stack

- **Python**: SQLite (`database.py`), DeepFace + DBSCAN (`ai_core.py`), FastAPI sidecar (`main.py`)
- **Desktop**: Tauri 2 + React + TypeScript + Tailwind CSS (`src/`, `src-tauri/`)

## Quick start (backend only)

```bash
pip install -r requirements.txt
python main.py
```

API: http://127.0.0.1:8000 — docs: http://127.0.0.1:8000/docs

## Quick start (desktop)

Prerequisites: [Node.js](https://nodejs.org/), [Rust](https://rustup.rs/), Python venv with `requirements.txt`.

```bash
npm install
npm run tauri:dev
```

In **debug**, if the PyInstaller sidecar is missing, Rust falls back to `python main.py` automatically. For release builds:

```bash
npm run sidecar:package
npm run tauri:build
```

## Project layout

| Module | Role |
|--------|------|
| `database.py` | SQLite persistence (photos, faces, people) |
| `ai_core.py` | Face detection, 512-d embeddings, DBSCAN clustering |
| `main.py` | FastAPI local API for Tauri sidecar |
| `src-tauri/` | Tauri 2 shell — spawns/kills `photo-ai-engine` sidecar |
| `scripts/package-sidecar.ps1` | PyInstaller → `src-tauri/binaries/photo-ai-engine-<triple>` |

## License

Private project — all rights reserved unless stated otherwise.
