"""Task 3.2.1 tier 3 — gallery and cluster HTTP endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ai_core import l2_normalize
from database import DatabaseManager
from errors import ErrorCode
from tests.integration.test_error_responses import assert_error_contract


def _bbox() -> dict[str, int]:
    return {"x": 0, "y": 0, "w": 32, "h": 32}


def _seed_gallery(
    database: DatabaseManager,
    *,
    processed: bool = True,
    has_faces: bool = True,
) -> tuple[int, int]:
    embedding = l2_normalize([1.0] + [0.0] * 511)
    person_id = database.insert_person("GalleryPerson")
    photo_id = database.insert_photo("/gallery/sample.jpg")
    face_id = database.insert_face(photo_id, embedding, _bbox())
    database.assign_faces_to_person([face_id], person_id)
    if processed:
        database.mark_photo_as_processed(photo_id, has_faces=has_faces)
    return photo_id, person_id


def _seed_unnamed_cluster(database: DatabaseManager) -> int:
    embedding = l2_normalize([0.0, 1.0] + [0.0] * 510)
    photo_id = database.insert_photo("/clusters/unnamed.jpg")
    database.insert_face(photo_id, embedding, _bbox())
    database.insert_face(photo_id, embedding, _bbox())
    database.update_faces_cluster_id(
        [
            row.id
            for row in database.get_faces_for_photo(photo_id)
        ],
        3,
    )
    return 3


def test_get_gallery_processed_filter(
    api_client: TestClient,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    processed_id, person_id = _seed_gallery(database, processed=True)
    unprocessed_id = database.insert_photo("/gallery/unprocessed.jpg")

    all_response = api_client.get("/api/gallery")
    assert all_response.status_code == 200
    all_ids = {item["photo_id"] for item in all_response.json()}
    assert processed_id in all_ids
    assert unprocessed_id in all_ids

    processed_response = api_client.get("/api/gallery", params={"ai_status": "processed"})
    assert processed_response.status_code == 200
    processed_ids = {item["photo_id"] for item in processed_response.json()}
    assert processed_id in processed_ids
    assert unprocessed_id not in processed_ids

    person_response = api_client.get(
        "/api/gallery",
        params={"person_ids": str(person_id), "ai_status": "processed"},
    )
    assert person_response.status_code == 200
    person_rows = person_response.json()
    assert len(person_rows) == 1
    assert person_rows[0]["photo_id"] == processed_id


def test_get_gallery_invalid_ai_status_returns_validation_error(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/gallery", params={"ai_status": "not-a-status"})
    assert_error_contract(
        response,
        status_code=400,
        code=ErrorCode.VALIDATION_ERROR,
    )


def test_get_unnamed_clusters_returns_seeded_cluster(
    api_client: TestClient,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    cluster_id = _seed_unnamed_cluster(database)

    response = api_client.get("/api/clusters/unnamed")
    assert response.status_code == 200
    payload = response.json()
    cluster_ids = {item["cluster_id"] for item in payload}
    assert cluster_id in cluster_ids
    summary = next(item for item in payload if item["cluster_id"] == cluster_id)
    assert summary["face_count"] >= 2
    assert summary["exemplar_face_id"] > 0


def test_post_identify_cluster_unknown_cluster_not_found(
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/api/clusters/identify",
        json={"cluster_id": 999_999, "name": "Nobody"},
    )
    assert_error_contract(response, status_code=404, code=ErrorCode.NOT_FOUND)


def test_post_merge_people_happy_path(
    api_client: TestClient,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    embedding = l2_normalize([0.5, 0.5] + [0.0] * 510)
    target_id = database.insert_person("MergeTarget")
    source_id = database.insert_person("MergeSource")
    photo_id = database.insert_photo("/merge/api.jpg")
    face_id = database.insert_face(photo_id, embedding, _bbox())
    database.assign_faces_to_person([face_id], source_id)

    response = api_client.post(
        "/api/people/merge",
        json={"target_person_id": target_id, "source_person_id": source_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["faces_moved"] == 1
    assert body["target_person_id"] == target_id
    assert database.get_face_by_id(face_id).person_id == target_id


def test_post_merge_people_missing_source_not_found(
    api_client: TestClient,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    target_id = database.insert_person("LonelyTarget")

    response = api_client.post(
        "/api/people/merge",
        json={"target_person_id": target_id, "source_person_id": 999_999},
    )
    assert_error_contract(response, status_code=404, code=ErrorCode.NOT_FOUND)
