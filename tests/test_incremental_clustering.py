"""Tests for incremental_assign (task 2.2.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from ai_core import incremental_assign, l2_normalize
from database import DatabaseManager


@dataclass
class _FakeFace:
    id: int
    cluster_id: int | None


class FakeVectorStore:
    def __init__(self, candidates: list[tuple[int, float]]) -> None:
        self._candidates = candidates

    def search(self, _embedding: Any, *, k: int = 10) -> list[tuple[int, float]]:
        return self._candidates[:k]


class FakeDatabase:
    def __init__(self, faces: dict[int, _FakeFace]) -> None:
        self._faces = faces

    def get_face_by_id(self, face_id: int) -> _FakeFace | None:
        return self._faces.get(face_id)


def _unit_vector(index: int) -> list[float]:
    vector = [0.0] * 512
    vector[index] = 1.0
    return l2_normalize(vector)


def test_incremental_assign_clear_match() -> None:
    store = FakeVectorStore([(1, 0.91), (2, 0.88)])
    database = FakeDatabase(
        {
            1: _FakeFace(id=1, cluster_id=7),
            2: _FakeFace(id=2, cluster_id=7),
        }
    )
    result = incremental_assign(_unit_vector(0), vector_store=store, database=database)
    assert result == 7


def test_incremental_assign_mixed_clusters_returns_none() -> None:
    store = FakeVectorStore([(1, 0.92), (2, 0.9)])
    database = FakeDatabase(
        {
            1: _FakeFace(id=1, cluster_id=3),
            2: _FakeFace(id=2, cluster_id=4),
        }
    )
    result = incremental_assign(_unit_vector(1), vector_store=store, database=database)
    assert result is None


def test_incremental_assign_below_threshold_returns_none() -> None:
    store = FakeVectorStore([(1, 0.55)])
    database = FakeDatabase({1: _FakeFace(id=1, cluster_id=2)})
    result = incremental_assign(
        _unit_vector(2),
        vector_store=store,
        database=database,
        threshold=0.7,
    )
    assert result is None


def test_incremental_assign_named_clusters_only() -> None:
    store = FakeVectorStore([(1, 0.95), (2, 0.93)])
    database = FakeDatabase(
        {
            1: _FakeFace(id=1, cluster_id=None),
            2: _FakeFace(id=2, cluster_id=5),
        }
    )
    result = incremental_assign(_unit_vector(3), vector_store=store, database=database)
    assert result == 5


def test_incremental_assign_no_candidates_returns_none() -> None:
    store = FakeVectorStore([])
    database = FakeDatabase({})
    result = incremental_assign(_unit_vector(4), vector_store=store, database=database)
    assert result is None


def test_incremental_assign_threshold_env(monkeypatch) -> None:
    monkeypatch.setenv("PHOTO_ORGANIZER_INCREMENTAL_THRESHOLD", "0.95")
    store = FakeVectorStore([(1, 0.90)])
    database = FakeDatabase({1: _FakeFace(id=1, cluster_id=9)})
    result = incremental_assign(_unit_vector(5), vector_store=store, database=database)
    assert result is None
