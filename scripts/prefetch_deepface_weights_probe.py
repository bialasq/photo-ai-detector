"""Download DeepFace weights into vendor/deepface-weights when local copy is unavailable."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED_WEIGHTS = ("arcface_weights.h5", "retinaface.h5")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: prefetch_deepface_weights_probe.py <vendor/deepface-weights>", file=sys.stderr)
        return 2

    vendor_root = Path(sys.argv[1]).resolve()
    weights_dir = vendor_root / ".deepface" / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)

    os.environ["DEEPFACE_HOME"] = str(vendor_root)

    import keras_legacy_env  # noqa: F401 — before TensorFlow / DeepFace

    from ai_core import AICoreEngine

    engine = AICoreEngine()
    engine.ensure_detector_backend_ready()

    missing = [name for name in REQUIRED_WEIGHTS if not (weights_dir / name).is_file()]
    if missing:
        print(f"Probe finished but weights still missing: {missing}", file=sys.stderr)
        return 1

    for name in REQUIRED_WEIGHTS:
        size_mb = (weights_dir / name).stat().st_size / (1024 * 1024)
        print(f"OK {name} ({size_mb:.1f} MB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
