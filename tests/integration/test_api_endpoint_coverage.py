"""Task 3.2.2 — integration coverage for previously untested API endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from ai_core import l2_normalize
from database import DatabaseManager
from errors import ErrorCode
from tests.conftest import stub_fastapi_test_startup
from tests.integration.test_error_responses import assert_error_contract


def _bbox() -> dict[str, int]:
    return {"x": 0, "y": 0, "w": 32, "h": 32}


def _seed_person_with_face(
    database: DatabaseManager,
    photo_path: str,
    *,
    name: str = "CoveragePerson",
) -> tuple[int, int, int]:
    embedding = l2_normalize([1.0] + [0.0] * 511)
    person_id = database.insert_person(name)
    photo_id = database.insert_photo(photo_path)
    face_id = database.insert_face(photo_id, embedding, _bbox())
    database.assign_faces_to_person([face_id], person_id)
    return person_id, photo_id, face_id


def _seed_unnamed_cluster(
    database: DatabaseManager,
    photo_path: str,
    *,
    cluster_id: int = 3,
) -> tuple[int, int]:
    embedding = l2_normalize([0.0, 1.0] + [0.0] * 510)
    photo_id = database.insert_photo(photo_path)
    face_ids = [
        database.insert_face(photo_id, embedding, _bbox()),
        database.insert_face(photo_id, embedding, _bbox()),
    ]
    database.update_faces_cluster_id(face_ids, cluster_id)
    return cluster_id, photo_id


def _seed_noise_face(database: DatabaseManager, photo_path: str) -> int:
    embedding = l2_normalize([0.0, 1.0] + [0.0] * 510)
    photo_id = database.insert_photo(photo_path)
    return database.insert_face(photo_id, embedding, _bbox())


@pytest.fixture
def dev_api_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """TestClient with dev routes enabled and isolated DB."""
    monkeypatch.setenv("PHOTO_ORGANIZER_DB_PATH", str(tmp_path / "dev-test.db"))
    monkeypatch.setenv(main.DEV_MODE_ENV_VAR, "1")
    stub_fastapi_test_startup(monkeypatch)
    application = main.create_application(dev_mode=True)
    with TestClient(application) as client:
        yield client


def test_get_health_returns_ok_payload(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_search_returns_photos_for_named_person(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("search-target.jpg")
    _, photo_id, _ = _seed_person_with_face(
        database,
        str(jpeg_path),
        name="SearchTarget",
    )

    response = api_client.get("/api/search", params={"names": "SearchTarget"})
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert {item["photo_id"] for item in payload} == {photo_id}


def test_get_noise_faces_empty_list(api_client: TestClient) -> None:
    response = api_client.get("/api/clusters/noise")
    assert response.status_code == 200
    assert response.json() == []


def test_get_noise_faces_returns_seeded_noise(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("noise-face.jpg")
    face_id = _seed_noise_face(database, str(jpeg_path))

    response = api_client.get("/api/clusters/noise")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["face_id"] == face_id
    assert payload[0]["bounding_box"]["w"] == 32


def test_get_people_empty_list(api_client: TestClient) -> None:
    response = api_client.get("/api/people")
    assert response.status_code == 200
    assert response.json() == []


def test_get_people_returns_seeded_person(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("people-list.jpg")
    person_id, _, _ = _seed_person_with_face(database, str(jpeg_path), name="ListedPerson")

    response = api_client.get("/api/people")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    row = payload[0]
    assert row["id"] == person_id
    assert row["name"] == "ListedPerson"
    assert row["face_count"] == 1


def test_post_identify_cluster_happy_path(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("identify-cluster.jpg")
    cluster_id, _ = _seed_unnamed_cluster(database, str(jpeg_path))

    response = api_client.post(
        "/api/clusters/identify",
        json={"cluster_id": cluster_id, "name": "NamedCluster"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["name"] == "NamedCluster"
    assert body["cluster_id"] == cluster_id
    assert body["person_id"] > 0


def test_get_cluster_photos_returns_seeded_photos(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("cluster-photos.jpg")
    cluster_id, photo_id = _seed_unnamed_cluster(database, str(jpeg_path))

    response = api_client.get(f"/api/clusters/{cluster_id}/photos")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["photo_id"] == photo_id


def test_get_cluster_photos_invalid_cluster_id_validation_error(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/clusters/-1/photos")
    assert_error_contract(
        response,
        status_code=400,
        code=ErrorCode.VALIDATION_ERROR,
    )


def test_get_v1_clusters_health_empty(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/clusters/health")
    assert response.status_code == 200
    body = response.json()
    assert body["run_at"] is None
    assert body["silhouette"] is None
    assert body["n_clusters"] == 0
    assert "No clustering run recorded yet." in body["recommendation"]


def test_get_v1_clusters_health_returns_latest_metrics(
    api_client: TestClient,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    database.insert_cluster_health(
        silhouette=0.55,
        db_score=0.42,
        n_clusters=2,
        eps=0.35,
    )

    response = api_client.get("/api/v1/clusters/health")
    assert response.status_code == 200
    body = response.json()
    assert body["silhouette"] == pytest.approx(0.55)
    assert body["db_score"] == pytest.approx(0.42)
    assert body["n_clusters"] == 2
    assert body["run_at"] is not None
    assert "acceptable" in body["recommendation"].lower()


def test_post_v1_log_error_accepted(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/log-error",
        json={
            "scope": "gallery",
            "message": "Test boundary crash",
            "stack": "at GalleryGrid (GalleryGrid.tsx:42)",
        },
    )
    assert response.status_code == 204
    assert response.content == b""


def test_post_v1_log_error_validation_error(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/log-error",
        json={"scope": "", "message": "missing scope"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == ErrorCode.VALIDATION_ERROR
    assert isinstance(body["error"], str) and body["error"]
    assert body.get("details", {}).get("errors")


def test_dev_simulate_scan_disabled_returns_not_found(
    api_client: TestClient,
) -> None:
    response = api_client.post("/api/dev/simulate-scan")
    assert response.status_code == 404


def test_dev_simulate_scan_starts_scan_when_enabled(
    dev_api_client: TestClient,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "dev_simulate"
    folder.mkdir()
    Image.new("RGB", (64, 64), color=(90, 90, 90)).save(
        folder / "dev-scan.jpg",
        format="JPEG",
    )

    response = dev_api_client.post(
        "/api/dev/simulate-scan",
        json={"folder_path": str(folder)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"
    assert body["total_files"] == 1


def test_get_photo_file_happy_path(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("photo-file.jpg")
    photo_id = database.insert_photo(str(jpeg_path))

    response = api_client.get(f"/api/photos/{photo_id}/file")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.content


def _assert_not_found_without_path_leak(response) -> None:
    assert_error_contract(response, status_code=404, code=ErrorCode.NOT_FOUND)
    error_text = response.json()["error"]
    assert ":\\" not in error_text
    assert error_text.count("/") <= 0 or "id=" in error_text


def test_get_photo_thumbnail_missing_not_found(api_client: TestClient) -> None:
    response = api_client.get("/api/photos/999999/thumbnail")
    _assert_not_found_without_path_leak(response)


def test_get_photo_thumbnail_happy_path(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("photo-thumb.jpg")
    photo_id = database.insert_photo(str(jpeg_path))

    response = api_client.get(f"/api/photos/{photo_id}/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_get_person_thumbnail_missing_not_found(api_client: TestClient) -> None:
    response = api_client.get("/api/people/999999/thumbnail")
    _assert_not_found_without_path_leak(response)


def test_get_person_thumbnail_happy_path(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("person-thumb.jpg")
    person_id, _, _ = _seed_person_with_face(database, str(jpeg_path))

    response = api_client.get(f"/api/people/{person_id}/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_get_face_thumbnail_missing_not_found(api_client: TestClient) -> None:
    response = api_client.get("/api/faces/999999/thumbnail")
    _assert_not_found_without_path_leak(response)


def test_get_face_thumbnail_happy_path(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("face-thumb.jpg")
    photo_id = database.insert_photo(str(jpeg_path))
    embedding = l2_normalize([0.5, 0.5] + [0.0] * 510)
    face_id = database.insert_face(photo_id, embedding, _bbox())

    response = api_client.get(
        f"/api/faces/{face_id}/thumbnail",
        params={"crop": "1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_get_cluster_thumbnail_missing_not_found(api_client: TestClient) -> None:
    response = api_client.get("/api/clusters/999999/thumbnail")
    _assert_not_found_without_path_leak(response)


def test_get_cluster_thumbnail_happy_path(
    api_client: TestClient,
    jpeg_file_factory,
) -> None:
    database: DatabaseManager = api_client.app.state.services.database
    jpeg_path = jpeg_file_factory("cluster-thumb.jpg")
    cluster_id, _ = _seed_unnamed_cluster(database, str(jpeg_path))

    response = api_client.get(f"/api/clusters/{cluster_id}/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")
