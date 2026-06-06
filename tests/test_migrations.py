"""Task 1.3.2 — numbered SQL migrations with backup and rollback."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

import database
from database import DatabaseManager, MIGRATE_DRY_RUN_ENV_VAR


@pytest.fixture
def migrations_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(database.MIGRATIONS_DIR, target)
    monkeypatch.setattr(database, "MIGRATIONS_DIR", target)
    return target


def test_migrations_create_schema(migrations_dir: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    manager = DatabaseManager(str(db_path))
    manager.create_tables()

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]

    assert {"photos", "people", "faces", "schema_migrations"}.issubset(tables)
    assert "cluster_health" in tables
    assert version == 4
    assert manager.integrity_check() == "ok"


def test_migration_dry_run_does_not_apply(
    migrations_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "dry-run.db"
    monkeypatch.setenv(MIGRATE_DRY_RUN_ENV_VAR, "1")

    manager = DatabaseManager(str(db_path))
    manager.create_tables()

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert version == 0
    assert tables == {"schema_migrations"}


def test_migration_failure_rollback(
    migrations_dir: Path,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rollback.db"
    manager = DatabaseManager(str(db_path))
    manager.create_tables()

    (migrations_dir / "005_fail.sql").write_text("THIS IS NOT VALID SQL;", encoding="utf-8")

    with pytest.raises(database.DatabaseError, match="005_fail.sql"):
        manager.apply_pending_migrations()

    backup = db_path.with_name(f"{db_path.name}.bak.4")
    assert backup.is_file()
    assert manager.integrity_check() == "ok"

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
    assert version == 4


def test_legacy_database_bootstraps_to_latest(
    migrations_dir: Path,
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(legacy_path)
    connection.executescript(
        """
        CREATE TABLE photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        );
        CREATE TABLE faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            bounding_box TEXT NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()

    manager = DatabaseManager(str(legacy_path))
    manager.create_tables()

    with sqlite3.connect(legacy_path) as upgraded:
        photo_columns = {
            row[1]
            for row in upgraded.execute("PRAGMA table_info(photos)")
        }
        version = upgraded.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        index_count = upgraded.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchone()[0]

    assert "has_faces" in photo_columns
    assert version == 4
    assert index_count >= 8
