"""GAP-003 — path privacy in logs and scan-status API (no plaintext PII paths)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PIL import Image

from logging_config import hash_path_for_log
from main import ScanProgressState, ThumbnailEngine


def test_scan_status_current_file_is_basename_in_snapshot() -> None:
    state = ScanProgressState()
    full_path = r"C:\Users\Jan_Kowalski\Photos\vacation\IMG_001.jpg"
    state.set_current_file(full_path)

    assert state.current_file == full_path
    assert state.snapshot()["current_file"] == "IMG_001.jpg"


def test_thumbnail_cache_miss_logs_source_hash_not_plaintext(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret_segment = "Jan_Kowalski"
    source_dir = tmp_path / "Users" / secret_segment / "Photos"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "photo.jpg"
    Image.new("RGB", (16, 16), color=(90, 90, 90)).save(source_path, format="JPEG")

    cache_dir = tmp_path / "thumb-cache"
    engine = ThumbnailEngine(cache_dir=cache_dir, max_bytes=10_000_000)

    with caplog.at_level(logging.INFO):
        engine.get_or_create_thumbnail(source_path, width=64, height=None)

    combined_log = " ".join(record.getMessage() for record in caplog.records)
    assert secret_segment not in combined_log
    assert str(source_path) not in combined_log

    miss_records = [
        record
        for record in caplog.records
        if getattr(record, "ctx", {}).get("event") == "thumbnail.miss"
    ]
    assert len(miss_records) == 1
    assert miss_records[0].ctx["source_hash"] == hash_path_for_log(source_path)
