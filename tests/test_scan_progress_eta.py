"""Unit tests for scan ETA estimation (feat/scan-eta)."""

from __future__ import annotations

import pytest

from main import ScanProgressState


def test_eta_resume_block_skips_uses_actual_rate_not_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    50 skipped + 10 real: rate uses actually_processed; remaining = total - processed.

    Remaining 40 files are all real (skip block finished) → eta == 400 s.
    """
    state = ScanProgressState()
    assert state.try_begin_scan(100) is True

    for _ in range(50):
        state.increment_processed()

    state.first_real_process_at = 1000.0
    state.increment_processed(10)
    state.increment_actually_processed(10)

    monkeypatch.setattr("main.time.monotonic", lambda: 1100.0)

    snapshot = state.snapshot()
    assert snapshot["processed"] == 60
    assert snapshot["is_active"] is True
    assert snapshot["phase"] == "scanning"

    elapsed = 100.0
    rate = elapsed / 10
    expected_eta = round(rate * (100 - 60), 1)
    assert expected_eta == 400.0
    assert snapshot["eta_seconds"] == 400.0

    wrong_rate_using_processed = elapsed / 60
    assert abs(wrong_rate_using_processed * 40 - 66.7) < 0.2


def test_eta_interleaved_skip_real_is_conservative(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Interleaved skips/real: ETA overestimates (~1000 s vs ~500 s real work).

    CELOWO — niedoszacowanie gorsze dla UX. Konserwatywne ETA = bezpieczne.
    """
    state = ScanProgressState()
    assert state.try_begin_scan(2000) is True

    state.processed = 1000
    state.actually_processed = 500
    state.first_real_process_at = 2000.0

    monkeypatch.setattr("main.time.monotonic", lambda: 2500.0)

    snapshot = state.snapshot()
    elapsed = 500.0
    rate = elapsed / 500
    assert rate == 1.0

    expected_eta = round(rate * (2000 - 1000), 1)
    assert expected_eta == 1000.0
    assert snapshot["eta_seconds"] == 1000.0


def test_eta_none_during_clustering(monkeypatch: pytest.MonkeyPatch) -> None:
    state = ScanProgressState()
    assert state.try_begin_scan(10) is True
    state.increment_processed(5)
    state.increment_actually_processed(5)
    state.set_phase("clustering")

    monkeypatch.setattr("main.time.monotonic", lambda: 9999.0)

    assert state.snapshot()["eta_seconds"] is None
