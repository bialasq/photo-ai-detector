"""API v1 routes (tasks 1.4.1, 1.4.3, 1.1.4)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["v1"])
LOGGER = logging.getLogger(__name__)


@router.get(
    "/health",
    summary="Versioned health check",
    status_code=status.HTTP_200_OK,
)
async def health_v1() -> dict[str, str]:
    """Liveness probe for the v1 API surface."""
    return {"status": "ok", "api_version": "v1"}


class FrontendErrorLogRequest(BaseModel):
    """Body for POST /api/v1/log-error (task 1.1.4)."""

    scope: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=2000)
    stack: str | None = Field(default=None, max_length=8000)


@router.post(
    "/log-error",
    summary="Log a React Error Boundary crash",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def log_frontend_error(body: FrontendErrorLogRequest) -> None:
    """Accept frontend error reports and write them to backend logs."""
    LOGGER.error(
        "frontend: scope=%s message=%s",
        body.scope,
        body.message,
        extra={"ctx": {"scope": body.scope, "component_stack": body.stack}},
    )


@router.post(
    "/scan-cancel",
    summary="Request cancellation of the active folder scan",
    status_code=status.HTTP_200_OK,
)
async def cancel_scan() -> dict[str, bool]:
    """Set the scan cancellation flag; the worker stops between batches."""
    from main import get_services

    services = get_services()
    services.scan_state.request_cancel()
    return {"ok": True}


class ClusterHealthResponse(BaseModel):
    """Latest DBSCAN quality metrics (task 2.2.3)."""

    run_at: str | None = None
    silhouette: float | None = None
    db_score: float | None = None
    n_clusters: int = 0
    eps: float = 0.35
    recommendation: str = "No clustering run recorded yet."


@router.get(
    "/clusters/health",
    summary="Latest clustering quality metrics",
    status_code=status.HTTP_200_OK,
)
async def get_cluster_health() -> ClusterHealthResponse:
    """Return the most recent silhouette / Davies-Bouldin snapshot."""
    from main import get_services

    services = get_services()
    row = services.database.get_latest_cluster_health()
    if row is None:
        return ClusterHealthResponse()

    recommendation = "Cluster separation looks acceptable."
    if row.silhouette is not None and row.silhouette < 0.3:
        recommendation = (
            "Silhouette score is low — consider rescanning with more photos or "
            "waiting for a full re-cluster."
        )
    elif row.silhouette is not None and row.silhouette < 0.4:
        recommendation = "Cluster quality is moderate — naming clusters manually may help."

    return ClusterHealthResponse(
        run_at=row.run_at,
        silhouette=row.silhouette,
        db_score=row.db_score,
        n_clusters=row.n_clusters,
        eps=row.eps,
        recommendation=recommendation,
    )
