"""Privacy helpers for log output (no PII filesystem paths in log lines)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_path_for_log(path: str | Path | None) -> str:
    """
    Return a stable short hash of a filesystem path for structured logs (privacy).

    Full paths must not appear at INFO/WARNING/DEBUG; use this helper instead.
    """
    if path is None:
        return "none"
    normalized = str(Path(path).expanduser().resolve())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:16]
