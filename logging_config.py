"""
Structured logging for the Photo Organizer Python sidecar.

Logs are written as JSON lines to a rotating file under the application data
directory (Windows: ``%AppData%\\com.photo.organizer\\logs\\backend.log``).

Override directories for tests or custom installs:
  - ``PHOTO_ORGANIZER_APP_DATA`` — base data folder (logs live in ``logs/`` beneath it)
  - ``PHOTO_ORGANIZER_LOG_DIR`` — explicit log directory
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final

DEV_MODE_ENV_VAR: Final[str] = "PHOTO_ORGANIZER_DEV"
APP_DATA_ENV_VAR: Final[str] = "PHOTO_ORGANIZER_APP_DATA"
LOG_DIR_ENV_VAR: Final[str] = "PHOTO_ORGANIZER_LOG_DIR"

BACKEND_LOG_FILENAME: Final[str] = "backend.log"
LOG_FILE_MAX_BYTES: Final[int] = 10 * 1024 * 1024
LOG_FILE_BACKUP_COUNT: Final[int] = 5

_CONFIGURED: bool = False


def is_dev_mode(*, override: bool | None = None) -> bool:
    """
    Return True when developer-only HTTP routes may be registered.

    When ``override`` is provided it takes precedence (used in tests).
    Otherwise delegates to ``PHOTO_ORGANIZER_DEV=1``.
    """
    if override is not None:
        return override
    return os.environ.get(DEV_MODE_ENV_VAR, "0").strip() == "1"


def get_app_data_dir() -> Path:
    """
    Return the writable application data root.

    Windows: ``%AppData%\\com.photo.organizer``
    macOS: ``~/Library/Application Support/com.photo.organizer``
    Linux: ``~/.local/share/com.photo.organizer``
    """
    override = os.environ.get(APP_DATA_ENV_VAR, "").strip()
    if override:
        base = Path(override).expanduser()
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
        else:
            base = base.resolve()
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "").strip()
        if not appdata:
            raise RuntimeError(
                "APPDATA environment variable is not set; "
                f"set {APP_DATA_ENV_VAR} to override the data directory."
            )
        base = Path(appdata) / "com.photo.organizer"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "com.photo.organizer"
    else:
        base = Path.home() / ".local" / "share" / "com.photo.organizer"

    base.mkdir(parents=True, exist_ok=True)
    return base


def get_log_dir() -> Path:
    """Return (and create) the directory for rotating log files."""
    override = os.environ.get(LOG_DIR_ENV_VAR, "").strip()
    if override:
        log_dir = Path(override).expanduser()
        if not log_dir.is_absolute():
            log_dir = (Path.cwd() / log_dir).resolve()
        else:
            log_dir = log_dir.resolve()
    else:
        log_dir = get_app_data_dir() / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def hash_path_for_log(path: str | Path | None) -> str:
    """
    Return a stable short hash of a filesystem path for INFO-level logs (privacy).

    Full paths must not appear at INFO; use this helper or log at ERROR with care.
    """
    if path is None:
        return "none"
    normalized = str(Path(path).expanduser().resolve())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:16]


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log line: ts, level, logger, msg, optional ctx."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict) and ctx:
            payload["ctx"] = ctx

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exc"] = record.exc_text

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> Path:
    """
    Configure the root logger with rotating JSON file output.

    Idempotent: subsequent calls are no-ops after the first successful setup.

    Returns:
        Path to the active ``backend.log`` file.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return get_log_dir() / BACKEND_LOG_FILENAME

    log_dir = get_log_dir()
    log_file = log_dir / BACKEND_LOG_FILENAME

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric_level)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLogFormatter())
    root.addHandler(file_handler)

    if is_dev_mode():
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(JsonLogFormatter())
        root.addHandler(stream_handler)

    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "Logging initialized",
        extra={
            "ctx": {
                "event": "logging.init",
                "log_dir": str(log_dir),
                "dev_stderr": is_dev_mode(),
            }
        },
    )
    return log_file


def reset_logging_for_tests() -> None:
    """Clear handlers and configuration flag (pytest only)."""
    global _CONFIGURED
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    _CONFIGURED = False
