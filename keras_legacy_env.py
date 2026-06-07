"""
Force TensorFlow to use the legacy Keras backend before ``import tensorflow``.

RetinaFace (``retinaface_model.py``) calls ``tf.shape`` in ways that break under
Keras 3 / TF 2.21 default; the probe then silently falls back to OpenCV. With
``TF_USE_LEGACY_KERAS=1`` and ``tf-keras`` installed, RetinaFace ``build_model()``
works. This module must be imported (side effect only) before any code path loads
TensorFlow — including sidecar ``main.py``, ``scripts/benchmark.py --live-ai``,
and ``python ai_core.py``.

When running as a PyInstaller one-file sidecar, bundled DeepFace weights live under
``sys._MEIPASS/.deepface/weights/``. Set ``DEEPFACE_HOME`` before DeepFace import
so offline installs never hit the network.
"""

import os
import sys

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    os.environ.setdefault("DEEPFACE_HOME", sys._MEIPASS)
