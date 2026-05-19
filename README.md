# Photo AI Detector

100% offline desktop photo organizer with face detection, embedding extraction, and incremental clustering.

## Stack

- **Python**: SQLite (`database.py`), DeepFace + DBSCAN (`ai_core.py`), FastAPI sidecar (`main.py`)
- **Desktop (planned)**: Tauri 2 + React + TypeScript + Tailwind CSS

## Quick start (backend)

```bash
pip install -r requirements.txt
python main.py
```

API: http://127.0.0.1:8000 — docs: http://127.0.0.1:8000/docs

## Project layout

| Module | Role |
|--------|------|
| `database.py` | SQLite persistence (photos, faces, people) |
| `ai_core.py` | Face detection, 512-d embeddings, DBSCAN clustering |
| `main.py` | FastAPI local API for Tauri sidecar |

## License

Private project — all rights reserved unless stated otherwise.
