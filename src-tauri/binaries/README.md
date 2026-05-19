# Sidecar binaries (`photo-ai-engine`)

Tauri bundles executables listed in `tauri.conf.json` → `bundle.externalBin`.

For `binaries/photo-ai-engine`, place **one file per target triple**:

| Platform | Example filename |
|----------|------------------|
| Windows x64 | `photo-ai-engine-x86_64-pc-windows-msvc.exe` |
| macOS Apple Silicon | `photo-ai-engine-aarch64-apple-darwin` |
| macOS Intel | `photo-ai-engine-x86_64-apple-darwin` |
| Linux x64 | `photo-ai-engine-x86_64-unknown-linux-gnu` |

Discover your triple:

```bash
rustc --print host-tuple
```

Build the binary:

```bash
npm run sidecar:package
```
