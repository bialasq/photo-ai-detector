"""Tests for gpu.py (task 2.1.3)."""

from __future__ import annotations

import gpu


def test_gpu_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(gpu.GPU_ENV_VAR, raising=False)
    status = gpu.initialize_gpu_if_requested()
    assert status["requested"] is False
    assert status["configured"] is False


def test_gpu_requested_without_device_falls_back(monkeypatch) -> None:
    monkeypatch.setenv(gpu.GPU_ENV_VAR, "1")
    monkeypatch.setattr(gpu, "detect_gpu", lambda: False)
    status = gpu.initialize_gpu_if_requested()
    assert status["requested"] is True
    assert status["available"] is False
    assert status["configured"] is False


def test_gpu_requested_with_device_configures(monkeypatch) -> None:
    monkeypatch.setenv(gpu.GPU_ENV_VAR, "1")
    monkeypatch.setattr(gpu, "detect_gpu", lambda: True)
    monkeypatch.setattr(gpu, "configure_tensorflow_gpu", lambda **_: True)
    status = gpu.initialize_gpu_if_requested()
    assert status == {"requested": True, "available": True, "configured": True}
