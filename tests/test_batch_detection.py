"""Task 2.1.1 — concurrent batch face detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_core import (
    DEFAULT_DETECTION_BATCH_SIZE,
    AICoreEngine,
)


def test_detect_faces_batch_returns_one_result_per_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [tmp_path / f"img_{index}.jpg" for index in range(5)]
    for path in paths:
        path.write_bytes(b"fake")

    engine = AICoreEngine.__new__(AICoreEngine)
    calls: list[str] = []

    def fake_process(image_path: str | Path) -> list[dict]:
        calls.append(str(image_path))
        return [{"embedding": [0.1], "bounding_box": {"x": 0, "y": 0, "w": 1, "h": 1}}]

    monkeypatch.setattr(engine, "process_image", fake_process)

    results = engine.detect_faces_batch(paths, batch_size=DEFAULT_DETECTION_BATCH_SIZE)

    assert len(results) == len(paths)
    assert len(calls) == len(paths)


def test_detect_faces_batch_with_retry_halves_batch_on_memory_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    engine = AICoreEngine.__new__(AICoreEngine)
    attempts: list[int] = []

    def flaky_batch(
        image_paths,
        *,
        batch_size: int = DEFAULT_DETECTION_BATCH_SIZE,
    ) -> list[list[dict]]:
        attempts.append(batch_size)
        if len(attempts) == 1:
            raise MemoryError("simulated OOM")
        return [[] for _ in image_paths]

    monkeypatch.setattr(engine, "detect_faces_batch", flaky_batch)

    results = engine.detect_faces_batch_with_retry(paths, batch_size=32)

    assert attempts == [32, 16]
    assert len(results) == len(paths)
