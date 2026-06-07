"""Tests for discover_image_files_recursively symlink sandbox (task 3.1.4, GAP-002)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PIL import Image

from main import discover_image_files_recursively


def _write_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(100, 100, 100)).save(path, format="JPEG")


def test_discover_skips_symlink_escape_but_keeps_legal_nested_files(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    scan_root = tmp_path / "scan_root"
    outside = tmp_path / "outside"
    scan_root.mkdir()
    outside.mkdir()

    inside_file = scan_root / "inside.jpg"
    _write_jpeg(inside_file)

    nested_file = scan_root / "sub" / "deep" / "real.jpg"
    _write_jpeg(nested_file)

    outside_secret = outside / "secret.jpg"
    _write_jpeg(outside_secret)

    escape_link = scan_root / "escape.jpg"
    try:
        escape_link.symlink_to(outside_secret)
    except OSError:
        pytest.skip("Platform does not allow symlinks in this environment")

    outside_via_dir = outside / "via_dir.jpg"
    _write_jpeg(outside_via_dir)
    dir_link = scan_root / "linked_out"
    try:
        dir_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pass

    with caplog.at_level(logging.WARNING):
        discovered = discover_image_files_recursively(scan_root.resolve())

    resolved_discovered = {p.resolve() for p in discovered}

    assert inside_file.resolve() in resolved_discovered
    assert nested_file.resolve() in resolved_discovered
    assert outside_secret.resolve() not in resolved_discovered
    assert outside_via_dir.resolve() not in resolved_discovered
    assert len(resolved_discovered) == 2

    outside_root_logs = [
        record
        for record in caplog.records
        if getattr(record, "ctx", {}).get("event") == "scan.file.outside_root"
    ]
    assert len(outside_root_logs) >= 1
    assert all(
        "path_hash" in getattr(record, "ctx", {}) for record in outside_root_logs
    )
