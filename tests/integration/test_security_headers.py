"""Task 3.1.3 — baseline security headers on sidecar HTTP responses."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_response_includes_security_headers(api_client: TestClient) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "no-referrer"
