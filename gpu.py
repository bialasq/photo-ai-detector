"""
Opt-in GPU configuration for TensorFlow / DeepFace (task 2.1.3).

Default is CPU-only. Set ``PHOTO_ORGANIZER_GPU=1`` to enable CUDA when available.
"""

from __future__ import annotations

import keras_legacy_env  # noqa: F401 — before tensorflow (TD-5)

import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)

GPU_ENV_VAR = "PHOTO_ORGANIZER_GPU"


def is_gpu_requested() -> bool:
    """Return True when the user opted in via ``PHOTO_ORGANIZER_GPU=1``."""
    return os.environ.get(GPU_ENV_VAR, "0").strip() == "1"


def detect_gpu() -> bool:
    """
    Return True when TensorFlow reports at least one physical GPU device.
    """
    try:
        import tensorflow as tf
    except ImportError:
        LOGGER.debug("TensorFlow not installed — GPU detection skipped")
        return False

    try:
        gpus = tf.config.list_physical_devices("GPU")
        return len(gpus) > 0
    except Exception as exc:  # noqa: BLE001 — TF runtime may be partially broken
        LOGGER.warning("GPU detection failed: %s", exc)
        return False


def configure_tensorflow_gpu(*, memory_growth: bool = True) -> bool:
    """
    Configure TensorFlow GPU memory growth when CUDA devices are visible.

    Returns:
        True when at least one GPU was configured, False otherwise.
    """
    try:
        import tensorflow as tf
    except ImportError:
        return False

    gpus = tf.config.list_physical_devices("GPU")
    if not gpus:
        return False

    for gpu in gpus:
        try:
            if memory_growth:
                tf.config.experimental.set_memory_growth(gpu, True)
        except RuntimeError as exc:
            LOGGER.warning("Could not set memory growth on %s: %s", gpu, exc)

    LOGGER.info("Configured %s TensorFlow GPU device(s)", len(gpus))
    return True


def initialize_gpu_if_requested() -> dict[str, Any]:
    """
    Apply GPU settings at sidecar startup based on ``PHOTO_ORGANIZER_GPU``.

    Never raises — missing GPU with flag set falls back to CPU with a warning.
    """
    if not is_gpu_requested():
        LOGGER.info("GPU disabled (default CPU mode)")
        return {"requested": False, "available": False, "configured": False}

    if not detect_gpu():
        LOGGER.warning(
            "%s=1 but no GPU detected — falling back to CPU",
            GPU_ENV_VAR,
        )
        return {"requested": True, "available": False, "configured": False}

    configured = configure_tensorflow_gpu()
    if configured:
        LOGGER.info("GPU enabled via %s=1", GPU_ENV_VAR)
    return {"requested": True, "available": True, "configured": configured}
