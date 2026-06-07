"""Pytest configuration and shared fixtures for ai_core tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from ai_core import l2_normalize
from database import DatabaseManager, EXPECTED_EMBEDDING_DIMENSION

# Matches production probe order (RetinaFace primary); safe to stub — no integration
# test asserts on detector_backend in HTTP responses (grep verified task 3.2.5).
STUB_DETECTOR_BACKEND: str = "retinaface"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: long-running performance benchmarks")
    config.addinivalue_line(
        "markers",
        "integration_slow: real sidecar subprocess startup (TensorFlow probe)",
    )


def stub_fastapi_test_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip heavy TF/DeepFace startup during TestClient lifespan (API tests only)."""
    import main

    monkeypatch.setattr(main, "verify_ai_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        main.AICoreEngine,
        "ensure_detector_backend_ready",
        lambda self: STUB_DETECTOR_BACKEND,
    )


@pytest.fixture(autouse=True)
def isolated_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Writable AppData root for every test (mirrors CI PHOTO_ORGANIZER_APP_DATA)."""
    monkeypatch.setenv("PHOTO_ORGANIZER_APP_DATA", str(tmp_path / "pytest-app-data"))


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """TestClient with isolated DB and without TensorFlow / detector probe at startup."""
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PHOTO_ORGANIZER_DB_PATH", str(tmp_path / "test.db"))
    stub_fastapi_test_startup(monkeypatch)
    application = main.create_application(dev_mode=False)
    with TestClient(application) as client:
        yield client


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Empty SQLite database with schema applied."""
    db_path = tmp_path / "test_organizer.db"
    manager = DatabaseManager(str(db_path))
    manager.create_tables()
    return manager


@pytest.fixture
def sample_embedding() -> list[float]:
    """Valid 512-d unit embedding for insert_face."""
    vector = [0.0] * EXPECTED_EMBEDDING_DIMENSION
    vector[0] = 1.0
    return l2_normalize(vector)


@pytest.fixture
def sample_detection(sample_embedding: list[float]) -> dict:
    """JSON-ready detection dict matching ``process_image`` output."""
    return {
        "embedding": sample_embedding,
        "bounding_box": {"x": 0, "y": 0, "w": 48, "h": 48},
        "detector_backend": "opencv",
        "model_name": "ArcFace",
        "confidence": 0.99,
    }


@pytest.fixture
def jpeg_file_factory(tmp_path: Path):
    """Create a minimal on-disk JPEG for path validation."""

    def _create(name: str, *, size: tuple[int, int] = (120, 120)) -> Path:
        path = tmp_path / name
        Image.new("RGB", size, color=(128, 128, 128)).save(path, format="JPEG")
        return path

    return _create
