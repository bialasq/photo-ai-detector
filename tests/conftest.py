"""Pytest configuration and shared fixtures for ai_core tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from ai_core import l2_normalize
from database import DatabaseManager, EXPECTED_EMBEDDING_DIMENSION


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
