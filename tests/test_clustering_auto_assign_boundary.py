"""Task 3.2.1 tier 2 — ClusteringEngine auto-assign vs boundary queue."""

from __future__ import annotations

from ai_core import ClusteringEngine, l2_normalize
from database import DatabaseManager


def _bbox() -> dict[str, int]:
    return {"x": 0, "y": 0, "w": 48, "h": 48}


def _reference_embedding() -> list[float]:
    return l2_normalize([1.0] + [0.0] * 511)


def _auto_match_embedding() -> list[float]:
    return l2_normalize([0.99, 0.01] + [0.0] * 510)


def _boundary_match_embedding() -> list[float]:
    return l2_normalize([0.60, 0.80] + [0.0] * 510)


def _orthogonal_cluster_embedding() -> list[float]:
    return l2_normalize([0.0, 1.0] + [0.0] * 510)


def _seed_known_person(
    database: DatabaseManager,
    *,
    name: str,
    embedding: list[float],
) -> int:
    person_id = database.insert_person(name)
    photo_id = database.insert_photo(f"/known/{name}.jpg")
    face_id = database.insert_face(photo_id, embedding, _bbox())
    database.assign_faces_to_person([face_id], person_id)
    return person_id


def test_clustering_auto_assigns_close_cluster_to_known_person(
    temp_db: DatabaseManager,
) -> None:
    reference = _reference_embedding()
    known_person_id = _seed_known_person(temp_db, name="Alice", embedding=reference)

    cluster_photo = temp_db.insert_photo("/new/auto-cluster.jpg")
    match = _auto_match_embedding()
    temp_db.insert_face(cluster_photo, match, _bbox())
    temp_db.insert_face(cluster_photo, match, _bbox())

    engine = ClusteringEngine(temp_db)
    result = engine.run_incremental_clustering(eps=0.5, min_samples=2)

    assert result.auto_assigned_faces >= 2
    assigned_faces = temp_db.get_faces_for_person(known_person_id)
    assert len(assigned_faces) >= 3
    assert result.boundary_faces_queued == 0


def test_clustering_queues_boundary_cluster_for_manual_review(
    temp_db: DatabaseManager,
) -> None:
    reference = _reference_embedding()
    _seed_known_person(temp_db, name="Bob", embedding=reference)

    cluster_photo = temp_db.insert_photo("/new/boundary-cluster.jpg")
    boundary = _boundary_match_embedding()
    temp_db.insert_face(cluster_photo, boundary, _bbox())
    temp_db.insert_face(cluster_photo, boundary, _bbox())

    engine = ClusteringEngine(temp_db)
    result = engine.run_incremental_clustering(eps=0.5, min_samples=2)

    assert result.auto_assigned_faces == 0
    assert result.boundary_faces_queued >= 2
    unassigned = temp_db.get_unassigned_faces()
    assert len(unassigned) >= 2
    assert all(face.person_id is None for face in unassigned)


def test_process_decision_queue_names_pending_cluster(
    temp_db: DatabaseManager,
) -> None:
    cluster_photo = temp_db.insert_photo("/pending/cluster.jpg")
    embedding = _orthogonal_cluster_embedding()
    temp_db.insert_face(cluster_photo, embedding, _bbox())
    temp_db.insert_face(cluster_photo, embedding, _bbox())

    engine = ClusteringEngine(temp_db)
    result = engine.run_incremental_clustering(eps=0.5, min_samples=2)

    assert result.clusters_created >= 1
    pending_id = result.pending_cluster_ids[0]
    person_id = engine.process_decision_queue(pending_id, "Magda")

    assert person_id > 0
    assigned = temp_db.get_faces_for_person(person_id)
    assert len(assigned) >= 2
    assert all(face.person_id == person_id for face in assigned)
