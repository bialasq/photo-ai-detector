"""Tests for FaceVectorStore (task 2.2.1 dependency for 2.2.2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("faiss")

from ai_core import l2_normalize
from database import DatabaseManager
from vector_store import FaceVectorStore


def _vector(seed: int) -> np.ndarray:
    raw = [0.0] * 512
    raw[seed % 512] = 1.0
    return np.asarray(l2_normalize(raw), dtype=np.float32)


def test_vector_store_add_and_search() -> None:
    store = FaceVectorStore()
    store.add(1, _vector(1))
    store.add(2, _vector(2))

    results = store.search(_vector(1), k=2)
    assert results[0][0] == 1
    assert results[0][1] == pytest.approx(1.0, abs=1e-4)


def test_vector_store_persistence(tmp_path: Path) -> None:
    index_path = tmp_path / "faiss.index"
    store = FaceVectorStore()
    store.add(10, _vector(3))
    store.save(index_path)

    loaded = FaceVectorStore()
    loaded.load(index_path)
    results = loaded.search(_vector(3), k=1)
    assert results == [(10, pytest.approx(1.0, abs=1e-4))]


def test_vector_store_rebuild_from_database(tmp_path: Path) -> None:
    db_path = tmp_path / "vectors.db"
    manager = DatabaseManager(str(db_path))
    manager.create_tables()

    photo_id = manager.insert_photo("/bench/a.jpg")
    face_id = manager.insert_face(
        photo_id=photo_id,
        embedding=l2_normalize([1.0] + [0.0] * 511),
        bounding_box={"x": 0, "y": 0, "w": 10, "h": 10},
    )

    store = FaceVectorStore.rebuild_from_database(manager)
    results = store.search(_vector(0), k=1)
    assert results[0][0] == face_id
