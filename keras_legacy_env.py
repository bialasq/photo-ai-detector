"""
Force TensorFlow to use the legacy Keras backend before ``import tensorflow``.

RetinaFace (``retinaface_model.py``) calls ``tf.shape`` in ways that break under
Keras 3 / TF 2.21 default; the probe then silently falls back to OpenCV. With
``TF_USE_LEGACY_KERAS=1`` and ``tf-keras`` installed, RetinaFace ``build_model()``
works. This module must be imported (side effect only) before any code path loads
TensorFlow — including sidecar ``main.py``, ``scripts/benchmark.py --live-ai``,
and ``python ai_core.py``.
"""

import os

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
