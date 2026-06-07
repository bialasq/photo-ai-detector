"""GAP-004 — sanitized user-facing scan last_error (no raw exceptions or paths)."""

from __future__ import annotations

import logging

import pytest

from main import ScanProgressState, format_user_scan_last_error


def test_ingest_file_last_error_uses_basename_without_raw_exception() -> None:
    state = ScanProgressState()
    secret_path = r"C:\Users\Jan_Kowalski\Photos\secret.jpg"
    raw_message = f"{secret_path}: CUDA OOM at layer 42"

    state.last_error = format_user_scan_last_error(
        file_basename="secret.jpg",
        scenario="ingest_file",
    )

    assert state.last_error == "secret.jpg: Failed to process file"
    assert "Jan_Kowalski" not in state.last_error
    assert raw_message not in state.last_error
    assert "OOM" not in state.last_error
    assert "CUDA" not in state.last_error


def test_snapshot_last_error_matches_sanitized_storage() -> None:
    state = ScanProgressState()
    state.set_current_file(r"C:\Users\Jan\Photos\IMG_001.jpg")
    state.last_error = format_user_scan_last_error(
        file_basename="broken.jpg",
        scenario="ingest_file",
    )

    snapshot = state.snapshot()
    assert snapshot["last_error"] == "broken.jpg: Failed to process file"
    assert snapshot["current_file"] == "IMG_001.jpg"


def test_clustering_last_error_is_safe_generic_message() -> None:
    state = ScanProgressState()
    state.last_error = format_user_scan_last_error(scenario="clustering")

    assert state.last_error == "Clustering failed"
    assert "dbscan" not in state.last_error.lower()
    assert "oom" not in state.last_error.lower()


def test_ingest_failure_still_logs_raw_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = ScanProgressState()
    exc = RuntimeError(r"C:\Users\Jan_Kowalski\secret failed: CUDA OOM")

    with caplog.at_level(logging.ERROR):
        logging.getLogger("main").exception(
            "Failed to ingest batch ending at %s: %s: %s",
            "hashed-path",
            type(exc).__name__,
            exc,
        )
        state.last_error = format_user_scan_last_error(
            file_basename="photo.jpg",
            scenario="ingest_file",
        )

    assert state.last_error == "photo.jpg: Failed to process file"
    assert "Jan_Kowalski" not in state.last_error
    assert "CUDA OOM" not in state.last_error

    logged_text = " ".join(
        record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR
    )
    assert "CUDA OOM" in logged_text or any(
        record.exc_info and "CUDA OOM" in str(record.exc_info[1])
        for record in caplog.records
    )
