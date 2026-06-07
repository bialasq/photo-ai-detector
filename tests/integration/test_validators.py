"""Task 1.4.2 — strict Pydantic validation on POST endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_scan_folder_empty_path_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/api/scan-folder", json={"folder_path": "   "})
    assert response.status_code == 422


def test_scan_folder_oversized_path_returns_422(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/scan-folder",
        json={"folder_path": "C:\\" + ("x" * 5000)},
    )
    assert response.status_code == 422


def test_identify_cluster_invalid_name_characters_returns_422(
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/api/clusters/identify",
        json={"cluster_id": 1, "name": "Bad<Name>"},
    )
    assert response.status_code == 422


def test_identify_cluster_empty_name_returns_422(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/clusters/identify",
        json={"cluster_id": 1, "name": "   "},
    )
    assert response.status_code == 422


def test_identify_cluster_missing_target_returns_422(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/clusters/identify",
        json={"name": "Anna"},
    )
    assert response.status_code == 422


def test_identify_cluster_negative_cluster_id_returns_422(
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/api/clusters/identify",
        json={"cluster_id": -1, "name": "Anna"},
    )
    assert response.status_code == 422


def test_merge_people_same_ids_returns_422(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/people/merge",
        json={"target_person_id": 3, "source_person_id": 3},
    )
    assert response.status_code == 422


def test_merge_people_zero_source_id_returns_422(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/people/merge",
        json={"target_person_id": 1, "source_person_id": 0},
    )
    assert response.status_code == 422


def test_scan_folder_missing_field_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/api/scan-folder", json={})
    assert response.status_code == 422


def test_v1_health_ok(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["api_version"] == "v1"
