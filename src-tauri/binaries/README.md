# Sidecar binaries (`photo-ai-backend`)

Tauri bundles executables listed in `tauri.conf.json` → `bundle.externalBin`.

For `binaries/photo-ai-backend`, place **one file per target triple**:

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

## Production (PyInstaller)

```bash
npm run sidecar:package
```

This overwrites the dev launcher with a self-contained FastAPI binary.

## Development

If the triple-named file is missing, `src-tauri/build.rs` automatically builds a small Rust **launcher** (`backend-launcher/`) that runs `python main.py` and forwards logs to the Tauri console.

Do **not** run `python main.py` manually while using `npm run tauri:dev` — only one process should bind port **8000**.

The sidecar listens on `http://127.0.0.1:8000` (`PHOTO_ORGANIZER_HOST` / `PHOTO_ORGANIZER_PORT`).
