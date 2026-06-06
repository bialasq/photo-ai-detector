"""Task 2.3.3 — ThumbnailEngine LRU eviction under AppData cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from database import APP_DATA_ENV_VAR
from main import (
    THUMBNAIL_CACHE_INDEX_FILENAME,
    THUMBNAIL_CACHE_MB_ENV_VAR,
    ThumbnailEngine,
    _thumbnail_cache_max_bytes,
    resolve_thumbnail_cache_dir,
)


@pytest.fixture
def app_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "appdata"
    monkeypatch.setenv(APP_DATA_ENV_VAR, str(root))
    return root


def _write_fake_jpg(cache_dir: Path, name: str, size: int) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / name
    path.write_bytes(b"x" * size)
    return path


def _seed_index(
    cache_dir: Path,
    entries: dict[str, dict[str, float | int]],
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_path = cache_dir / THUMBNAIL_CACHE_INDEX_FILENAME
    index_path.write_text(
        json.dumps({"version": 1, "entries": entries}),
        encoding="utf-8",
    )


def test_resolve_thumbnail_cache_dir_under_app_data(app_data_root: Path) -> None:
    expected = app_data_root / "thumbnails"
    assert resolve_thumbnail_cache_dir() == expected.resolve()


def test_thumbnail_cache_mb_env_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(THUMBNAIL_CACHE_MB_ENV_VAR, "not-a-number")
    with pytest.raises(ValueError, match=THUMBNAIL_CACHE_MB_ENV_VAR):
        _thumbnail_cache_max_bytes()


def test_eviction_removes_oldest_and_stays_within_limit(app_data_root: Path) -> None:
    cache_dir = resolve_thumbnail_cache_dir()
    _write_fake_jpg(cache_dir, "oldest.jpg", 400)
    _write_fake_jpg(cache_dir, "middle.jpg", 400)
    _write_fake_jpg(cache_dir, "newest.jpg", 400)
    _seed_index(
        cache_dir,
        {
            "oldest.jpg": {"size_bytes": 400, "last_access": 1.0},
            "middle.jpg": {"size_bytes": 400, "last_access": 2.0},
            "newest.jpg": {"size_bytes": 400, "last_access": 3.0},
        },
    )

    engine = ThumbnailEngine(cache_dir=cache_dir, max_bytes=1000)
    assert engine._total_bytes == 1200

    with engine._io_lock:
        engine._evict_if_needed()

    assert engine._total_bytes <= 1000
    assert not (cache_dir / "oldest.jpg").exists()
    assert (cache_dir / "middle.jpg").exists()
    assert (cache_dir / "newest.jpg").exists()
    assert "oldest.jpg" not in engine._access_index


def test_new_write_triggers_auto_eviction(app_data_root: Path) -> None:
    cache_dir = resolve_thumbnail_cache_dir()
    _write_fake_jpg(cache_dir, "stale.jpg", 400)
    _seed_index(
        cache_dir,
        {"stale.jpg": {"size_bytes": 400, "last_access": 1.0}},
    )

    engine = ThumbnailEngine(cache_dir=cache_dir, max_bytes=500)
    assert engine._total_bytes == 400

    new_path = _write_fake_jpg(cache_dir, "fresh.jpg", 300)
    with engine._io_lock:
        engine._record_cache_write(new_path)

    assert engine._total_bytes <= 500
    assert not (cache_dir / "stale.jpg").exists()
    assert (cache_dir / "fresh.jpg").exists()
    assert "stale.jpg" not in engine._access_index
    assert "fresh.jpg" in engine._access_index


def test_close_persists_index(app_data_root: Path) -> None:
    cache_dir = resolve_thumbnail_cache_dir()
    _write_fake_jpg(cache_dir, "one.jpg", 128)
    engine = ThumbnailEngine(cache_dir=cache_dir, max_bytes=10_000)
    engine._access_index["one.jpg"] = {"size_bytes": 128, "last_access": 99.0}
    engine._total_bytes = 128

    engine.close()

    index_path = cache_dir / THUMBNAIL_CACHE_INDEX_FILENAME
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["entries"]["one.jpg"]["last_access"] == 99.0
