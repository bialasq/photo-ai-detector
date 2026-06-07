"""Task 1.1.3 — unified ErrorResponse contract across endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from errors import ErrorCode


def assert_error_contract(
    response,
    *,
    status_code: int,
    code: ErrorCode,
) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert isinstance(body["error"], str) and body["error"]
    assert body["code"] == code
    assert "hint" in body


def test_scan_folder_missing_directory_path_invalid(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/scan-folder",
        json={"folder_path": "Z:\\definitely-not-a-real-folder-12345"},
    )
    assert_error_contract(response, status_code=400, code=ErrorCode.PATH_INVALID)


def test_search_empty_names_validation_error(api_client: TestClient) -> None:
    response = api_client.get("/api/search", params={"names": "  ,  "})
    assert_error_contract(response, status_code=400, code=ErrorCode.VALIDATION_ERROR)


def test_identify_unknown_cluster_not_found(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/clusters/identify",
        json={"cluster_id": 999_999, "name": "Nobody"},
    )
    assert_error_contract(response, status_code=404, code=ErrorCode.NOT_FOUND)


def test_scan_folder_while_active_scan_in_progress(
    api_client: TestClient,
    tmp_path: Path,
) -> None:
    photo_dir = tmp_path / "photos"
    photo_dir.mkdir()
    services = api_client.app.state.services
    assert services.scan_state.try_begin_scan(total_files=1)

    response = api_client.post(
        "/api/scan-folder",
        json={"folder_path": str(photo_dir)},
    )
    assert_error_contract(response, status_code=409, code=ErrorCode.SCAN_IN_PROGRESS)


def test_photo_file_missing_not_found(api_client: TestClient) -> None:
    response = api_client.get("/api/photos/999999/file")
    assert_error_contract(response, status_code=404, code=ErrorCode.NOT_FOUND)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-root path check")
def test_scan_drive_root_path_invalid(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/scan-folder",
        json={"folder_path": "C:\\"},
    )
    assert_error_contract(response, status_code=400, code=ErrorCode.PATH_INVALID)
