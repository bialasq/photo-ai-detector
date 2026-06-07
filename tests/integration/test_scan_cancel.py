"""Integration tests for scan cancellation (task 1.4.3)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from ai_core import DEFAULT_DETECTION_BATCH_SIZE


def _populate_photo_folder(folder: Path, count: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        path = folder / f"photo_{index:04d}.jpg"
        Image.new("RGB", (16, 16), color=(index % 256, 64, 128)).save(
            path,
            format="JPEG",
        )


def test_scan_cancel_after_two_batches(
    api_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "scan_cancel"
    total_files = DEFAULT_DETECTION_BATCH_SIZE * 2 + 5
    _populate_photo_folder(folder, total_files)

    batch_counter = {"count": 0}

    def noop_ingest_batch(_services, _batch_paths, _face_buffer) -> None:
        batch_counter["count"] += 1
        time.sleep(0.05)

    monkeypatch.setattr(main, "_ingest_detection_batch_sync", noop_ingest_batch)

    start = api_client.post(
        "/api/scan-folder",
        json={"folder_path": str(folder)},
    )
    assert start.status_code == 200
    assert start.json()["total_files"] == total_files

    status: dict = {}
    for _ in range(200):
        status = api_client.get("/api/scan-status").json()
        if status.get("processed", 0) >= DEFAULT_DETECTION_BATCH_SIZE:
            break
        time.sleep(0.05)

    cancel = api_client.post("/api/v1/scan-cancel")
    assert cancel.status_code == 200
    assert cancel.json() == {"ok": True}

    for _ in range(200):
        status = api_client.get("/api/scan-status").json()
        if not status.get("is_active"):
            break
        time.sleep(0.05)

    assert status.get("cancelled") is True
    assert status.get("phase") == "cancelled"
    assert 0 < int(status.get("processed", 0)) < int(status.get("total", 0))
    assert batch_counter["count"] >= 1
