"""Task 1.2.4 — Swagger / ReDoc / OpenAPI gated behind dev mode."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from tests.conftest import stub_fastapi_test_startup


@pytest.fixture
def api_client_factory(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Build TestClient instances with isolated DB and without TensorFlow startup."""
    monkeypatch.setenv("PHOTO_ORGANIZER_DB_PATH", str(tmp_path / "test.db"))
    stub_fastapi_test_startup(monkeypatch)

    def _factory(*, dev_mode: bool | None) -> TestClient:
        application = main.create_application(dev_mode=dev_mode)
        return TestClient(application, raise_server_exceptions=True)

    return _factory


def test_docs_disabled_in_release_mode(api_client_factory) -> None:
    with api_client_factory(dev_mode=False) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_docs_enabled_in_dev_mode(api_client_factory) -> None:
    with api_client_factory(dev_mode=True) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        assert openapi.json()["info"]["title"] == "Photo Organizer Sidecar API"
