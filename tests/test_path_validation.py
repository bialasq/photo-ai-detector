"""Tests for path_validation.validate_scan_path (task 1.2.3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from errors import ErrorCode, PathValidationError
from path_validation import validate_scan_path


def _write_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(100, 100, 100)).save(path, format="JPEG")


def test_rejects_parent_traversal_segment(tmp_path: Path) -> None:
    target = tmp_path / "photos"
    target.mkdir()

    with pytest.raises(PathValidationError) as exc_info:
        validate_scan_path(str(tmp_path / "photos" / ".." / "secret"))

    assert exc_info.value.details.get("reason") == "parent_traversal"


def test_rejects_nonexistent_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises(PathValidationError) as exc_info:
        validate_scan_path(str(missing))

    assert exc_info.value.details.get("reason") == "not_found"


def test_rejects_file_not_directory(tmp_path: Path, jpeg_file_factory) -> None:
    file_path = jpeg_file_factory("single.jpg")

    with pytest.raises(PathValidationError) as exc_info:
        validate_scan_path(str(file_path))

    assert exc_info.value.details.get("reason") == "not_directory"


@pytest.mark.skipif(os.name != "nt", reason="Drive-root check is Windows-specific")
def test_rejects_drive_root_without_confirmation() -> None:
    with pytest.raises(PathValidationError) as exc_info:
        validate_scan_path("C:\\")

    assert exc_info.value.details.get("reason") == "whole_disk"


def test_rejects_symlink_outside_tree(tmp_path: Path) -> None:
    inside = tmp_path / "allowed"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    link = inside / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Platform does not allow directory symlinks in this environment")

    with pytest.raises(PathValidationError) as exc_info:
        validate_scan_path(str(link))

    assert exc_info.value.details.get("reason") == "symlink_escape"


def test_accepts_valid_subdirectory(tmp_path: Path) -> None:
    photos = tmp_path / "library" / "2024"
    photos.mkdir(parents=True)
    _write_jpeg(photos / "a.jpg")

    resolved = validate_scan_path(str(photos))

    assert resolved == photos.resolve()
    assert resolved.is_dir()


def test_api_rejects_traversal_path(api_client) -> None:
    """POST /api/scan-folder must not accept traversal payloads."""
    from tests.integration.test_error_responses import assert_error_contract

    response = api_client.post(
        "/api/scan-folder",
        json={"folder_path": "..\\Windows"},
    )
    assert_error_contract(response, status_code=400, code=ErrorCode.PATH_INVALID)
    body = response.json()
    assert "path_hash" in (body.get("details") or {})
    assert "Windows" not in body["error"]
