# Sidecar binaries (`photo-ai-backend`)

Registered in **`tauri.conf.json`** → `bundle.externalBin`:

```json
"externalBin": ["binaries/photo-ai-backend"]
```

Tauri copies the host-specific file into the app bundle and exposes it to Rust as `app.shell().sidecar("photo-ai-backend")`.

## Required filenames

Place **one executable per target triple** in this directory:

| Platform | Example filename |
|----------|------------------|
| Windows x64 | `photo-ai-backend-x86_64-pc-windows-msvc.exe` |
| macOS Apple Silicon | `photo-ai-backend-aarch64-apple-darwin` |
| macOS Intel | `photo-ai-backend-x86_64-apple-darwin` |
| Linux x64 | `photo-ai-backend-x86_64-unknown-linux-gnu` |

Discover your triple:

```bash
rustc --print host-tuple
```

## Build (PyInstaller)

From the repo root:

```bash
npm run sidecar:package
```

Output is copied to `photo-ai-backend-<triple>.exe` in this folder.

## Runtime behavior (`src-tauri/src/lib.rs`)

| Scenario | Backend |
|----------|---------|
| **Release / packaged app** | Auto-spawns bundled sidecar on startup; `kill()` on window close / app exit |
| **`PHOTO_ORGANIZER_EXTERNAL_BACKEND=1`** | No spawn (e.g. `run_app.bat` with separate uvicorn window) |
| **Dev without PyInstaller binary** | `build.rs` may build a Rust launcher stub, or Rust falls back to `venv\Scripts\python.exe main.py` |

The sidecar listens on `http://127.0.0.1:8000` (`PHOTO_ORGANIZER_HOST` / `PHOTO_ORGANIZER_PORT`). In release, the working directory is the app data folder so `organizer.db` is stored beside the installed app.
