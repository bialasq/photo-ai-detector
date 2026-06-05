"""Task 1.3.4 — database integrity and foreign-key enforcement."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from database import DatabaseError, DatabaseManager, EMBEDDING_BLOB_FORMAT, EXPECTED_EMBEDDING_DIMENSION


def test_foreign_keys_reject_orphan_face_insert(tmp_path: Path) -> None:
    manager = DatabaseManager(str(tmp_path / "fk.db"))
    manager.create_tables()
    embedding = struct.pack(EMBEDDING_BLOB_FORMAT, *([0.01] * EXPECTED_EMBEDDING_DIMENSION))

    with manager._managed_connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO faces (photo_id, embedding, bounding_box, cluster_id, person_id)
                VALUES (?, ?, ?, NULL, NULL)
                """,
                (999_999, embedding, '{"x":0,"y":0,"w":1,"h":1}'),
            )


def test_require_database_integrity_raises_on_corrupt_db(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    manager = DatabaseManager(str(db_path))
    manager.create_tables()
    manager.insert_photo("/photos/corrupt-test.jpg")

    raw = bytearray(db_path.read_bytes())
    if len(raw) > 128:
        raw[100:120] = b"\x00" * 20
    db_path.write_bytes(raw)

    with pytest.raises(DatabaseError, match="integrity check failed"):
        manager.require_database_integrity()
