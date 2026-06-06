"""
Filesystem path validation for scan endpoints (task 1.2.3).

Rejects traversal, symlink escapes, missing paths, non-directories, and whole-disk
roots unless explicitly confirmed by the caller.
"""

from __future__ import annotations

import os
from pathlib import Path

from errors import PathValidationError
from logging_config import hash_path_for_log


def _path_hash(raw: str) -> str:
    return hash_path_for_log(raw)


def _reject(message: str, *, raw: str, reason: str) -> None:
    raise PathValidationError(
        message,
        details={"path_hash": _path_hash(raw), "reason": reason},
    )


def _is_drive_root(resolved: Path) -> bool:
    normalized = str(resolved).rstrip("\\/")
    drive, tail = os.path.splitdrive(normalized)
    return bool(drive and not tail.lstrip("\\/"))


def _symlink_escape_detected(unresolved: Path, resolved: Path) -> bool:
    """
    Return True when resolving symlinks escapes the nominal parent directory tree.
    """
    anchor = unresolved.parent.resolve()
    if resolved == anchor or resolved.is_relative_to(anchor):
        return False
    if unresolved.is_symlink():
        return True
    for parent in unresolved.parents:
        if parent.is_symlink():
            return True
    return False


def validate_scan_path(raw: str, *, allow_whole_disk: bool = False) -> Path:
    """
    Validate and resolve a user-supplied folder path for scanning.

    Raises:
        PathValidationError: Path fails security or shape checks.
    """
    if not raw or not str(raw).strip():
        _reject("folder_path must not be empty", raw=raw or "", reason="empty")

    if "\0" in raw:
        _reject("folder_path contains null byte", raw=raw, reason="null_byte")

    candidate = Path(raw).expanduser()

    if ".." in candidate.parts:
        _reject(
            "Path must not contain '..' segments",
            raw=raw,
            reason="parent_traversal",
        )

    if not candidate.exists():
        _reject(
            "Directory does not exist",
            raw=raw,
            reason="not_found",
        )

    resolved = candidate.resolve()

    if _symlink_escape_detected(candidate, resolved):
        _reject(
            "Symlink resolves outside the allowed directory tree",
            raw=raw,
            reason="symlink_escape",
        )

    if not resolved.is_dir():
        _reject(
            "Path is not a directory",
            raw=raw,
            reason="not_directory",
        )

    if _is_drive_root(resolved) and not allow_whole_disk:
        _reject(
            "Refusing to scan drive root without explicit confirmation",
            raw=raw,
            reason="whole_disk",
        )

    return resolved
