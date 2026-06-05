"""Task 1.2.2 — database path resolution under AppData / env override."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from database import (
    APP_DATA_ENV_VAR,
    DB_FILENAME,
    DB_PATH_ENV_VAR,
    DatabaseManager,
    get_app_data_dir,
    get_db_path,
    migrate_legacy_database_if_needed,
)


def test_get_db_path_respects_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom" / "library.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(custom))

    assert get_db_path(migrate_legacy=False) == custom.resolve()
    assert custom.parent.is_dir()


def test_db_path_windows_uses_appdata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appdata = tmp_path / "Roaming"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv(APP_DATA_ENV_VAR, raising=False)
    monkeypatch.delenv(DB_PATH_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    expected = appdata / "com.photo.organizer" / DB_FILENAME
    assert get_db_path(migrate_legacy=False) == expected.resolve()
    assert expected.parent.is_dir()


def test_db_path_macos_uses_application_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv(APP_DATA_ENV_VAR, raising=False)
    monkeypatch.delenv(DB_PATH_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    expected = home / "Library" / "Application Support" / "com.photo.organizer" / DB_FILENAME
    assert get_db_path(migrate_legacy=False) == expected.resolve()


def test_db_path_linux_uses_xdg_data_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv(APP_DATA_ENV_VAR, raising=False)
    monkeypatch.delenv(DB_PATH_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")

    expected = home / ".local" / "share" / "com.photo.organizer" / DB_FILENAME
    assert get_db_path(migrate_legacy=False) == expected.resolve()


def test_get_app_data_dir_respects_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "appdata-override"
    monkeypatch.setenv(APP_DATA_ENV_VAR, str(override))

    assert get_app_data_dir() == override.resolve()
    assert override.is_dir()


def test_migrate_legacy_database_copies_from_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / DB_FILENAME
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE migration_marker (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    target = tmp_path / "dest" / DB_FILENAME
    migrate_legacy_database_if_needed(target)

    assert target.is_file()
    assert legacy.is_file()
    dest_conn = sqlite3.connect(target)
    tables = {
        row[0]
        for row in dest_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    dest_conn.close()
    assert "migration_marker" in tables


def test_database_manager_default_uses_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "via-manager.db"
    monkeypatch.setenv(DB_PATH_ENV_VAR, str(db_file))

    manager = DatabaseManager(migrate_legacy=False)
    assert manager.db_path == str(db_file.resolve())
