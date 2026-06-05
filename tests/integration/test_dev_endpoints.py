"""Task 1.2.1 — /api/dev/* gated behind PHOTO_ORGANIZER_DEV=1."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def isolated_db_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point lifespan startup at a throwaway SQLite file."""
    db_path = str(tmp_path / "test_organizer.db")
    monkeypatch.setattr(main, "DEFAULT_DATABASE_PATH", db_path)
    return db_path


@pytest.fixture
def fast_client(
    isolated_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    """Build a TestClient without loading TensorFlow / DeepFace at startup."""
    monkeypatch.setattr(main, "verify_ai_runtime_dependencies", lambda: None)
    yield


def _make_client(*, dev_mode: bool | None) -> TestClient:
    application = main.create_application(dev_mode=dev_mode)
    return TestClient(application, raise_server_exceptions=True)


def test_dev_endpoints_disabled_by_default(
    fast_client: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production default: no PHOTO_ORGANIZER_DEV → /api/dev/* returns 404."""
    monkeypatch.delenv(main.DEV_MODE_ENV_VAR, raising=False)
    with _make_client(dev_mode=False) as client:
        response = client.post("/api/dev/reset-library")

    assert response.status_code == 404


def test_dev_endpoints_enabled_with_env(
    fast_client: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHOTO_ORGANIZER_DEV=1 (or dev_mode=True) registers dev routes."""
    monkeypatch.setenv(main.DEV_MODE_ENV_VAR, "1")
    with _make_client(dev_mode=True) as client:
        response = client.post("/api/dev/reset-library")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["removed"], dict)


def test_is_dev_mode_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(main.DEV_MODE_ENV_VAR, raising=False)
    assert main.is_dev_mode() is False


def test_is_dev_mode_true_only_when_env_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(main.DEV_MODE_ENV_VAR, "true")
    assert main.is_dev_mode() is False
    monkeypatch.setenv(main.DEV_MODE_ENV_VAR, "1")
    assert main.is_dev_mode() is True
