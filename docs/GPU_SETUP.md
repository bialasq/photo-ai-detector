# GPU setup (optional)

The desktop app runs **CPU-only by default** for stability and PyInstaller compatibility.

Enable GPU inference only when you have a supported NVIDIA stack and accept the extra
operational complexity.

## Enable GPU mode

Set before starting the Python sidecar (Tauri release builds do **not** set this):

```powershell
$env:PHOTO_ORGANIZER_GPU = "1"
D:\photo-ai-detector\venv\Scripts\python.exe main.py
```

When `PHOTO_ORGANIZER_GPU=1` and TensorFlow sees no GPU, the sidecar logs a warning and
continues on CPU — it does **not** crash.

## Requirements (Windows)

1. **NVIDIA driver** — current Game Ready / Studio driver for your GPU.
2. **CUDA Toolkit** — version compatible with your pinned TensorFlow build.
   See [TensorFlow tested build configurations](https://www.tensorflow.org/install/source#tested_build_configurations).
3. **cuDNN** — matching the CUDA major version TensorFlow expects.
4. Project venv with `tensorflow` from `requirements.txt` (no separate `tensorflow-gpu`
   wheel on TF 2.x — GPU support is bundled in the main package when CUDA is present).

## Verify GPU visibility

```powershell
D:\photo-ai-detector\venv\Scripts\python.exe -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
```

Expected when working: a list containing at least one `PhysicalDevice(name='/physical_device:GPU:0', ...)`.

## Benchmark notes

GPU vs CPU throughput depends on batch size, model cold start, and driver power state.
Use `scripts/benchmark.py --live-ai` on a fixed folder after enabling the flag and record
hardware in `docs/BENCHMARKS.md`.

## Production default

v1.0.0 ships with **GPU off**. Opt-in only via environment variable.
