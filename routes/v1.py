"""API v1 routes (task 1.4.1)."""

from __future__ import annotations

from fastapi import APIRouter, status

router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.get(
    "/health",
    summary="Versioned health check",
    status_code=status.HTTP_200_OK,
)
async def health_v1() -> dict[str, str]:
    """Liveness probe for the v1 API surface."""
    return {"status": "ok", "api_version": "v1"}
