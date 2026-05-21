"""
FastAPI sidecar server for the offline photo organizer (Tauri desktop companion).

This process exposes a minimal local HTTP API on 127.0.0.1 so the desktop UI can:
  - Trigger recursive folder ingestion (face detection + DB persistence)
  - Poll scanning progress without blocking the UI thread
  - Search photos by person names (strict AND / intersection semantics)
  - List unnamed DBSCAN clusters and assign human identities
  - Merge duplicate person records and browse the people gallery
  - Serve cached JPEG thumbnails for gallery previews

Architecture:
  ┌─────────────┐   HTTP (localhost only)   ┌──────────────────────────────────┐
  │ Tauri / Web │ ◄────────────────────────► │ FastAPI (main.py)                │
  │   Frontend  │                            │  ├─ asyncio background scan task │
  └─────────────┘                            │  ├─ ThumbnailEngine (disk cache) │
                                             │  └─ thread pool for CPU-bound AI │
                                             └───────────┬──────────────────────┘
                                                         │
                         ┌───────────────────────────────┼───────────────────────────────┐
                         ▼                               ▼                               ▼
                 DatabaseManager                 AICoreEngine                  ClusteringEngine
                   (database.py)                  (ai_core.py)                    (ai_core.py)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Final, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator, model_validator

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

def _resolve_project_root() -> Path:
    """Repo root for caches and assets; honors PHOTO_AI_PROJECT_ROOT from Tauri sidecar."""
    override = os.environ.get("PHOTO_AI_PROJECT_ROOT", "").strip()
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        else:
            root = root.resolve()
        return root
    return Path(__file__).resolve().parent


PROJECT_ROOT: Final[Path] = _resolve_project_root()
DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 8000
DEFAULT_DATABASE_PATH: Final[str] = "organizer.db"

API_PREFIX: Final[str] = "/api"

DEV_SCAN_FOLDER: Final[str] = os.environ.get(
    "PHOTO_ORGANIZER_DEV_SCAN_FOLDER",
    r"C:\PhotoTest",
)

THUMBNAIL_CACHE_DIR: Final[Path] = PROJECT_ROOT / ".thumbnail_cache"
THUMBNAIL_DEFAULT_WIDTH: Final[int] = 300
THUMBNAIL_JPEG_QUALITY: Final[int] = 85
THUMBNAIL_MIN_EDGE: Final[int] = 1
THUMBNAIL_MAX_EDGE: Final[int] = 4096

SCAN_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".png"})
THUMBNAIL_SOURCE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
)

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

ALLOWED_CORS_ORIGIN_REGEX: Final[str] = (
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|"
    r"^tauri://localhost$|"
    r"^https://tauri\.localhost(:\d+)?$|"
    r"^asset://.*$"
)

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
    phase: str = Field(
        default="idle",
        description="Worker phase: idle | scanning | clustering.",
    )
    current_file: Optional[str] = Field(
        default=None,
        description="Basename of the image currently being ingested, if any.",
    )
    last_error: Optional[str] = Field(
        default=None,
        description="Most recent per-file or clustering error message, if any.",
    )


class SearchResultItem(BaseModel):
    """One photo returned by GET /api/search."""

    photo_id: int = Field(..., ge=1)
    file_path: str = Field(..., min_length=1)


class NoiseFaceItem(BaseModel):
    """One DBSCAN noise face for the Noise Inspector UI."""

    face_id: int = Field(..., ge=1)
    photo_id: int = Field(..., ge=1)
    thumbnail_url: str = Field(
        ...,
        min_length=1,
        description="Relative URL to a JPEG thumbnail for this face's source photo.",
    )


class IdentifyClusterRequest(BaseModel):
    """Body for POST /api/clusters/identify (cluster batch or single noise face)."""

    cluster_id: Optional[int] = Field(
        default=None,
        ge=0,
        description="DBSCAN cluster label (>= 0) for batch naming.",
    )
    face_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Single noise face id (cluster_id IS NULL) for Noise Inspector.",
    )
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Display name for a new profile (cluster or noise face).",
    )
    person_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Existing people.id when assigning a noise face to a named profile.",
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be empty or whitespace")
        return stripped

    @model_validator(mode="after")
    def validate_identify_target(self) -> IdentifyClusterRequest:
        has_cluster = self.cluster_id is not None
        has_face = self.face_id is not None

        if has_cluster == has_face:
            raise ValueError("Provide exactly one of cluster_id or face_id")

        if has_face:
            if self.person_id is not None:
                if self.name is not None:
                    raise ValueError(
                        "Provide person_id or name for face_id, not both"
                    )
                return self
            if self.name is None:
                raise ValueError("name is required when person_id is omitted for face_id")
            return self

        if self.person_id is not None:
            raise ValueError("person_id is only valid with face_id")
        if self.name is None:
            raise ValueError("name is required when cluster_id is provided")
        return self


class IdentifyClusterResponse(BaseModel):
    """Response for POST /api/clusters/identify."""

    status: str = Field(default="success", description="Operation outcome indicator.")
    person_id: int = Field(..., ge=1, description="Assigned or created people.id.")
    name: str = Field(..., min_length=1, description="Assigned display name.")
    cluster_id: Optional[int] = Field(
        default=None,
        ge=0,
        description="Cluster labeled when cluster_id was provided.",
    )
    face_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Noise face removed from the inspector when face_id was provided.",
    )


class DevResetLibraryResponse(BaseModel):
    """Response for POST /api/dev/reset-library."""

    status: str = Field(default="ok")
    removed: dict[str, int] = Field(default_factory=dict)


class DevSimulateScanRequest(BaseModel):
    """Optional body for POST /api/dev/simulate-scan."""

    folder_path: Optional[str] = Field(
        default=None,
        description="Override dev scan folder (default: PHOTO_ORGANIZER_DEV_SCAN_FOLDER).",
    )
    reset_first: bool = Field(
        default=True,
        description="When true, wipe photos/faces/people before starting the scan.",
    )


class MergePeopleRequest(BaseModel):
    """Body for POST /api/people/merge."""

    target_person_id: int = Field(
        ...,
        ge=1,
        description="Person record that survives the merge.",
    )
    source_person_id: int = Field(
        ...,
        ge=1,
        description="Person record removed after faces are reassigned.",
    )

    @model_validator(mode="after")
    def validate_distinct_person_ids(self) -> MergePeopleRequest:
        if self.target_person_id == self.source_person_id:
            raise ValueError("source_person_id must differ from target_person_id")
        return self


class MergePeopleResponse(BaseModel):
    """Response for POST /api/people/merge."""

    status: str = Field(default="success", description="Operation outcome indicator.")
    target_person_id: int = Field(..., ge=1)
    source_person_id: int = Field(..., ge=1)
    faces_moved: int = Field(
        ...,
        ge=0,
        description="Number of face rows reassigned from source to target.",
    )


class PersonSummaryItem(BaseModel):
    """One identified person returned by GET /api/people."""

    id: int = Field(..., ge=1, description="people.id primary key.")
    name: Optional[str] = Field(
        None,
        description="Assigned display name, or null if not yet named.",
    )
    face_count: int = Field(
        ...,
        ge=0,
        description="Total faces linked to this person.",
    )
    exemplar_photo_path: Optional[str] = Field(
        None,
        description="Filesystem path of one representative photo for UI thumbnails.",
    )


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
    phase: str = "idle"
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
                "phase": self.phase,
                "current_file": self.current_file,
                "last_error": self.last_error,
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
            self.phase = "scanning"
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

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.phase = phase

    def finish_scan(self, *, error_message: Optional[str] = None) -> None:
        with self._lock:
            self.is_active = False
            self.phase = "idle"
            self.current_file = None
            if error_message is not None:
                self.last_error = error_message


# ---------------------------------------------------------------------------
# Thumbnail engine — disk cache + Pillow downscaling
# ---------------------------------------------------------------------------


class ThumbnailEngine:
    """
    Local JPEG thumbnail cache with SHA-256 keys derived from source path and geometry.

    Cache directory: `.thumbnail_cache/` at project root (created on startup).
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir: Path = cache_dir.resolve()
        self._io_lock = threading.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info("ThumbnailEngine cache directory: %s", self.cache_dir)

    @staticmethod
    def _sanitize_cache_filename(digest_hex: str) -> str:
        """
        Build a safe on-disk filename from a hex digest (no path separators).
        """
        if not digest_hex or not all(character in "0123456789abcdef" for character in digest_hex):
            raise ValueError("thumbnail cache digest must be lowercase hexadecimal")
        return f"{digest_hex}.jpg"

    def build_cache_path(
        self,
        source_path: Path,
        *,
        width: int,
        height: Optional[int],
    ) -> Path:
        """
        Deterministic cache file path for a source image and requested geometry.
        """
        resolved_source = source_path.resolve()
        height_token = "auto" if height is None else str(height)
        fingerprint = f"{resolved_source}|w={width}|h={height_token}"
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        cache_name = self._sanitize_cache_filename(digest)
        cache_path = (self.cache_dir / cache_name).resolve()

        if self.cache_dir not in cache_path.parents and cache_path != self.cache_dir:
            raise ValueError("thumbnail cache path escaped cache directory")

        return cache_path

    @staticmethod
    def _open_image_unicode_safe(source_path: Path) -> Image.Image:
        """
        Open an image via an in-memory buffer (safe for Polish / Unicode paths on Windows).
        """
        if "\0" in str(source_path):
            raise ValueError("source path contains null byte")

        with source_path.open("rb") as handle:
            payload = handle.read()

        if not payload:
            raise ValueError(f"image file is empty: {source_path}")

        image = Image.open(BytesIO(payload))
        image.load()
        return image.convert("RGB")

    @staticmethod
    def _compute_thumbnail_size(
        original_width: int,
        original_height: int,
        *,
        target_width: int,
        target_height: Optional[int],
    ) -> tuple[int, int]:
        if original_width <= 0 or original_height <= 0:
            raise ValueError("source image has invalid dimensions")

        if target_height is None:
            scale = target_width / float(original_width)
            scaled_height = max(THUMBNAIL_MIN_EDGE, int(round(original_height * scale)))
            return target_width, scaled_height

        return target_width, target_height

    def _generate_thumbnail_file(
        self,
        source_path: Path,
        cache_path: Path,
        *,
        width: int,
        height: Optional[int],
    ) -> None:
        image = self._open_image_unicode_safe(source_path)
        try:
            target_size = self._compute_thumbnail_size(
                image.width,
                image.height,
                target_width=width,
                target_height=height,
            )
            resized = image.copy()
            resized.thumbnail(target_size, Image.Resampling.LANCZOS)

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            resized.save(
                cache_path,
                format="JPEG",
                quality=THUMBNAIL_JPEG_QUALITY,
                optimize=True,
            )
            LOGGER.debug(
                "Thumbnail generated source=%s cache=%s size=%sx%s",
                source_path,
                cache_path,
                resized.width,
                resized.height,
            )
        finally:
            image.close()

    def get_or_create_thumbnail(
        self,
        source_path: Path,
        *,
        width: int,
        height: Optional[int],
    ) -> Path:
        """
        Return a cached JPEG thumbnail path, generating the file on cache miss.

        Args:
            source_path: Validated absolute path to the original image on disk.
            width: Target maximum width in pixels.
            height: Optional maximum height; aspect ratio preserved when omitted.

        Returns:
            Absolute path to a JPEG file inside `.thumbnail_cache/`.
        """
        cache_path = self.build_cache_path(source_path, width=width, height=height)

        if cache_path.is_file():
            LOGGER.debug("Thumbnail cache hit: %s", cache_path.name)
            return cache_path

        with self._io_lock:
            if cache_path.is_file():
                LOGGER.debug("Thumbnail cache hit after lock: %s", cache_path.name)
                return cache_path

            LOGGER.info(
                "Thumbnail cache miss — generating %s (source=%s)",
                cache_path.name,
                source_path,
            )
            self._generate_thumbnail_file(
                source_path=source_path,
                cache_path=cache_path,
                width=width,
                height=height,
            )

        return cache_path


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
        thumbnail_engine: Disk-backed JPEG thumbnail cache.
        scan_state: Thread-safe progress tracker for folder scans.
        scan_task: Handle to the currently running asyncio.Task (if any).
        scan_task_lock: asyncio.Lock preventing concurrent scan task creation.
    """

    database: DatabaseManager
    ai_engine: AICoreEngine
    clustering: ClusteringEngine
    thumbnail_engine: ThumbnailEngine
    scan_state: ScanProgressState
    scan_task: Optional[asyncio.Task[None]] = None
    scan_task_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
    if "\0" in folder_path:
        raise ValueError("folder_path contains null byte")

    resolved = Path(folder_path).expanduser().resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"Directory does not exist: {resolved}")

    if not resolved.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {resolved}")

    return resolved


def resolve_photo_source_path(file_path: str) -> Path:
    """
    Resolve a database-stored photo path to a safe, existing file on disk.

    Path traversal hardening:
      - Reject null bytes and raw '..' path segments before resolution.
      - Require a regular file after `.resolve()`.
      - Restrict to known raster suffixes.

    Raises:
        ValueError: Unsafe or unsupported path.
        FileNotFoundError: Missing file on disk.
    """
    if not file_path or not str(file_path).strip():
        raise ValueError("photo file_path must not be empty")

    if "\0" in file_path:
        raise ValueError("photo file_path contains null byte")

    raw_path = Path(file_path).expanduser()
    if ".." in raw_path.parts:
        raise ValueError("photo file_path must not contain '..' segments")

    resolved = raw_path.resolve()

    if not resolved.is_file():
        raise FileNotFoundError(f"Photo file does not exist on disk: {resolved}")

    if resolved.suffix.lower() not in THUMBNAIL_SOURCE_SUFFIXES:
        raise ValueError(
            f"Unsupported photo extension {resolved.suffix!r}. "
            f"Supported: {sorted(THUMBNAIL_SOURCE_SUFFIXES)}"
        )

    return resolved


async def build_photo_thumbnail_file_response(
    services: AppServices,
    photo_id: int,
    width: int,
    height: Optional[int],
) -> FileResponse:
    """
    Build a cached JPEG FileResponse for a photo primary key.

    Shared by photo and cluster thumbnail routes.
    """
    if photo_id <= 0:
        raise ValueError("photo_id must be a positive integer")

    photo_row = services.database.get_photo_by_id(photo_id)
    if photo_row is None:
        raise RecordNotFoundError(f"No photo found with id={photo_id}")

    source_path = resolve_photo_source_path(photo_row.file_path)

    thumbnail_path = await asyncio.to_thread(
        services.thumbnail_engine.get_or_create_thumbnail,
        source_path,
        width=width,
        height=height,
    )

    if not thumbnail_path.is_file():
        raise FileNotFoundError(
            f"Thumbnail file missing after generation: {thumbnail_path}"
        )

    return FileResponse(
        path=str(thumbnail_path),
        media_type="image/jpeg",
        filename=thumbnail_path.name,
    )


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


def parse_comma_separated_person_ids(person_ids_parameter: str) -> list[int]:
    """
    Parse `?person_ids=1,2,3` into a list of positive integers for gallery filters.

    Raises:
        ValueError: If the parameter is empty, malformed, or contains non-positive ids.
    """
    if not person_ids_parameter or not person_ids_parameter.strip():
        return []

    person_ids: list[int] = []
    for segment in person_ids_parameter.split(","):
        stripped = segment.strip()
        if not stripped:
            continue
        try:
            parsed = int(stripped)
        except ValueError as exc:
            raise ValueError(
                f"person_ids must be comma-separated integers, invalid segment: {stripped!r}"
            ) from exc
        if parsed <= 0:
            raise ValueError(f"person_ids must be positive integers, got {parsed}")
        person_ids.append(parsed)

    return person_ids


def resolve_gallery_processed_filter(ai_status: str) -> Optional[bool]:
    """
    Map gallery `ai_status` query string to DatabaseManager.get_all_photos processed_only.

    Returns:
        None for all photos, True for processed-only, False for unprocessed-only.
    """
    normalized = ai_status.strip().lower()
    if normalized == "all":
        return None
    if normalized == "processed":
        return True
    if normalized == "unprocessed":
        return False
    raise ValueError(
        "ai_status must be one of: all, processed, unprocessed "
        f"(got {ai_status!r})"
    )


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

    if isinstance(exc, (ValueError, UnidentifiedImageError)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(exc, PermissionError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {exc}",
        ) from exc

    if isinstance(exc, OSError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Filesystem error: {exc}",
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unexpected server error: {type(exc).__name__}: {exc}",
    ) from exc


# ---------------------------------------------------------------------------
# Background scan orchestration (asyncio task + thread pool for AI)
# ---------------------------------------------------------------------------


def _resolve_ingestion_file_path(image_path: Path) -> str:
    """
    Canonical absolute path string stored in ``photos.file_path``.

    Must match ``ingest_image_to_database`` so skip checks hit the same row.
    """
    return str(image_path.expanduser().resolve())


def _photo_already_ingested(
    database: DatabaseManager,
    image_path: Path,
) -> bool:
    """
    Return True when the image is already in SQLite with ``processed = 1``.

    Unprocessed rows (``processed = 0``) are re-run through the AI pipeline.
    """
    resolved_path = _resolve_ingestion_file_path(image_path)
    existing_photo = database.get_photo_by_path(resolved_path)
    return existing_photo is not None and existing_photo.processed


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
        "Incremental clustering finished: loaded=%s clusters=%s auto=%s "
        "boundary=%s noise_discarded=%s",
        result.total_unassigned_loaded,
        result.clusters_created,
        result.auto_assigned_faces,
        result.boundary_faces_queued,
        result.noise_faces_discarded,
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
            resolved_path = _resolve_ingestion_file_path(image_path)

            if _photo_already_ingested(services.database, image_path):
                LOGGER.info(
                    "Skipping file %s/%s: %s (already ingested)",
                    index,
                    len(image_files),
                    resolved_path,
                )
                scan_state.increment_processed()
                continue

            LOGGER.info(
                "Ingesting file %s/%s: %s",
                index,
                len(image_files),
                resolved_path,
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
                    resolved_path,
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
        scan_state.set_phase("clustering")
        scan_state.set_current_file(None)
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
      - Construct AICoreEngine + ClusteringEngine + ThumbnailEngine
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
    thumbnail_engine = ThumbnailEngine(cache_dir=THUMBNAIL_CACHE_DIR)
    scan_state = ScanProgressState()

    _services = AppServices(
        database=database,
        ai_engine=ai_engine,
        clustering=clustering,
        thumbnail_engine=thumbnail_engine,
        scan_state=scan_state,
    )

    application.state.services = _services
    LOGGER.info(
        "Services ready: db=%s model=%s cache=%s",
        database.db_path,
        ai_engine.model_name,
        thumbnail_engine.cache_dir,
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
        try:
            return {"status": "ok"}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /health failed")
            raise_http_exception_from_error(exc)

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
                phase=str(snapshot.get("phase") or "idle"),
                current_file=snapshot.get("current_file"),
                last_error=snapshot.get("last_error"),
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

    @application.get(
        f"{API_PREFIX}/gallery",
        response_model=list[SearchResultItem],
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["gallery"],
        summary="List photos with optional person intersection and AI processed filters",
    )
    async def get_gallery(
        person_ids: str = Query(
            "",
            description="Comma-separated people.id values (AND / intersection). Empty = no person filter.",
            examples=["1,2"],
        ),
        ai_status: str = Query(
            "all",
            description="Filter by ingestion status: all | processed | unprocessed.",
            examples=["processed"],
        ),
    ) -> list[SearchResultItem]:
        """
        Gallery listing for the desktop UI.

        When person_ids is omitted or empty, returns all photos (subject to ai_status).
        When person_ids is set, returns photos where every listed person appears together.
        """
        try:
            services = get_services()
            processed_only = resolve_gallery_processed_filter(ai_status)
            person_id_list = parse_comma_separated_person_ids(person_ids)

            if person_id_list:
                photo_rows = services.database.get_photos_by_person_ids(person_id_list)
                if processed_only is True:
                    photo_rows = [row for row in photo_rows if row.processed]
                elif processed_only is False:
                    photo_rows = [row for row in photo_rows if not row.processed]
            else:
                photo_rows = services.database.get_all_photos(
                    processed_only=processed_only,
                )

            results = [
                SearchResultItem(photo_id=row.id, file_path=row.file_path)
                for row in photo_rows
            ]

            LOGGER.info(
                "GET /api/gallery person_ids=%r ai_status=%r → %s photo(s)",
                person_id_list,
                ai_status,
                len(results),
            )
            return results

        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "GET /api/gallery failed person_ids=%r ai_status=%r",
                person_ids,
                ai_status,
            )
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/clusters/unnamed",
        response_model=list[int],
        responses={
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["clusters"],
        summary="List DBSCAN cluster IDs awaiting a human-assigned name",
    )
    async def get_unnamed_clusters() -> list[int]:
        """
        Return cluster_id values persisted in SQLite that are not yet linked to `people`.

        DBSCAN noise faces (cluster_id NULL) are excluded by the database layer.
        """
        try:
            services = get_services()
            cluster_ids = services.database.get_unnamed_clusters()
            LOGGER.info("GET /api/clusters/unnamed → %s cluster(s)", len(cluster_ids))
            return cluster_ids
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /api/clusters/unnamed failed")
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/clusters/noise",
        response_model=list[NoiseFaceItem],
        responses={
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["clusters"],
        summary="List DBSCAN noise faces awaiting manual assignment",
    )
    async def get_noise_faces() -> list[NoiseFaceItem]:
        """
        Return unassigned faces with no named cluster (``cluster_id IS NULL`` or ``< 0``).

        Thumbnail URLs point at the parent photo preview for each detection.
        """
        try:
            services = get_services()
            noise_faces = services.database.get_noise_faces()
            results = [
                NoiseFaceItem(
                    face_id=face.id,
                    photo_id=face.photo_id,
                    thumbnail_url=(
                        f"{API_PREFIX}/faces/{face.id}/thumbnail"
                        f"?width={THUMBNAIL_DEFAULT_WIDTH}"
                    ),
                )
                for face in noise_faces
            ]
            LOGGER.info("GET /api/clusters/noise → %s face(s)", len(results))
            return results
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /api/clusters/noise failed")
            raise_http_exception_from_error(exc)

    @application.post(
        f"{API_PREFIX}/clusters/identify",
        response_model=IdentifyClusterResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["clusters"],
        summary="Assign a display name to an unnamed DBSCAN cluster",
    )
    async def post_identify_cluster(
        request_body: IdentifyClusterRequest,
    ) -> IdentifyClusterResponse:
        """
        Name a DBSCAN cluster or assign a single noise face to a new/existing person.
        """
        try:
            services = get_services()

            if request_body.face_id is not None:
                if request_body.person_id is not None:
                    person_name = services.database.assign_noise_face_to_person(
                        request_body.face_id,
                        request_body.person_id,
                    )
                    LOGGER.info(
                        "POST /api/clusters/identify face_id=%s → person_id=%s",
                        request_body.face_id,
                        request_body.person_id,
                    )
                    return IdentifyClusterResponse(
                        status="success",
                        person_id=request_body.person_id,
                        name=person_name,
                        face_id=request_body.face_id,
                    )

                assert request_body.name is not None
                person_id = services.database.assign_name_to_noise_face(
                    request_body.face_id,
                    request_body.name,
                )
                LOGGER.info(
                    "POST /api/clusters/identify face_id=%s name=%r → person_id=%s",
                    request_body.face_id,
                    request_body.name,
                    person_id,
                )
                return IdentifyClusterResponse(
                    status="success",
                    person_id=person_id,
                    name=request_body.name,
                    face_id=request_body.face_id,
                )

            assert request_body.cluster_id is not None
            assert request_body.name is not None
            person_id = services.database.assign_name_to_cluster(
                request_body.cluster_id,
                request_body.name,
            )
            LOGGER.info(
                "POST /api/clusters/identify cluster_id=%s name=%r → person_id=%s",
                request_body.cluster_id,
                request_body.name,
                person_id,
            )
            return IdentifyClusterResponse(
                status="success",
                person_id=person_id,
                name=request_body.name,
                cluster_id=request_body.cluster_id,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("POST /api/clusters/identify failed")
            raise_http_exception_from_error(exc)

    @application.post(
        f"{API_PREFIX}/people/merge",
        response_model=MergePeopleResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["people"],
        summary="Merge two person records into one identity",
    )
    async def post_merge_people(request_body: MergePeopleRequest) -> MergePeopleResponse:
        """
        Reassign all faces from `source_person_id` to `target_person_id` and delete the source.

        Uses DatabaseManager.merge_person_clusters(target_person_id, source_person_id).
        """
        try:
            services = get_services()
            faces_moved = services.database.merge_person_clusters(
                request_body.target_person_id,
                request_body.source_person_id,
            )
            LOGGER.info(
                "POST /api/people/merge target=%s source=%s faces_moved=%s",
                request_body.target_person_id,
                request_body.source_person_id,
                faces_moved,
            )
            return MergePeopleResponse(
                status="success",
                target_person_id=request_body.target_person_id,
                source_person_id=request_body.source_person_id,
                faces_moved=faces_moved,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "POST /api/people/merge failed target=%s source=%s",
                request_body.target_person_id,
                request_body.source_person_id,
            )
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/people",
        response_model=list[PersonSummaryItem],
        responses={
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["people"],
        summary="List all identified people with face counts and exemplar photos",
    )
    async def get_people() -> list[PersonSummaryItem]:
        """
        Return every person row with aggregate statistics for the UI gallery.

        Uses DatabaseManager.get_all_people_with_face_counts().
        """
        try:
            services = get_services()
            rows = services.database.get_all_people_with_face_counts()
            results = [
                PersonSummaryItem(
                    id=row.id,
                    name=row.name,
                    face_count=row.face_count,
                    exemplar_photo_path=row.exemplar_photo_path,
                )
                for row in rows
            ]
            LOGGER.info("GET /api/people → %s person(s)", len(results))
            return results
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /api/people failed")
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/people/{{person_id}}/thumbnail",
        response_class=FileResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["people"],
        summary="Serve a JPEG thumbnail for a person's exemplar photo",
    )
    async def get_person_thumbnail(
        person_id: int,
        width: int = Query(
            THUMBNAIL_DEFAULT_WIDTH,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Maximum thumbnail width in pixels.",
        ),
        height: Optional[int] = Query(
            None,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Optional maximum height; aspect ratio preserved when omitted.",
        ),
    ) -> FileResponse:
        """
        Resolve the lowest linked face for the person to a photo, then stream its thumbnail.
        """
        try:
            services = get_services()
            photo_id = services.database.get_exemplar_photo_id_for_person(person_id)
            response = await build_photo_thumbnail_file_response(
                services=services,
                photo_id=photo_id,
                width=width,
                height=height,
            )
            LOGGER.info(
                "GET /api/people/%s/thumbnail → photo_id=%s file=%s (width=%s height=%s)",
                person_id,
                photo_id,
                response.filename,
                width,
                height,
            )
            return response

        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "GET /api/people/%s/thumbnail failed (width=%s height=%s)",
                person_id,
                width,
                height,
            )
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/photos/{{photo_id}}/thumbnail",
        response_class=FileResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["photos"],
        summary="Serve a cached JPEG thumbnail for a photo",
    )
    async def get_photo_thumbnail(
        photo_id: int,
        width: int = Query(
            THUMBNAIL_DEFAULT_WIDTH,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Maximum thumbnail width in pixels.",
        ),
        height: Optional[int] = Query(
            None,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Optional maximum height; aspect ratio preserved when omitted.",
        ),
    ) -> FileResponse:
        """
        Stream a downscaled JPEG thumbnail from `.thumbnail_cache/`.

        On cache miss the image is resized with Pillow (LANCZOS) and stored as JPEG quality 85.
        """
        try:
            services = get_services()
            response = await build_photo_thumbnail_file_response(
                services=services,
                photo_id=photo_id,
                width=width,
                height=height,
            )
            LOGGER.info(
                "GET /api/photos/%s/thumbnail → %s (width=%s height=%s)",
                photo_id,
                response.filename,
                width,
                height,
            )
            return response

        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "GET /api/photos/%s/thumbnail failed (width=%s height=%s)",
                photo_id,
                width,
                height,
            )
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/photos/{{photo_id}}/file",
        response_class=FileResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["photos"],
        summary="Stream the original photo file from disk",
    )
    async def get_photo_file(photo_id: int) -> FileResponse:
        """
        Return the full-resolution source image for gallery lightbox viewing.
        """
        try:
            if photo_id <= 0:
                raise ValueError("photo_id must be a positive integer")

            services = get_services()
            photo_row = services.database.get_photo_by_id(photo_id)
            if photo_row is None:
                raise RecordNotFoundError(f"No photo found with id={photo_id}")

            source_path = resolve_photo_source_path(photo_row.file_path)
            guessed_type, _ = mimetypes.guess_type(str(source_path))
            media_type = guessed_type if guessed_type else "application/octet-stream"

            LOGGER.info(
                "GET /api/photos/%s/file → %s (%s)",
                photo_id,
                source_path.name,
                media_type,
            )
            return FileResponse(
                path=str(source_path),
                media_type=media_type,
                filename=source_path.name,
            )

        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /api/photos/%s/file failed", photo_id)
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/clusters/{{cluster_id}}/thumbnail",
        response_class=FileResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["clusters"],
        summary="Serve a JPEG thumbnail for an unnamed cluster exemplar photo",
    )
    async def get_cluster_thumbnail(
        cluster_id: int,
        width: int = Query(
            THUMBNAIL_DEFAULT_WIDTH,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Maximum thumbnail width in pixels.",
        ),
        height: Optional[int] = Query(
            None,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Optional maximum height; aspect ratio preserved when omitted.",
        ),
    ) -> FileResponse:
        """
        Resolve the lowest unassigned face in the cluster to a photo, then stream its thumbnail.
        """
        try:
            services = get_services()
            photo_id = services.database.get_exemplar_photo_id_for_cluster(cluster_id)
            response = await build_photo_thumbnail_file_response(
                services=services,
                photo_id=photo_id,
                width=width,
                height=height,
            )
            LOGGER.info(
                "GET /api/clusters/%s/thumbnail → photo_id=%s file=%s (width=%s height=%s)",
                cluster_id,
                photo_id,
                response.filename,
                width,
                height,
            )
            return response

        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "GET /api/clusters/%s/thumbnail failed (width=%s height=%s)",
                cluster_id,
                width,
                height,
            )
            raise_http_exception_from_error(exc)

    @application.get(
        f"{API_PREFIX}/faces/{{face_id}}/thumbnail",
        response_class=FileResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["faces"],
        summary="Serve a JPEG thumbnail for a face's source photo (Noise Inspector)",
    )
    async def get_face_thumbnail(
        face_id: int,
        width: int = Query(
            THUMBNAIL_DEFAULT_WIDTH,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Maximum thumbnail width in pixels.",
        ),
        height: Optional[int] = Query(
            None,
            ge=THUMBNAIL_MIN_EDGE,
            le=THUMBNAIL_MAX_EDGE,
            description="Optional maximum thumbnail height in pixels.",
        ),
    ) -> FileResponse:
        """Stream a downscaled JPEG for the photo that contains this face."""
        try:
            if face_id <= 0:
                raise ValueError("face_id must be a positive integer")

            services = get_services()
            face_row = services.database.get_face_by_id(face_id)
            if face_row is None:
                raise RecordNotFoundError(f"No face found with id={face_id}")

            response = await build_photo_thumbnail_file_response(
                services=services,
                photo_id=face_row.photo_id,
                width=width,
                height=height,
            )
            LOGGER.info(
                "GET /api/faces/%s/thumbnail → photo_id=%s file=%s",
                face_id,
                face_row.photo_id,
                response.filename,
            )
            return response

        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "GET /api/faces/%s/thumbnail failed (width=%s height=%s)",
                face_id,
                width,
                height,
            )
            raise_http_exception_from_error(exc)

    @application.post(
        f"{API_PREFIX}/dev/reset-library",
        response_model=DevResetLibraryResponse,
        tags=["dev"],
        summary="[Dev] Wipe photos, faces, and people tables",
    )
    async def dev_reset_library() -> DevResetLibraryResponse:
        """Developer helper: clear ingestion tables (localhost sidecar only)."""
        try:
            services = get_services()
            removed = services.database.clear_all_ingestion_data()
            LOGGER.warning("POST /api/dev/reset-library removed=%r", removed)
            return DevResetLibraryResponse(status="ok", removed=removed)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("POST /api/dev/reset-library failed")
            raise_http_exception_from_error(exc)

    @application.post(
        f"{API_PREFIX}/dev/simulate-scan",
        response_model=ScanFolderResponse,
        responses={
            status.HTTP_409_CONFLICT: {"model": ErrorResponse},
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["dev"],
        summary="[Dev] Optionally reset DB and scan PHOTO_ORGANIZER_DEV_SCAN_FOLDER",
    )
    async def dev_simulate_scan(
        request_body: DevSimulateScanRequest = DevSimulateScanRequest(),
    ) -> ScanFolderResponse:
        """Developer helper to exercise scan progress UI against a fixed folder."""
        try:
            services = get_services()
            if request_body.reset_first:
                services.database.clear_all_ingestion_data()

            folder_path = (
                request_body.folder_path.strip()
                if request_body.folder_path and request_body.folder_path.strip()
                else DEV_SCAN_FOLDER
            )
            return await start_folder_scan(services=services, folder_path=folder_path)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("POST /api/dev/simulate-scan failed")
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
    bind_host = os.environ.get("PHOTO_ORGANIZER_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    bind_port_raw = os.environ.get("PHOTO_ORGANIZER_PORT", str(DEFAULT_PORT)).strip()
    try:
        bind_port = int(bind_port_raw)
    except ValueError as exc:
        raise ValueError(
            f"PHOTO_ORGANIZER_PORT must be an integer, got {bind_port_raw!r}"
        ) from exc
    run_server(host=bind_host, port=bind_port)
