"""Task 1.3.1 — gallery SELECT plans must use indexes (no full table scans)."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import pytest

from database import (
    EMBEDDING_BLOB_FORMAT,
    EXPECTED_EMBEDDING_DIMENSION,
    NAMED_CLUSTER_ID_MIN,
    DatabaseManager,
    PHOTO_COLUMNS,
    PHOTO_COLUMNS_ALIASED,
    FACE_COLUMNS,
)


@dataclass(frozen=True)
class GalleryQueryCase:
    name: str
    sql: str
    params: tuple[object, ...]
    forbidden_scans: tuple[str, ...]


GALLERY_QUERY_CASES: tuple[GalleryQueryCase, ...] = (
    GalleryQueryCase(
        name="gallery_faceless_only",
        sql=f"""
            SELECT {PHOTO_COLUMNS}
            FROM photos
            WHERE processed = 1 AND has_faces = 0
            ORDER BY date_added DESC, id DESC
        """,
        params=(),
        forbidden_scans=("photos",),
    ),
    GalleryQueryCase(
        name="gallery_processed_only",
        sql=f"""
            SELECT {PHOTO_COLUMNS}
            FROM photos
            WHERE processed = 1
            ORDER BY date_added DESC, id DESC
        """,
        params=(),
        forbidden_scans=("photos",),
    ),
    GalleryQueryCase(
        name="faces_for_person",
        sql=f"""
            SELECT {FACE_COLUMNS}
            FROM faces
            WHERE person_id = ?
            ORDER BY id ASC
        """,
        params=(1,),
        forbidden_scans=("faces",),
    ),
    GalleryQueryCase(
        name="photos_by_cluster_join",
        sql=f"""
            SELECT DISTINCT {PHOTO_COLUMNS_ALIASED}
            FROM photos AS p
            INNER JOIN faces AS f ON f.photo_id = p.id
            WHERE f.cluster_id = ?
            ORDER BY p.date_added DESC, p.id DESC
        """,
        params=(0,),
        forbidden_scans=("faces",),
    ),
    GalleryQueryCase(
        name="unnamed_cluster_summaries",
        sql="""
            WITH ranked AS (
                SELECT
                    cluster_id,
                    id,
                    photo_id,
                    bounding_box,
                    ROW_NUMBER() OVER (
                        PARTITION BY cluster_id
                        ORDER BY id ASC
                    ) AS rn,
                    COUNT(*) OVER (PARTITION BY cluster_id) AS face_count
                FROM faces
                WHERE cluster_id IS NOT NULL
                  AND cluster_id >= ?
                  AND person_id IS NULL
            )
            SELECT
                cluster_id,
                id AS exemplar_face_id,
                photo_id,
                bounding_box,
                face_count
            FROM ranked
            WHERE rn = 1
            ORDER BY cluster_id ASC
        """,
        params=(NAMED_CLUSTER_ID_MIN,),
        forbidden_scans=("faces",),
    ),
)


def _embedding_blob() -> bytes:
    return struct.pack(EMBEDDING_BLOB_FORMAT, *([0.01] * EXPECTED_EMBEDDING_DIMENSION))


def _seed_gallery_database(manager: DatabaseManager, *, photo_count: int) -> None:
    bbox = '{"x": 1, "y": 2, "w": 10, "h": 10}'
    embedding = _embedding_blob()

    with manager._managed_connection() as connection:
        for index in range(photo_count):
            processed = 1 if index % 3 != 0 else 0
            has_faces = 0 if index % 17 == 0 and processed else 1
            cursor = manager._execute(
                connection,
                """
                INSERT INTO photos (file_path, processed, has_faces)
                VALUES (?, ?, ?)
                """,
                (f"/photos/img_{index:06d}.jpg", processed, has_faces),
            )
            photo_id = int(cursor.lastrowid)
            if has_faces:
                cluster_id = index % 25
                person_id = 1 if index % 5 == 0 else None
                manager._execute(
                    connection,
                    """
                    INSERT INTO faces (
                        photo_id, embedding, bounding_box, cluster_id, person_id
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (photo_id, embedding, bbox, cluster_id, person_id),
                )

        manager._execute(
            connection,
            "INSERT INTO people (name) VALUES ('Anna')",
        )


def _assert_no_table_scan(plan: str, table: str) -> None:
    for line in plan.splitlines():
        upper = line.upper()
        if "SCAN TABLE" in upper and table.upper() in upper:
            pytest.fail(f"Full table scan on {table}:\n{plan}")
        if "SCAN " + table.upper() in upper and "USING" not in upper:
            pytest.fail(f"Full table scan on {table}:\n{plan}")


@pytest.fixture
def indexed_gallery_db(tmp_path) -> DatabaseManager:
    manager = DatabaseManager(str(tmp_path / "gallery-perf.db"))
    manager.create_tables()
    _seed_gallery_database(manager, photo_count=2_000)
    return manager


@pytest.mark.parametrize("case", GALLERY_QUERY_CASES, ids=lambda case: case.name)
def test_gallery_query_uses_index(
    indexed_gallery_db: DatabaseManager,
    case: GalleryQueryCase,
) -> None:
    with indexed_gallery_db._managed_connection() as connection:
        plan = DatabaseManager.explain_query_plan(
            connection,
            case.sql,
            case.params,
        )

    for table in case.forbidden_scans:
        _assert_no_table_scan(plan, table)

    upper = plan.upper()
    assert "USING INDEX" in upper or "USING COVERING INDEX" in upper, (
        f"Expected index usage for {case.name}:\n{plan}"
    )


@pytest.mark.slow
def test_get_all_photos_faceless_benchmark_50k(tmp_path) -> None:
    manager = DatabaseManager(str(tmp_path / "faceless-bench.db"))
    manager.create_tables()
    _seed_gallery_database(manager, photo_count=50_000)

    started = time.perf_counter()
    rows = manager.get_all_photos(faceless_only=True)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert len(rows) > 0
    assert elapsed_ms < 100, f"faceless gallery took {elapsed_ms:.1f}ms (target <100ms)"
