"""Tests for cluster quality metrics (task 2.2.3)."""

from __future__ import annotations

import numpy as np
import pytest

from ai_core import ClusteringEngine, _compute_cluster_quality_metrics, l2_normalize
from database import DatabaseManager


def test_compute_cluster_quality_metrics_known_fixture() -> None:
    vectors = np.asarray(
        [
            l2_normalize([1.0] + [0.0] * 511),
            l2_normalize([0.99, 0.01] + [0.0] * 510),
            l2_normalize([0.0, 1.0] + [0.0] * 510),
            l2_normalize([0.0, 0.99, 0.01] + [0.0] * 509),
        ],
        dtype=np.float64,
    )
    labels = np.asarray([0, 0, 1, 1])

    silhouette, db_score, n_clusters = _compute_cluster_quality_metrics(vectors, labels)

    assert n_clusters == 2
    assert silhouette is not None
    assert silhouette > 0.3
    assert db_score is not None


def test_cluster_health_persisted_after_dbscan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PHOTO_ORGANIZER_DB_PATH", str(tmp_path / "metrics.db"))

    manager = DatabaseManager(str(tmp_path / "metrics.db"))
    manager.create_tables()

    embeddings = [
        l2_normalize([1.0, 0.0] + [0.0] * 510),
        l2_normalize([0.99, 0.01] + [0.0] * 510),
        l2_normalize([0.0, 1.0] + [0.0] * 510),
        l2_normalize([0.0, 0.99, 0.01] + [0.0] * 509),
    ]
    for index, embedding in enumerate(embeddings):
        photo_id = manager.insert_photo(f"/cluster/{index}.jpg")
        manager.insert_face(
            photo_id=photo_id,
            embedding=embedding,
            bounding_box={"x": 0, "y": 0, "w": 10, "h": 10},
        )

    engine = ClusteringEngine(manager)
    result = engine.run_incremental_clustering(eps=0.5, min_samples=2)

    assert result.silhouette is not None
    health = manager.get_latest_cluster_health()
    assert health is not None
    assert health.silhouette == pytest.approx(result.silhouette, rel=1e-4)
    assert health.n_clusters >= 1
