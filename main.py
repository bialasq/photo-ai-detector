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

import keras_legacy_env  # noqa: F401 — before ai_core / tensorflow (TD-5)

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Final, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from path_validation import validate_scan_path
from routes.v1 import router as v1_router
from schemas import (
    DevSimulateScanRequest,
    IdentifyClusterRequest,
    MergePeopleRequest,
    ScanFolderRequest,
)

from ai_core import (
    AICoreError,
    AICoreEngine,
    ClusteringEngine,
    ClusteringError,
    DEFAULT_DETECTION_BATCH_SIZE,
    FaceDetectionError,
    FaceInsertBuffer,
    ingest_image_to_database,
    verify_ai_runtime_dependencies,
)
from database import (
    DatabaseError,
    DatabaseManager,
    FaceRow,
    RecordNotFoundError,
    ValidationError,
    get_app_data_dir,
)
from gpu import initialize_gpu_if_requested
from errors import (
    ErrorCode,
    ErrorResponse,
    PathValidationError,
    raise_api_error,
    register_exception_handlers,
)
from logging_config import (
    DEV_MODE_ENV_VAR,
    hash_path_for_log,
    is_dev_mode,
    setup_logging,
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

LOOPBACK_BIND_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})

API_PREFIX: Final[str] = "/api"

DEV_SCAN_FOLDER: Final[str] = os.environ.get(
    "PHOTO_ORGANIZER_DEV_SCAN_FOLDER",
    r"C:\PhotoTest",
)

DEFAULT_MAX_SCAN_FILES: Final[int] = 20_000

# Directory names skipped during recursive scan (case-insensitive).
EXCLUDED_SCAN_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "__pycache__",
        "node_modules",
        "venv",
        ".venv",
        "target",
        "dist",
        "build",
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "$recycle.bin",
        "system volume information",
        "appdata",
        "application data",
        "msocache",
        "recovery",
        "perflogs",
    }
)

THUMBNAIL_CACHE_SUBDIR: Final[str] = "thumbnails"
THUMBNAIL_CACHE_INDEX_FILENAME: Final[str] = ".lru_index.json"
THUMBNAIL_CACHE_MB_ENV_VAR: Final[str] = "PHOTO_ORGANIZER_THUMB_CACHE_MB"
THUMBNAIL_CACHE_MAX_BYTES_DEFAULT: Final[int] = 500 * 1024 * 1024
LEGACY_THUMBNAIL_CACHE_DIR: Final[Path] = PROJECT_ROOT / ".thumbnail_cache"
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
# Pydantic response models (OpenAPI + validation)
# ---------------------------------------------------------------------------


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
        description="Worker phase: idle | scanning | clustering | cancelled.",
    )
    current_file: Optional[str] = Field(
        default=None,
        description="Basename of the image currently being ingested, if any.",
    )
    last_error: Optional[str] = Field(
        default=None,
        description="Most recent per-file or clustering error message, if any.",
    )
    cancelled: bool = Field(
        default=False,
        description="True when the user stopped the scan before completion.",
    )
    eta_seconds: float | None = Field(
        default=None,
        description=(
            "Estimated seconds until scan ingestion completes; null when unknown "
            "(idle, clustering, or no real files processed yet)."
        ),
    )


class SearchResultItem(BaseModel):
    """One photo returned by GET /api/search."""

    photo_id: int = Field(..., ge=1)
    file_path: str = Field(..., min_length=1)


class BoundingBoxModel(BaseModel):
    """Pixel bounding box stored in faces.bounding_box JSON (x, y, w, h)."""

    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., ge=1)
    h: int = Field(..., ge=1)


class FacePreviewItem(BaseModel):
    """Face metadata for cropped avatar thumbnails in the UI."""

    face_id: int = Field(..., ge=1)
    photo_id: int = Field(..., ge=1)
    bounding_box: BoundingBoxModel
    thumbnail_url: str = Field(
        ...,
        min_length=1,
        description="JPEG thumbnail URL (use ?crop=1 for face-centered crop).",
    )


class UnnamedClusterSummaryItem(BaseModel):
    """Unnamed DBSCAN cluster with exemplar face geometry for the People UI."""

    cluster_id: int = Field(..., ge=0)
    exemplar_face_id: int = Field(..., ge=1)
    photo_id: int = Field(..., ge=1)
    bounding_box: BoundingBoxModel
    face_count: int = Field(..., ge=1)
    thumbnail_url: str = Field(..., min_length=1)


