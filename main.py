"""
FastAPI sidecar server for the offline photo organizer (Tauri desktop companion).

This process exposes a minimal local HTTP API on 127.0.0.1 so the desktop UI can:
  - Trigger recursive folder ingestion (face detection + DB persistence)
  - Poll scanning progress without blocking the UI thread
  - Search photos by person names (strict AND / intersection semantics)

Architecture:
  ┌─────────────┐   HTTP (localhost only)   ┌──────────────────────────────────┐
  │ Tauri / Web │ ◄────────────────────────► │ FastAPI (main.py)                │
  │   Frontend  │                            │  ├─ asyncio background scan task │
  └─────────────┘                            │  └─ thread pool for CPU-bound AI │
                                             └───────────┬──────────────────────┘
                                                         │
                         ┌───────────────────────────────┼───────────────────────────────┐
                         ▼                               ▼                               ▼
                 DatabaseManager                 AICoreEngine                  ClusteringEngine
                   (database.py)                  (ai_core.py)                    (ai_core.py)
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from ai_core import (
    AICoreError,
    AICoreEngine,
    ClusteringEngine,
    ClusteringError,
    FaceDetectionError,
    ingest_image_to_database,
)
from database import (
    DatabaseError,
    DatabaseManager,
    RecordNotFoundError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server / security configuration
# ---------------------------------------------------------------------------

DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 8000
DEFAULT_DATABASE_PATH: Final[str] = "organizer.db"

API_PREFIX: Final[str] = "/api"

# Folder scan discovers only these extensions (spec: jpg, jpeg, png).
SCAN_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".png"})

# Explicit CORS allow-list for offline local desktop shells.
ALLOWED_CORS_ORIGINS: Final[list[str]] = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:1420",
    "http://localhost:5173",
    "http://127.0.0.1:1420",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
    "tauri://localhost",
    "https://tauri.localhost",
    "asset://localhost",
]

# Regex fallback: any localhost port + Tauri / asset protocols.
ALLOWED_CORS_ORIGIN_REGEX: Final[str] = (
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|"
    r"^tauri://localhost$|"
    r"^https://tauri\.localhost(:\d+)?$|"
    r"^asset://.*$"
)

# Thread pool size for CPU-bound DeepFace / DBSCAN work (one scan at a time).
AI_THREAD_POOL_WORKERS: Final[int] = 1


# ---------------------------------------------------------------------------
# Pydantic request / response models (OpenAPI + validation)
# ---------------------------------------------------------------------------

class ScanFolderRequest(BaseModel):
    """Body for POST /api/scan-folder."""

    folder_path: str = Field(
        ...,
        min_length=1,
        description="Absolute or relative path to a local directory to scan recursively.",
        examples=[r"C:\Users\Photos\Vacation2024"],
    )

    @field_validator("folder_path")
    @classmethod
    def strip_folder_path(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("folder_path must not be empty or whitespace")
        return stripped


class ScanFolderResponse(BaseModel):
    """Response for POST /api/scan-folder."""

    status: str = Field(..., description="Always 'started' when the background worker launches.")
    total_files: int = Field(..., ge=0, description="Number of image files queued for ingestion.")


class ScanStatusResponse(BaseModel):
    """Response for GET /api/scan-status."""

    processed: int = Field(..., ge=0, description="Files fully ingested so far.")
    total: int = Field(..., ge=0, description="Total files discovered for this scan run.")
    is_active: bool = Field(..., description="True while the background worker is running.")


class SearchResultItem(BaseModel):
    """One photo returned by GET /api/search."""

    photo_id: int = Field(..., ge=1)
    file_path: str = Field(..., min_length=1)


class ErrorResponse(BaseModel):
    """Standard error payload (documented in OpenAPI responses)."""

    detail: str


# ---------------------------------------------------------------------------
# Thread-safe scan progress state
# ---------------------------------------------------------------------------

@dataclass
class ScanProgressState:
    """
    Live scanning metrics shared between the asyncio task and HTTP handlers.

    All reads/writes pass through `threading.Lock` because:
      - FastAPI handlers run on the event loop thread
      - Per-file ingestion runs inside `asyncio.to_thread()` worker threads
    """

    processed: int = 0
    total: int = 0
    is_active: bool = False
    last_error: Optional[str] = None
    current_file: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """Return a consistent copy for API responses."""
        with self._lock:
            return {
                "processed": self.processed,
                "total": self.total,
                "is_active": self.is_active,
            }

    def try_begin_scan(self, total_files: int) -> bool:
        """
        Attempt to mark a new scan as active.

        Returns:
            True if this call acquired the scan lock (scan may start).
            False if another scan is already running.
        """
        with self._lock:
            if self.is_active:
                return False
            self.processed = 0
            self.total = total_files
            self.is_active = True
            self.last_error = None
            self.current_file = None
            return True

    def set_total(self, total_files: int) -> None:
        with self._lock:
            self.total = total_files

    def increment_processed(self) -> None:
        with self._lock:
            self.processed += 1

    def set_current_file(self, file_path: Optional[str]) -> None:
        with self._lock:
            self.current_file = file_path

    def finish_scan(self, *, error_message: Optional[str] = None) -> None:
        with self._lock:
            self.is_active = False
            self.current_file = None
            if error_message is not None:
                self.last_error = error_message


# ---------------------------------------------------------------------------
# Application service container (initialized at startup)
# ---------------------------------------------------------------------------

@dataclass
class AppServices:
    """
    Holds long-lived service instances created during FastAPI lifespan startup.

    Attributes:
        database: SQLite persistence layer.
        ai_engine: DeepFace detection + embedding extraction.
        clustering: DBSCAN incremental clustering over unassigned faces.
        scan_state: Thread-safe progress tracker for folder scans.
        scan_task: Handle to the currently running asyncio.Task (if any).
        scan_task_lock: asyncio.Lock preventing concurrent scan task creation.
    """

    database: DatabaseManager
    ai_engine: AICoreEngine
    clustering: ClusteringEngine
    scan_state: ScanProgressState
    scan_task: Optional[asyncio.Task[None]] = None
    scan_task_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Module-level reference set in lifespan (avoids circular imports with routes).
_services: Optional[AppServices] = None


def get_services() -> AppServices:
    """
    Retrieve initialized application services.

    Raises:
        RuntimeError: If called before FastAPI lifespan startup completed.
    """
    if _services is None:
        raise RuntimeError(
            "Application services are not initialized. "
            "Ensure the FastAPI lifespan context has started."
        )
    return _services


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def resolve_and_validate_folder(folder_path: str) -> Path:
    """
    Resolve `folder_path` to an absolute directory on disk.

    Raises:
        FileNotFoundError: Path does not exist.
        NotADirectoryError: Path exists but is not a directory.
        ValueError: Path is not a directory (generic fallback).
    """
    resolved = Path(folder_path).expanduser().resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"Directory does not exist: {resolved}")

    if not resolved.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {resolved}")

    return resolved


def discover_image_files_recursively(folder: Path) -> list[Path]:
    """
    Recursively collect .jpg / .jpeg / .png files under `folder`.

    Returns:
        Sorted list of absolute file paths (stable ingestion order).
    """
    discovered: list[Path] = []

    for candidate in folder.rglob("*"):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in SCAN_IMAGE_SUFFIXES:
            continue
        discovered.append(candidate.resolve())

    discovered.sort(key=lambda path: str(path).lower())
    LOGGER.info(
        "Discovered %s image file(s) under %s (suffixes=%s)",
        len(discovered),
        folder,
        sorted(SCAN_IMAGE_SUFFIXES),
    )
    return discovered


def parse_comma_separated_names(names_parameter: str) -> list[str]:
    """
    Parse `?names=Magda,Łukasz` into a clean list for intersection search.

    Raises:
        ValueError: If the parameter is empty or yields no usable names.
    """
    if not names_parameter or not names_parameter.strip():
        raise ValueError("Query parameter 'names' must not be empty")

    parts = [segment.strip() for segment in names_parameter.split(",")]
    non_empty = [name for name in parts if name]

    if not non_empty:
        raise ValueError(
            "Query parameter 'names' must contain at least one non-empty name"
        )

    return non_empty


# ---------------------------------------------------------------------------
# Exception → HTTP mapping
# ---------------------------------------------------------------------------

def raise_http_exception_from_error(exc: Exception) -> None:
    """
    Map domain / IO exceptions to FastAPI HTTPException with helpful details.

    Always raises — never returns.
    """
    if isinstance(exc, HTTPException):
        raise exc

    if isinstance(exc, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Validation error: {exc}",
        ) from exc

    if isinstance(exc, (FileNotFoundError, RecordNotFoundError)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(exc, NotADirectoryError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(exc, (FaceDetectionError, AICoreError)):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI processing error: {exc}",
        ) from exc

    if isinstance(exc, ClusteringError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Clustering error: {exc}",
        ) from exc

    if isinstance(exc, DatabaseError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        ) from exc

    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(exc, PermissionError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {exc}",
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unexpected server error: {type(exc).__name__}: {exc}",
    ) from exc


# ---------------------------------------------------------------------------
# Background scan orchestration (asyncio task + thread pool for AI)
# ---------------------------------------------------------------------------

def _ingest_single_image_sync(
    services: AppServices,
    image_path: Path,
) -> dict[str, Any]:
    """
    Synchronous wrapper executed inside `asyncio.to_thread`.

    Runs DeepFace + SQLite writes without blocking the event loop.
    """
    return ingest_image_to_database(
        ai_engine=services.ai_engine,
        database=services.database,
        file_path=str(image_path),
        mark_processed=True,
    )


def _run_clustering_sync(services: AppServices) -> None:
    """Execute DBSCAN incremental clustering in a worker thread."""
    result = services.clustering.run_incremental_clustering()
    LOGGER.info(
        "Incremental clustering finished: loaded=%s clusters=%s auto=%s boundary=%s",
        result.total_unassigned_loaded,
        result.clusters_created,
        result.auto_assigned_faces,
        result.boundary_faces_queued,
    )


async def _execute_folder_scan_async(
    services: AppServices,
    folder: Path,
    image_files: list[Path],
) -> None:
    """
    Background coroutine: ingest every file, then cluster unassigned faces.

    Flow:
      1. For each image → `asyncio.to_thread(ingest)` (DeepFace is CPU-bound).
      2. Increment shared `ScanProgressState` after each successful file.
      3. After all files → `asyncio.to_thread(run_incremental_clustering)`.
      4. Always clear `is_active` in `finally`.
    """
    scan_state = services.scan_state
    LOGGER.info(
        "Background scan started: folder=%s files=%s",
        folder,
        len(image_files),
    )

    try:
        for index, image_path in enumerate(image_files, start=1):
            scan_state.set_current_file(str(image_path))
            LOGGER.info(
                "Ingesting file %s/%s: %s",
                index,
                len(image_files),
                image_path,
            )

            try:
                summary = await asyncio.to_thread(
                    _ingest_single_image_sync,
                    services,
                    image_path,
                )
                LOGGER.debug(
                    "Ingested photo_id=%s faces=%s path=%s",
                    summary.get("photo_id"),
                    summary.get("detection_count"),
                    image_path,
                )
            except Exception as exc:  # noqa: BLE001 — continue scan; record last error
                LOGGER.exception(
                    "Failed to ingest %s: %s: %s",
                    image_path,
                    type(exc).__name__,
                    exc,
                )
                scan_state.last_error = f"{image_path.name}: {exc}"
            finally:
                scan_state.increment_processed()

        LOGGER.info("Folder ingestion complete — starting incremental clustering")
        try:
            await asyncio.to_thread(_run_clustering_sync, services)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Incremental clustering failed: %s", exc)
            scan_state.last_error = f"Clustering failed: {exc}"

        LOGGER.info("Background scan finished successfully")

    except Exception as exc:  # noqa: BLE001 — catastrophic scan failure
        LOGGER.exception("Background scan aborted: %s", exc)
        scan_state.finish_scan(error_message=str(exc))
        return

    scan_state.finish_scan()


async def start_folder_scan(
    services: AppServices,
    folder_path: str,
) -> ScanFolderResponse:
    """
    Validate folder, discover images, and launch the asyncio background scan task.

    Raises:
        HTTPException: 409 if scan already active; 4xx/5xx on validation failures.
    """
    try:
        folder = resolve_and_validate_folder(folder_path)
        image_files = discover_image_files_recursively(folder)
    except Exception as exc:  # noqa: BLE001
        raise_http_exception_from_error(exc)

    total_files = len(image_files)

    async with services.scan_task_lock:
        if services.scan_state.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A folder scan is already in progress. "
                    "Poll GET /api/scan-status until is_active is false before starting another scan."
                ),
            )

        if services.scan_task is not None and not services.scan_task.done():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Previous scan task has not completed yet.",
            )

        acquired = services.scan_state.try_begin_scan(total_files=total_files)
        if not acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Scanner is already active (could not acquire scan lock).",
            )

        services.scan_task = asyncio.create_task(
            _execute_folder_scan_async(
                services=services,
                folder=folder,
                image_files=image_files,
            ),
            name=f"folder-scan:{folder.name}",
        )

        def _on_task_done(task: asyncio.Task[None]) -> None:
            try:
                task.result()
            except asyncio.CancelledError:
                LOGGER.warning("Folder scan task was cancelled")
                services.scan_state.finish_scan(error_message="Scan cancelled")
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Folder scan task crashed: %s", exc)
                services.scan_state.finish_scan(error_message=str(exc))

        services.scan_task.add_done_callback(_on_task_done)

    LOGGER.info(
        "Scan task created for %s (%s files)",
        folder,
        total_files,
    )
    return ScanFolderResponse(status="started", total_files=total_files)


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """
    Startup:
      - Initialize DatabaseManager + schema
      - Construct AICoreEngine + ClusteringEngine
      - Reset scan progress state

    Shutdown:
      - Cancel running scan task (if any)
      - Clear service reference
    """
    global _services

    LOGGER.info("Starting photo organizer sidecar (offline FastAPI)")

    database = DatabaseManager(db_path=DEFAULT_DATABASE_PATH)
    database.create_tables()

    ai_engine = AICoreEngine()
    clustering = ClusteringEngine(database=database)
    scan_state = ScanProgressState()

    _services = AppServices(
        database=database,
        ai_engine=ai_engine,
        clustering=clustering,
        scan_state=scan_state,
    )

    application.state.services = _services
    LOGGER.info(
        "Services ready: db=%s model=%s",
        database.db_path,
        ai_engine.model_name,
    )

    yield

    LOGGER.info("Shutting down photo organizer sidecar")

    if _services is not None:
        async with _services.scan_task_lock:
            if _services.scan_task is not None and not _services.scan_task.done():
                LOGGER.info("Cancelling active scan task")
                _services.scan_task.cancel()
                try:
                    await _services.scan_task
                except asyncio.CancelledError:
                    pass

    _services = None


def create_application() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    application = FastAPI(
        title="Photo Organizer Sidecar API",
        description=(
            "Local-only FastAPI sidecar for the offline photo organizer. "
            "Wraps database.py and ai_core.py for Tauri desktop integration."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_CORS_ORIGINS,
        allow_origin_regex=ALLOWED_CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )

    register_routes(application)
    return application


def register_routes(application: FastAPI) -> None:
    """Attach all /api routes to the FastAPI application."""

    @application.get(
        "/health",
        tags=["system"],
        summary="Health check for Tauri sidecar readiness probes",
    )
    async def health_check() -> dict[str, str]:
        """Simple liveness endpoint (no auth — localhost only)."""
        return {"status": "ok"}

    @application.post(
        f"{API_PREFIX}/scan-folder",
        response_model=ScanFolderResponse,
        responses={
            status.HTTP_409_CONFLICT: {"model": ErrorResponse},
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["scan"],
        summary="Start background folder ingestion + clustering",
    )
    async def post_scan_folder(request_body: ScanFolderRequest) -> ScanFolderResponse:
        """
        Recursively scan a local folder for JPG/PNG images.

        Heavy AI work runs in a background asyncio task using worker threads so this
        endpoint returns immediately with `{ "status": "started", "total_files": N }`.
        """
        try:
            services = get_services()
            return await start_folder_scan(
                services=services,
                folder_path=request_body.folder_path,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("POST /api/scan-folder failed")
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/scan-status",
        response_model=ScanStatusResponse,
        responses={
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["scan"],
        summary="Poll background scan progress",
    )
    async def get_scan_status() -> ScanStatusResponse:
        """
        Return live scanning metrics from the thread-safe global state object.

        Frontend should poll this endpoint while `is_active` is true.
        """
        try:
            services = get_services()
            snapshot = services.scan_state.snapshot()
            return ScanStatusResponse(
                processed=int(snapshot["processed"]),
                total=int(snapshot["total"]),
                is_active=bool(snapshot["is_active"]),
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /api/scan-status failed")
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/search",
        response_model=list[SearchResultItem],
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["search"],
        summary="Search photos containing ALL named people (intersection)",
    )
    async def get_search(
        names: str = Query(
            ...,
            description="Comma-separated person names (AND / intersection). Example: Magda,Łukasz",
            examples=["Magda,Łukasz"],
        ),
    ) -> list[SearchResultItem]:
        """
        Strict relational intersection search via DatabaseManager.get_photos_by_names.

        Only photos where EVERY listed person appears together are returned.
        """
        try:
            services = get_services()
            names_list = parse_comma_separated_names(names)
            photo_rows = services.database.get_photos_by_names(names_list)

            results = [
                SearchResultItem(photo_id=row.id, file_path=row.file_path)
                for row in photo_rows
            ]

            LOGGER.info(
                "Search names=%r → %s photo(s)",
                names_list,
                len(results),
            )
            return results

        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /api/search failed for names=%r", names)
            raise_http_exception_from_error(exc)


app = create_application()


# ---------------------------------------------------------------------------
# Local entrypoint (python main.py)
# ---------------------------------------------------------------------------

def run_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    log_level: str = "info",
) -> None:
    """
    Start Uvicorn programmatically (used by `python main.py`).

    Binds only to loopback interface — not exposed to the LAN.
    """
    LOGGER.info("Launching Uvicorn on http://%s:%s", host, port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=True,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    run_server(host=DEFAULT_HOST, port=DEFAULT_PORT)
