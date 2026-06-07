"""DNS rebinding mitigation — Host header check (task 3.1.2, GAP-001)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from tests.conftest import stub_fastapi_test_startup


@pytest.fixture
def host_check_client(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """TestClient without the autouse testserver allowlist (production Host rules)."""
    monkeypatch.delenv("PHOTO_ORGANIZER_EXTRA_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("PHOTO_ORGANIZER_DB_PATH", str(tmp_path / "dns-rebind.db"))
    stub_fastapi_test_startup(monkeypatch)
    application = main.create_application(dev_mode=False)
    with TestClient(application) as client:
        yield client


def test_accepts_loopback_host(host_check_client: TestClient) -> None:
    port = main._resolve_sidecar_port()
    response = host_check_client.get(
        "/health",
        headers={"Host": f"127.0.0.1:{port}"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rejects_evil_host(
    host_check_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        response = host_check_client.get(
            "/health",
            headers={
                "Host": "evil.com",
                "Origin": "http://evil.com",
            },
        )

    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body.get("status") != "ok"
    assert "access-control-allow-origin" not in {
        name.lower() for name in response.headers
    }

    rejected = [
        record
        for record in caplog.records
        if getattr(record, "ctx", {}).get("event") == "host.rejected"
    ]
    assert len(rejected) == 1
    assert "host_hash" in getattr(rejected[0], "ctx", {})


def test_testserver_allowed_only_with_extra_env(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHOTO_ORGANIZER_DB_PATH", str(tmp_path / "dns-rebind.db"))
    stub_fastapi_test_startup(monkeypatch)

    monkeypatch.delenv("PHOTO_ORGANIZER_EXTRA_ALLOWED_HOSTS", raising=False)
    application = main.create_application(dev_mode=False)
    with TestClient(application) as client:
        blocked = client.get("/health")

    assert blocked.status_code == 403

    monkeypatch.setenv("PHOTO_ORGANIZER_EXTRA_ALLOWED_HOSTS", "testserver")
    application = main.create_application(dev_mode=False)
    with TestClient(application) as client:
        allowed = client.get("/health")

    assert allowed.status_code == 200
    assert allowed.json() == {"status": "ok"}