class NoiseFaceItem(BaseModel):
    """One DBSCAN noise face for the Noise Inspector UI."""

    face_id: int = Field(..., ge=1)
    photo_id: int = Field(..., ge=1)
    bounding_box: BoundingBoxModel
    thumbnail_url: str = Field(
        ...,
        min_length=1,
        description="Relative URL to a JPEG thumbnail for this face's source photo.",
    )


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
    exemplar_face_id: Optional[int] = Field(
        None,
        ge=1,
        description="Representative face id for cropped thumbnails.",
    )
    bounding_box: Optional[BoundingBoxModel] = Field(
        None,
        description="Bounding box of the exemplar face in pixel coordinates.",
    )


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
    cancelled: bool = False
    last_error: Optional[str] = None
    current_file: Optional[str] = None
    started_at: float | None = None
    actually_processed: int = 0
    first_real_process_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @staticmethod
    def _compute_eta_seconds(
        *,
        is_active: bool,
        phase: str,
        processed: int,
        total: int,
        actually_processed: int,
        first_real_process_at: float | None,
    ) -> float | None:
        """
        Estimate seconds until ingestion completes.

        ETA is intentionally conservative: it may overestimate when skipped and
        new files are interleaved, because underestimating progress is worse UX
        than a slightly long wait.
        """
        if not is_active or phase != "scanning":
            return None

        if processed == 0 or actually_processed == 0 or first_real_process_at is None:
            return None

        remaining = total - processed
        if remaining <= 0:
            return 0.0

        elapsed = time.monotonic() - first_real_process_at
        if elapsed <= 0:
            return None

        rate = elapsed / actually_processed
        return round(rate * remaining, 1)

    def snapshot(self) -> dict[str, Any]:
        """Return a consistent copy for API responses."""
        with self._lock:
            eta_seconds = self._compute_eta_seconds(
                is_active=self.is_active,
                phase=self.phase,
                processed=self.processed,
                total=self.total,
                actually_processed=self.actually_processed,
                first_real_process_at=self.first_real_process_at,
            )
            return {
                "processed": self.processed,
                "total": self.total,
                "is_active": self.is_active,
                "phase": self.phase,
                "cancelled": self.cancelled,
                "current_file": self.current_file,
                "last_error": self.last_error,
                "eta_seconds": eta_seconds,
            }

    def is_cancelled(self) -> bool:
        with self._lock:
            return self.cancelled

    def request_cancel(self) -> None:
        with self._lock:
            self.cancelled = True

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
            self.cancelled = False
            self.last_error = None
            self.current_file = None
            self.started_at = time.monotonic()
            self.actually_processed = 0
            self.first_real_process_at = None
            return True

    def set_total(self, total_files: int) -> None:
        with self._lock:
            self.total = total_files

    def increment_processed(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            self.processed += count

    def increment_actually_processed(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            if self.first_real_process_at is None:
                self.first_real_process_at = time.monotonic()
            self.actually_processed += count

    def set_current_file(self, file_path: Optional[str]) -> None:
        with self._lock:
            self.current_file = file_path

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.phase = phase

    def finish_scan(self, *, error_message: Optional[str] = None, cancelled: bool = False) -> None:
        with self._lock:
            self.is_active = False
            self.phase = "cancelled" if cancelled else "idle"
            self.cancelled = cancelled
            self.current_file = None
            if error_message is not None:
                self.last_error = error_message


def resolve_thumbnail_cache_dir() -> Path:
    """Writable thumbnail cache under application data (``…/thumbnails``)."""
    return get_app_data_dir() / THUMBNAIL_CACHE_SUBDIR


def _thumbnail_cache_max_bytes() -> int:
    raw = os.environ.get(THUMBNAIL_CACHE_MB_ENV_VAR, "").strip()
    if not raw:
        return THUMBNAIL_CACHE_MAX_BYTES_DEFAULT
    try:
        megabytes = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{THUMBNAIL_CACHE_MB_ENV_VAR} must be a positive integer"
        ) from exc
    if megabytes <= 0:
        raise ValueError(f"{THUMBNAIL_CACHE_MB_ENV_VAR} must be a positive integer")
    return megabytes * 1024 * 1024


def _remove_legacy_thumbnail_cache_dir() -> None:
    if LEGACY_THUMBNAIL_CACHE_DIR.is_dir():
        LOGGER.info(
            "Removing legacy project-root thumbnail cache: %s",
            LEGACY_THUMBNAIL_CACHE_DIR,
        )
        shutil.rmtree(LEGACY_THUMBNAIL_CACHE_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Thumbnail engine — disk cache + Pillow downscaling
# ---------------------------------------------------------------------------


class ThumbnailEngine:
    """
    Local JPEG thumbnail cache with SHA-256 keys derived from source path and geometry.

    Cache directory: ``%AppData%/com.photo.organizer/thumbnails`` (LRU eviction, 500 MB default).
    """

    def __init__(self, cache_dir: Path, *, max_bytes: int | None = None) -> None:
        self.cache_dir: Path = cache_dir.resolve()
        self._max_bytes = max_bytes if max_bytes is not None else _thumbnail_cache_max_bytes()
        self._io_lock = threading.Lock()
        self._index_path = self.cache_dir / THUMBNAIL_CACHE_INDEX_FILENAME
        self._access_index: dict[str, dict[str, float | int]] = {}
        self._total_bytes = 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_or_rebuild_index()
        LOGGER.info(
            "ThumbnailEngine cache directory: %s (max_bytes=%s entries=%s)",
            self.cache_dir,
            self._max_bytes,
            len(self._access_index),
        )

    def _load_or_rebuild_index(self) -> None:
        if self._index_path.is_file():
            try:
                payload = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("Corrupt thumbnail LRU index, rebuilding from disk: %s", exc)
                self._rebuild_index_from_disk()
                return
            entries = payload.get("entries")
            if not isinstance(entries, dict):
                LOGGER.warning("Invalid thumbnail LRU index shape, rebuilding from disk")
                self._rebuild_index_from_disk()
                return
            self._access_index = {
                str(name): {
                    "size_bytes": int(entry["size_bytes"]),
                    "last_access": float(entry["last_access"]),
                }
                for name, entry in entries.items()
                if isinstance(entry, dict)
                and "size_bytes" in entry
                and "last_access" in entry
            }
            self._sync_index_with_disk()
            return
        self._rebuild_index_from_disk()

    def _rebuild_index_from_disk(self) -> None:
        self._access_index = {}
        self._total_bytes = 0
        for cache_path in sorted(self.cache_dir.glob("*.jpg")):
            try:
                stat_result = cache_path.stat()
            except OSError:
                continue
            self._access_index[cache_path.name] = {
                "size_bytes": stat_result.st_size,
                "last_access": stat_result.st_mtime,
            }
        self._recalculate_total_bytes()

    def _sync_index_with_disk(self) -> None:
        on_disk = {path.name for path in self.cache_dir.glob("*.jpg")}
        for name in list(self._access_index):
            if name not in on_disk:
                del self._access_index[name]
        for cache_path in self.cache_dir.glob("*.jpg"):
            if cache_path.name in self._access_index:
                continue
            try:
                stat_result = cache_path.stat()
            except OSError:
                continue
            self._access_index[cache_path.name] = {
                "size_bytes": stat_result.st_size,
                "last_access": stat_result.st_mtime,
            }
        self._recalculate_total_bytes()

    def _recalculate_total_bytes(self) -> None:
        self._total_bytes = sum(
            int(entry["size_bytes"]) for entry in self._access_index.values()
        )

    def _touch_access_locked(self, cache_name: str) -> None:
        """Update ``last_access`` for a cache hit; caller must hold ``_io_lock``."""
        entry = self._access_index.get(cache_name)
        if entry is not None:
            entry["last_access"] = time.time()
            return
        cache_path = self.cache_dir / cache_name
        if not cache_path.is_file():
            return
        try:
            stat_result = cache_path.stat()
        except OSError:
            return
        self._access_index[cache_name] = {
            "size_bytes": stat_result.st_size,
            "last_access": time.time(),
        }
        self._total_bytes += stat_result.st_size

    def _touch_cache_hit(self, cache_path: Path) -> None:
        with self._io_lock:
            self._touch_access_locked(cache_path.name)

    def _record_cache_write(self, cache_path: Path) -> None:
        """Register a new or replaced cache file and evict if over limit; caller holds lock."""
        try:
            size = cache_path.stat().st_size
        except OSError as exc:
            LOGGER.warning("Cannot stat new thumbnail %s: %s", cache_path.name, exc)
            return
        name = cache_path.name
        previous = self._access_index.pop(name, None)
        if previous is not None:
            self._total_bytes -= int(previous["size_bytes"])
        now = time.time()
        self._access_index[name] = {"size_bytes": size, "last_access": now}
        self._total_bytes += size
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        """Remove least-recently-used entries until within limit; caller holds ``_io_lock``."""
        if self._total_bytes <= self._max_bytes:
            return
        evicted = False
        for name, entry in sorted(
            self._access_index.items(),
            key=lambda item: float(item[1]["last_access"]),
        ):
            if self._total_bytes <= self._max_bytes:
                break
            cache_path = self.cache_dir / name
            try:
                cache_path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning("Failed to evict thumbnail %s: %s", name, exc)
                continue
            self._total_bytes -= int(entry["size_bytes"])
            del self._access_index[name]
            evicted = True
            LOGGER.debug("Evicted thumbnail cache entry: %s", name)
        if evicted:
            self._persist_index()

    def _persist_index(self) -> None:
        payload = {"version": 1, "entries": self._access_index}
        temp_path = self._index_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )
        temp_path.replace(self._index_path)

    def close(self) -> None:
        """Flush the in-memory LRU index to disk (lifespan shutdown)."""
        with self._io_lock:
            self._persist_index()

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

    def build_face_cache_path(
        self,
        source_path: Path,
        bounding_box: dict[str, int],
        *,
        width: int,
    ) -> Path:
        """Deterministic cache path for a face crop derived from ``bounding_box``."""
        resolved_source = source_path.resolve()
        bbox_token = (
            f"x={bounding_box['x']}|y={bounding_box['y']}|"
            f"w={bounding_box['w']}|h={bounding_box['h']}"
        )
        fingerprint = f"{resolved_source}|face|{bbox_token}|w={width}"
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        cache_name = self._sanitize_cache_filename(digest)
        cache_path = (self.cache_dir / cache_name).resolve()

        if self.cache_dir not in cache_path.parents and cache_path != self.cache_dir:
            raise ValueError("face thumbnail cache path escaped cache directory")

        return cache_path

    @staticmethod
    def _crop_face_region(
        image: Image.Image,
        bounding_box: dict[str, int],
        *,
        padding_ratio: float = 0.35,
    ) -> Image.Image:
        """Crop a square-ish region around a face with proportional padding."""
        image_width, image_height = image.size
        x = int(bounding_box["x"])
        y = int(bounding_box["y"])
        w = max(1, int(bounding_box["w"]))
        h = max(1, int(bounding_box["h"]))

        pad_x = int(w * padding_ratio)
        pad_y = int(h * padding_ratio)
        left = max(0, x - pad_x)
        top = max(0, y - pad_y)
        right = min(image_width, x + w + pad_x)
        bottom = min(image_height, y + h + pad_y)

        if right <= left or bottom <= top:
            raise ValueError("invalid face bounding box for crop")

        return image.crop((left, top, right, bottom))

    def _generate_face_thumbnail_file(
        self,
        source_path: Path,
        cache_path: Path,
        bounding_box: dict[str, int],
        *,
        width: int,
    ) -> None:
        image = self._open_image_unicode_safe(source_path)
        try:
            cropped = self._crop_face_region(image, bounding_box)
            target_size = self._compute_thumbnail_size(
                cropped.width,
                cropped.height,
                target_width=width,
                target_height=None,
            )
            resized = cropped.copy()
            resized.thumbnail(target_size, Image.Resampling.LANCZOS)

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            resized.save(
                cache_path,
                format="JPEG",
                quality=THUMBNAIL_JPEG_QUALITY,
                optimize=True,
            )
            LOGGER.debug(
                "Face thumbnail generated source=%s cache=%s bbox=%s",
                source_path,
                cache_path,
                bounding_box,
            )
        finally:
            image.close()

    def get_or_create_face_thumbnail(
        self,
        source_path: Path,
        bounding_box: dict[str, int],
        *,
        width: int,
    ) -> Path:
        """Return a cached JPEG cropped to the supplied face bounding box."""
        cache_path = self.build_face_cache_path(
            source_path,
            bounding_box,
            width=width,
        )

        if cache_path.is_file():
            self._touch_cache_hit(cache_path)
            return cache_path

        with self._io_lock:
            if cache_path.is_file():
                self._touch_access_locked(cache_path.name)
                return cache_path

            self._generate_face_thumbnail_file(
                source_path=source_path,
                cache_path=cache_path,
                bounding_box=bounding_box,
                width=width,
            )
            self._record_cache_write(cache_path)

        return cache_path

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
            Absolute path to a JPEG file inside the AppData thumbnail cache.
        """
        cache_path = self.build_cache_path(source_path, width=width, height=height)

        if cache_path.is_file():
            LOGGER.debug("Thumbnail cache hit: %s", cache_path.name)
            self._touch_cache_hit(cache_path)
            return cache_path

        with self._io_lock:
            if cache_path.is_file():
                LOGGER.debug("Thumbnail cache hit after lock: %s", cache_path.name)
                self._touch_access_locked(cache_path.name)
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
            self._record_cache_write(cache_path)

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
    vector_store: Any = None
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


def _max_scan_files() -> int:
    raw = os.environ.get("PHOTO_ORGANIZER_MAX_SCAN_FILES", "").strip()
    if not raw:
        return DEFAULT_MAX_SCAN_FILES
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError(
            "PHOTO_ORGANIZER_MAX_SCAN_FILES must be a positive integer"
        ) from exc
    if limit <= 0:
        raise ValueError("PHOTO_ORGANIZER_MAX_SCAN_FILES must be a positive integer")
    return limit


def _is_blocked_scan_directory(directory: Path) -> bool:
    """Return True when `directory` must not be traversed during ingestion."""
    name = directory.name.lower()
    if name in EXCLUDED_SCAN_DIR_NAMES:
        return True
    if name.startswith("$"):
        return True
    return False


def _is_within_scan_root(resolved: Path, scan_root: Path) -> bool:
    """Return True when `resolved` equals `scan_root` or lies under it."""
    if os.name == "nt":
        resolved_cmp = Path(os.path.normcase(str(resolved)))
        root_cmp = Path(os.path.normcase(str(scan_root)))
        try:
            return resolved_cmp == root_cmp or resolved_cmp.is_relative_to(root_cmp)
        except ValueError:
            return False
    try:
        return resolved == scan_root or resolved.is_relative_to(scan_root)
    except ValueError:
        return False


def resolve_and_validate_folder(
    folder_path: str,
    *,
    allow_whole_disk: bool = False,
) -> Path:
    """
    Resolve `folder_path` to an absolute directory on disk (task 1.2.3).

    Raises:
        PathValidationError: Path fails security or shape checks.
    """
    return validate_scan_path(folder_path, allow_whole_disk=allow_whole_disk)


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


async def build_face_thumbnail_file_response(
    services: AppServices,
    face_row: FaceRow,
    *,
    width: int,
    height: Optional[int],
) -> FileResponse:
    """Build a face-centered JPEG thumbnail using the stored bounding box."""
    if width <= 0:
        raise ValueError("width must be a positive integer")
    if height is not None and height <= 0:
        raise ValueError("height must be a positive integer when provided")

    photo_row = services.database.get_photo_by_id(face_row.photo_id)
    if photo_row is None:
        raise RecordNotFoundError(f"No photo found with id={face_row.photo_id}")

    source_path = resolve_photo_source_path(photo_row.file_path)

    thumbnail_path = await asyncio.to_thread(
        services.thumbnail_engine.get_or_create_face_thumbnail,
        source_path,
        face_row.bounding_box,
        width=width,
    )

    if not thumbnail_path.is_file():
        raise FileNotFoundError(
            f"Face thumbnail file missing after generation: {thumbnail_path}"
        )

    return FileResponse(
        path=str(thumbnail_path),
        media_type="image/jpeg",
        filename=thumbnail_path.name,
    )


def discover_image_files_recursively(folder: Path) -> list[Path]:
    """
    Recursively collect .jpg / .jpeg / .png files under `folder`.

    Skips known system / dependency directories and enforces ``PHOTO_ORGANIZER_MAX_SCAN_FILES``.

    Returns:
        Sorted list of absolute file paths (stable ingestion order).

    Raises:
        ValueError: When the discovered file count exceeds the configured limit.
    """
    scan_root = folder.resolve()
    discovered: list[Path] = []
    max_files = _max_scan_files()

    for dirpath, dirnames, filenames in os.walk(
        scan_root, topdown=True, followlinks=False
    ):
        current_dir = Path(dirpath)
        allowed_dirnames: list[str] = []
        for name in dirnames:
            child = current_dir / name
            if _is_blocked_scan_directory(child):
                continue
            if not _is_within_scan_root(child.resolve(), scan_root):
                continue
            allowed_dirnames.append(name)
        dirnames[:] = allowed_dirnames

        for filename in filenames:
            candidate = (current_dir / filename).resolve()
            if candidate.suffix.lower() not in SCAN_IMAGE_SUFFIXES:
                continue
            if not _is_within_scan_root(candidate, scan_root):
                LOGGER.warning(
                    "Skipping image file outside scan root",
                    extra={
                        "ctx": {
                            "event": "scan.file.outside_root",
                            "path_hash": hash_path_for_log(candidate),
                        }
                    },
                )
                continue
            discovered.append(candidate)
            if len(discovered) > max_files:
                raise ValueError(
                    f"Folder contains more than {max_files} images "
                    f"(limit PHOTO_ORGANIZER_MAX_SCAN_FILES). "
                    f"Choose a narrower folder or raise the limit."
                )

    discovered.sort(key=lambda path: str(path).lower())
    LOGGER.info(
        "Discovered image files for scan",
        extra={
            "ctx": {
                "event": "scan.discover",
                "count": len(discovered),
                "folder_hash": hash_path_for_log(folder),
                "max_files": max_files,
            }
        },
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


def resolve_gallery_ai_filter(ai_status: str) -> tuple[Optional[bool], bool]:
    """
    Map gallery ``ai_status`` to database filters.

    Returns:
        (processed_only, faceless_only) — at most one restrictive flag is active.
    """
    normalized = ai_status.strip().lower()
    if normalized == "all":
        return None, False
    if normalized == "processed":
        return True, False
    if normalized == "unprocessed":
        return False, False
    if normalized == "faceless":
        return None, True
    raise ValueError(
        "ai_status must be one of: all, processed, unprocessed, faceless "
        f"(got {ai_status!r})"
    )


def bounding_box_to_model(bounding_box: dict[str, int]) -> BoundingBoxModel:
    """Convert a validated database bounding box dict to an API model."""
    return BoundingBoxModel(
        x=int(bounding_box["x"]),
        y=int(bounding_box["y"]),
        w=int(bounding_box["w"]),
        h=int(bounding_box["h"]),
    )


def face_preview_item_from_row(face_row: FaceRow, *, width: int) -> FacePreviewItem:
    """Build a FacePreviewItem with a crop thumbnail URL for a face row."""
    return FacePreviewItem(
        face_id=face_row.id,
        photo_id=face_row.photo_id,
        bounding_box=bounding_box_to_model(face_row.bounding_box),
        thumbnail_url=(
            f"{API_PREFIX}/faces/{face_row.id}/thumbnail"
            f"?crop=1&width={width}"
        ),
    )


# ---------------------------------------------------------------------------
# Exception → HTTP mapping
# ---------------------------------------------------------------------------


def raise_http_exception_from_error(exc: Exception) -> None:
    """
    Map domain / IO exceptions to ``HTTPException`` with ``ErrorResponse`` detail.

    Always raises — never returns.
    """
    if isinstance(exc, HTTPException):
        raise exc

    if isinstance(exc, PathValidationError):
        raise_api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=exc.message,
            code=ErrorCode.PATH_INVALID,
            details=exc.details or None,
        )

    if isinstance(exc, ValidationError):
        raise_api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=str(exc),
            code=ErrorCode.VALIDATION_ERROR,
        )

    if isinstance(exc, (FileNotFoundError, RecordNotFoundError)):
        raise_api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            error=str(exc),
            code=ErrorCode.NOT_FOUND,
        )

    if isinstance(exc, NotADirectoryError):
        raise_api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=str(exc),
            code=ErrorCode.PATH_INVALID,
        )

    if isinstance(exc, (FaceDetectionError, AICoreError)):
        raise_api_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=str(exc),
            code=ErrorCode.AI_CORE_FAILURE,
            hint=getattr(exc, "hint", None),
            details=getattr(exc, "context", None) or None,
        )

    if isinstance(exc, ClusteringError):
        raise_api_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=str(exc),
            code=ErrorCode.AI_CORE_FAILURE,
            hint=getattr(exc, "hint", None),
            details=getattr(exc, "context", None) or None,
        )

    if isinstance(exc, DatabaseError):
        raise_api_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=str(exc),
            code=ErrorCode.INTERNAL_ERROR,
        )

    if isinstance(exc, (ValueError, UnidentifiedImageError)):
        raise_api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error=str(exc),
            code=ErrorCode.VALIDATION_ERROR,
        )

    if isinstance(exc, PermissionError):
        raise_api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            error=f"Permission denied: {exc}",
            code=ErrorCode.INTERNAL_ERROR,
        )

    if isinstance(exc, OSError):
        raise_api_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=f"Filesystem error: {exc}",
            code=ErrorCode.INTERNAL_ERROR,
        )

    raise_api_error(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error=f"Unexpected server error: {type(exc).__name__}: {exc}",
        code=ErrorCode.INTERNAL_ERROR,
    )


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
    face_buffer: FaceInsertBuffer | None = None,
    detections: list[dict[str, Any]] | None = None,
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
        face_buffer=face_buffer,
        detections=detections,
    )


def _ingest_detection_batch_sync(
    services: AppServices,
    image_paths: list[Path],
    face_buffer: FaceInsertBuffer,
) -> None:
    """Run batched detection then persist each image (task 2.1.1)."""
    if not image_paths:
        return

    detection_results = services.ai_engine.detect_faces_batch_with_retry(
        image_paths,
        batch_size=DEFAULT_DETECTION_BATCH_SIZE,
    )
    for image_path, detections in zip(image_paths, detection_results, strict=True):
        if _photo_already_ingested(services.database, image_path):
            continue
        ingest_image_to_database(
            ai_engine=services.ai_engine,
            database=services.database,
            file_path=str(image_path),
            mark_processed=True,
            face_buffer=face_buffer,
            detections=detections,
        )


def _persist_vector_store(services: AppServices) -> None:
    """Rebuild and save the FAISS index after ingestion (tasks 2.2.1 / 2.2.2)."""
    if services.vector_store is None:
        return
    try:
        from vector_store import FaceVectorStore, get_faiss_index_path

        services.vector_store = FaceVectorStore.rebuild_from_database(services.database)
        index_path = get_faiss_index_path(get_app_data_dir())
        services.vector_store.save(index_path)
    except Exception as exc:  # noqa: BLE001 — index persistence must not fail scans
        LOGGER.warning("Failed to persist FAISS index: %s", exc)


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
    face_buffer = FaceInsertBuffer(services.database)
    LOGGER.info(
        "Background scan started",
        extra={
            "ctx": {
                "event": "scan.start",
                "file_count": len(image_files),
                "folder_hash": hash_path_for_log(folder),
            }
        },
    )

    try:
        pending_batch: list[Path] = []

        for index, image_path in enumerate(image_files, start=1):
            if scan_state.is_cancelled():
                LOGGER.info(
                    "Scan cancelled by user",
                    extra={"ctx": {"event": "scan.cancelled"}},
                )
                break

            scan_state.set_current_file(str(image_path))
            resolved_path = _resolve_ingestion_file_path(image_path)

            if _photo_already_ingested(services.database, image_path):
                LOGGER.info(
                    "Skipping already-ingested file",
                    extra={
                        "ctx": {
                            "event": "scan.file.skip",
                            "index": index,
                            "total": len(image_files),
                            "path_hash": hash_path_for_log(resolved_path),
                        }
                    },
                )
                scan_state.increment_processed()
                continue

            pending_batch.append(image_path)
            if len(pending_batch) < DEFAULT_DETECTION_BATCH_SIZE and index < len(image_files):
                continue

            batch_paths = list(pending_batch)
            pending_batch.clear()

            if scan_state.is_cancelled():
                LOGGER.info(
                    "Scan cancelled before batch processing",
                    extra={"ctx": {"event": "scan.cancelled"}},
                )
                break

            try:
                await asyncio.to_thread(
                    _ingest_detection_batch_sync,
                    services,
                    batch_paths,
                    face_buffer,
                )
                for batch_index, batch_path in enumerate(batch_paths):
                    LOGGER.info(
                        "Scan file processed",
                        extra={
                            "ctx": {
                                "event": "scan.file.done",
                                "index": index - len(batch_paths) + batch_index + 1,
                                "total": len(image_files),
                                "path_hash": hash_path_for_log(
                                    _resolve_ingestion_file_path(batch_path)
                                ),
                            }
                        },
                    )
            except Exception as exc:  # noqa: BLE001 — continue scan; record last error
                LOGGER.exception(
                    "Failed to ingest batch ending at %s: %s: %s",
                    hash_path_for_log(image_path),
                    type(exc).__name__,
                    exc,
                )
                scan_state.last_error = f"{image_path.name}: {exc}"
            finally:
                scan_state.increment_processed(len(batch_paths))
                scan_state.increment_actually_processed(len(batch_paths))

        if pending_batch and not scan_state.is_cancelled():
            try:
                await asyncio.to_thread(
                    _ingest_detection_batch_sync,
                    services,
                    pending_batch,
                    face_buffer,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Failed to ingest final batch: %s", exc)
                scan_state.last_error = str(exc)
            finally:
                scan_state.increment_processed(len(pending_batch))
                scan_state.increment_actually_processed(len(pending_batch))

        await asyncio.to_thread(face_buffer.flush)

        if scan_state.is_cancelled():
            snapshot = scan_state.snapshot()
            LOGGER.info(
                "Background scan cancelled (partial progress preserved)",
                extra={
                    "ctx": {
                        "event": "scan.cancelled.complete",
                        "processed": snapshot["processed"],
                        "total": snapshot["total"],
                    }
                },
            )
            scan_state.finish_scan(cancelled=True)
            return

        _persist_vector_store(services)

        LOGGER.info(
            "Folder ingestion complete — starting incremental clustering",
            extra={"ctx": {"event": "scan.ingest.complete"}},
        )
        scan_state.set_phase("clustering")
        scan_state.set_current_file(None)
        try:
            await asyncio.to_thread(_run_clustering_sync, services)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Incremental clustering failed: %s", exc)
            scan_state.last_error = f"Clustering failed: {exc}"

        snapshot = scan_state.snapshot()
        LOGGER.info(
            "Background scan finished successfully",
            extra={
                "ctx": {
                    "event": "scan.complete",
                    "processed": snapshot["processed"],
                    "total": snapshot["total"],
                }
            },
        )

    except Exception as exc:  # noqa: BLE001 — catastrophic scan failure
        LOGGER.exception("Background scan aborted: %s", exc)
        scan_state.finish_scan(error_message=str(exc))
        return

    scan_state.finish_scan()


async def start_folder_scan(
    services: AppServices,
    folder_path: str,
    *,
    allow_whole_disk: bool = False,
) -> ScanFolderResponse:
    """
    Validate folder, discover images, and launch the asyncio background scan task.

    Raises:
        HTTPException: 409 if scan already active; 4xx/5xx on validation failures.
    """
    try:
        folder = resolve_and_validate_folder(
            folder_path,
            allow_whole_disk=allow_whole_disk,
        )
        image_files = discover_image_files_recursively(folder)
    except Exception as exc:  # noqa: BLE001
        raise_http_exception_from_error(exc)

    total_files = len(image_files)

    async with services.scan_task_lock:
        if services.scan_state.is_active:
            raise_api_error(
                status_code=status.HTTP_409_CONFLICT,
                error=(
                    "A folder scan is already in progress. "
                    "Poll GET /api/scan-status until is_active is false before starting another scan."
                ),
                code=ErrorCode.SCAN_IN_PROGRESS,
            )

        if services.scan_task is not None and not services.scan_task.done():
            raise_api_error(
                status_code=status.HTTP_409_CONFLICT,
                error="Previous scan task has not completed yet.",
                code=ErrorCode.SCAN_IN_PROGRESS,
            )

        acquired = services.scan_state.try_begin_scan(total_files=total_files)
        if not acquired:
            raise_api_error(
                status_code=status.HTTP_409_CONFLICT,
                error="Scanner is already active (could not acquire scan lock).",
                code=ErrorCode.SCAN_IN_PROGRESS,
            )

        await asyncio.to_thread(services.ai_engine.ensure_detector_backend_ready)

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

    setup_logging(level=os.environ.get("PHOTO_ORGANIZER_LOG_LEVEL", "INFO"))

    LOGGER.info(
        "Starting photo organizer sidecar (offline FastAPI)",
        extra={"ctx": {"event": "sidecar.start"}},
    )

    if is_dev_mode():
        LOGGER.warning(
            "DEV endpoints ENABLED — do not use in production",
            extra={"ctx": {"event": "dev_mode.enabled"}},
        )

    verify_ai_runtime_dependencies()
    initialize_gpu_if_requested()

    database = DatabaseManager()
    database.create_tables()
    try:
        database.require_database_integrity()
    except DatabaseError as exc:
        LOGGER.error("Sidecar startup aborted: %s", exc)
        raise SystemExit(1) from exc

    ai_engine = AICoreEngine()
    try:
        detector_backend = await asyncio.to_thread(ai_engine.ensure_detector_backend_ready)
    except FaceDetectionError as exc:
        LOGGER.error("Sidecar startup aborted: detector probe failed: %s", exc)
        raise SystemExit(1) from exc
    LOGGER.info(
        "Detector backend ready at startup",
        extra={"ctx": {"event": "detector.ready", "backend": detector_backend}},
    )

    clustering = ClusteringEngine(database=database)
    _remove_legacy_thumbnail_cache_dir()
    thumbnail_engine = ThumbnailEngine(cache_dir=resolve_thumbnail_cache_dir())
    scan_state = ScanProgressState()

    vector_store = None
    try:
        from vector_store import FaceVectorStore, get_faiss_index_path

        vector_store = FaceVectorStore.load_or_rebuild(
            get_faiss_index_path(get_app_data_dir()),
            database,
        )
    except RuntimeError as exc:
        LOGGER.warning("FAISS vector store unavailable: %s", exc)

    _services = AppServices(
        database=database,
        ai_engine=ai_engine,
        clustering=clustering,
        thumbnail_engine=thumbnail_engine,
        scan_state=scan_state,
        vector_store=vector_store,
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
        _services.thumbnail_engine.close()

    _services = None


def assert_loopback_bind_host(host: str) -> str:
    """
    Validate that the sidecar binds only to a loopback interface.

    Returns the normalized host string passed to Uvicorn (``localhost`` → ``127.0.0.1``).
    """
    normalized = host.strip().lower()
    if not normalized:
        raise ValueError("Bind host must not be empty")
    if normalized not in LOOPBACK_BIND_HOSTS:
        raise ValueError(
            f"Sidecar must bind to loopback only, got {host!r}. "
            f"Allowed: {sorted(LOOPBACK_BIND_HOSTS)}"
        )
    if normalized == "localhost":
        return "127.0.0.1"
    return normalized


def create_application(*, dev_mode: bool | None = None) -> FastAPI:
    """Build and configure the FastAPI application instance."""
    docs_enabled = is_dev_mode(override=dev_mode)
    application = FastAPI(
        title="Photo Organizer Sidecar API",
        description=(
            "Local-only FastAPI sidecar for the offline photo organizer. "
            "Wraps database.py and ai_core.py for Tauri desktop integration."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
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

    register_exception_handlers(application)

    register_routes(application, dev_mode=dev_mode)
    application.include_router(v1_router)
    return application


def register_routes(application: FastAPI, *, dev_mode: bool | None = None) -> None:
    """Attach production /api routes; dev-only routes when ``PHOTO_ORGANIZER_DEV=1``."""

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
    async def post_scan_folder(
        request: Request,
        request_body: ScanFolderRequest,
    ) -> ScanFolderResponse:
        """
        Recursively scan a local folder for JPG/PNG images.

        Heavy AI work runs in a background asyncio task using worker threads so this
        endpoint returns immediately with `{ "status": "started", "total_files": N }`.
        """
        try:
            services = get_services()
            allow_whole_disk = request.headers.get("X-Confirm-Whole-Disk") == "1"
            return await start_folder_scan(
                services=services,
                folder_path=request_body.folder_path,
                allow_whole_disk=allow_whole_disk,
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
                cancelled=bool(snapshot.get("cancelled")),
                eta_seconds=snapshot.get("eta_seconds"),
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
            description="Filter by ingestion status: all | processed | unprocessed | faceless.",
            examples=["processed"],
        ),
    ) -> list[SearchResultItem]:
        """
        Gallery listing for the desktop UI.

        When person_ids is omitted or empty, returns all photos (subject to ai_status).
        When one person_id is set, returns every photo that contains that person (group shots included).
        When multiple person_ids are set, returns photos where every listed person appears together.
        """
        try:
            services = get_services()
            processed_only, faceless_only = resolve_gallery_ai_filter(ai_status)
            person_id_list = parse_comma_separated_person_ids(person_ids)

            if faceless_only:
                photo_rows = services.database.get_all_photos(faceless_only=True)
            elif person_id_list:
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
        response_model=list[UnnamedClusterSummaryItem],
        responses={
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["clusters"],
        summary="List unnamed DBSCAN clusters with exemplar face geometry",
    )
    async def get_unnamed_clusters() -> list[UnnamedClusterSummaryItem]:
        """
        Return unnamed clusters with bounding boxes for face-centered UI thumbnails.

        DBSCAN noise faces (cluster_id NULL) are excluded by the database layer.
        """
        try:
            services = get_services()
            summaries = services.database.get_unnamed_cluster_summaries()
            results = [
                UnnamedClusterSummaryItem(
                    cluster_id=summary.cluster_id,
                    exemplar_face_id=summary.exemplar_face_id,
                    photo_id=summary.photo_id,
                    bounding_box=bounding_box_to_model(summary.bounding_box),
                    face_count=summary.face_count,
                    thumbnail_url=(
                        f"{API_PREFIX}/faces/{summary.exemplar_face_id}/thumbnail"
                        f"?crop=1&width={THUMBNAIL_DEFAULT_WIDTH}"
                    ),
                )
                for summary in summaries
            ]
            LOGGER.info("GET /api/clusters/unnamed → %s cluster(s)", len(results))
            return results
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
                    bounding_box=bounding_box_to_model(face.bounding_box),
                    thumbnail_url=(
                        f"{API_PREFIX}/faces/{face.id}/thumbnail"
                        f"?crop=1&width={THUMBNAIL_DEFAULT_WIDTH}"
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
                    exemplar_face_id=row.exemplar_face_id,
                    bounding_box=(
                        bounding_box_to_model(row.exemplar_bounding_box)
                        if row.exemplar_bounding_box is not None
                        else None
                    ),
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
        f"{API_PREFIX}/clusters/{{cluster_id}}/photos",
        response_model=list[SearchResultItem],
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
            status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
        },
        tags=["clusters"],
        summary="List every photo containing at least one face in the cluster",
    )
    async def get_cluster_photos(cluster_id: int) -> list[SearchResultItem]:
        """
        Return all images where any detected face belongs to ``cluster_id``.

        Multi-face photos are included when at least one face matches; other faces in the
        same image are ignored for filtering purposes.
        """
        try:
            services = get_services()
            photo_rows = services.database.get_photos_by_cluster_id(cluster_id)
            results = [
                SearchResultItem(photo_id=row.id, file_path=row.file_path)
                for row in photo_rows
            ]
            LOGGER.info(
                "GET /api/clusters/%s/photos → %s photo(s)",
                cluster_id,
                len(results),
            )
            return results
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET /api/clusters/%s/photos failed", cluster_id)
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
        crop: bool = Query(
            False,
            description="When true, crop to the face bounding box before resizing.",
        ),
    ) -> FileResponse:
        """Stream a JPEG thumbnail for this face (full photo or face-centered crop)."""
        try:
            if face_id <= 0:
                raise ValueError("face_id must be a positive integer")

            services = get_services()
            face_row = services.database.get_face_by_id(face_id)
            if face_row is None:
                raise RecordNotFoundError(f"No face found with id={face_id}")

            if crop:
                response = await build_face_thumbnail_file_response(
                    services=services,
                    face_row=face_row,
                    width=width,
                    height=height,
                )
            else:
                response = await build_photo_thumbnail_file_response(
                    services=services,
                    photo_id=face_row.photo_id,
                    width=width,
                    height=height,
                )
            LOGGER.info(
                "GET /api/faces/%s/thumbnail crop=%s → photo_id=%s file=%s",
                face_id,
                crop,
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

    if is_dev_mode(override=dev_mode):
        register_dev_routes(application)


def register_dev_routes(application: FastAPI) -> None:
    """Register ``/api/dev/*`` helpers (localhost sidecar, developer workflows only)."""

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
    bind_host = assert_loopback_bind_host(host)
    setup_logging(level=log_level.upper())
    LOGGER.info("Listening on %s:%s (loopback-only)", bind_host, port)
    uvicorn.run(
        app,
        host=bind_host,
        port=port,
        log_level=log_level,
        access_log=True,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    setup_logging(level=os.environ.get("PHOTO_ORGANIZER_LOG_LEVEL", "INFO"))
    bind_host = os.environ.get("PHOTO_ORGANIZER_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    bind_port_raw = os.environ.get("PHOTO_ORGANIZER_PORT", str(DEFAULT_PORT)).strip()
    try:
        bind_port = int(bind_port_raw)
    except ValueError as exc:
        raise ValueError(
            f"PHOTO_ORGANIZER_PORT must be an integer, got {bind_port_raw!r}"
        ) from exc
    run_server(host=bind_host, port=bind_port)
