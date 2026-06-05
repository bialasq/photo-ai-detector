"""Task 1.3.3 — batched face inserts with transactional atomicity."""

from __future__ import annotations

import time

import pytest

from database import (
    EXPECTED_EMBEDDING_DIMENSION,
    DatabaseManager,
    PendingFaceInsert,
    RecordNotFoundError,
    insert_faces_batch,
)


def _sample_pending(*, photo_id: int, index: int = 0) -> PendingFaceInsert:
    vector = [0.0] * EXPECTED_EMBEDDING_DIMENSION
    vector[0] = 0.1 + (index * 0.0001)
    return PendingFaceInsert(
        photo_id=photo_id,
        embedding=vector,
        bounding_box={"x": 1, "y": 2, "w": 10, "h": 10},
    )


def _insert_faces_one_by_one(
    manager: DatabaseManager,
    photo_id: int,
    count: int,
) -> None:
    for index in range(count):
        manager.insert_face(
            photo_id=photo_id,
            embedding=_sample_pending(photo_id=photo_id, index=index).embedding,
            bounding_box={"x": 1, "y": 2, "w": 10, "h": 10},
        )


def _insert_faces_batched(
    manager: DatabaseManager,
    photo_id: int,
    count: int,
    *,
    batch_size: int = 100,
) -> None:
    pending = [_sample_pending(photo_id=photo_id, index=index) for index in range(count)]
    manager.insert_faces_batch(pending, batch_size=batch_size)


@pytest.fixture
def batch_db(tmp_path) -> DatabaseManager:
    manager = DatabaseManager(str(tmp_path / "batch-insert.db"))
    manager.create_tables()
    return manager


def test_insert_faces_batch_rolls_back_failed_chunk(batch_db: DatabaseManager) -> None:
    photo_id = batch_db.insert_photo("/photos/rollback-test.jpg")
    valid = [_sample_pending(photo_id=photo_id, index=index) for index in range(5)]

    with batch_db._managed_connection() as connection:
        batch_db.insert_faces_batch(valid, connection=connection, batch_size=5)
        before = connection.execute("SELECT COUNT(*) FROM faces").fetchone()[0]

        bad_chunk = valid + [PendingFaceInsert(photo_id=999_999, embedding=valid[0].embedding, bounding_box=valid[0].bounding_box)]
        with pytest.raises(RecordNotFoundError):
            insert_faces_batch(connection, batch_db, bad_chunk, batch_size=6)

        after = connection.execute("SELECT COUNT(*) FROM faces").fetchone()[0]

    assert before == 5
    assert after == 5


def test_face_insert_buffer_flushes_in_chunks(batch_db: DatabaseManager) -> None:
    from ai_core import FaceInsertBuffer

    photo_id = batch_db.insert_photo("/photos/buffer.jpg")
    buffer = FaceInsertBuffer(batch_db, batch_size=100)
    for index in range(250):
        buffer.add(_sample_pending(photo_id=photo_id, index=index))

    assert len(buffer) == 50
    flushed = buffer.flush()
    assert len(flushed) == 50
    assert batch_db.get_schema_version_info()["faces"] == 250


@pytest.mark.slow
def test_batch_insert_is_at_least_5x_faster(batch_db: DatabaseManager, tmp_path) -> None:
    row_count = 5_000
    bbox = {"x": 1, "y": 2, "w": 10, "h": 10}
    embedding = _sample_pending(photo_id=1).embedding

    row_db = DatabaseManager(str(tmp_path / "row-insert.db"))
    row_db.create_tables()
    row_photo_id = row_db.insert_photo("/photos/row-bench.jpg")

    batch_db_path = tmp_path / "batch-insert-bench.db"
    batch_manager = DatabaseManager(str(batch_db_path))
    batch_manager.create_tables()
    batch_photo_id = batch_manager.insert_photo("/photos/batch-bench.jpg")

    row_started = time.perf_counter()
    _insert_faces_one_by_one(row_db, row_photo_id, row_count)
    row_elapsed = time.perf_counter() - row_started

    batch_started = time.perf_counter()
    _insert_faces_batched(batch_manager, batch_photo_id, row_count, batch_size=100)
    batch_elapsed = time.perf_counter() - batch_started

    speedup = row_elapsed / batch_elapsed if batch_elapsed > 0 else float("inf")
    assert batch_manager.get_schema_version_info()["faces"] == row_count
    assert speedup >= 5.0, (
        f"Expected ≥5× speedup, got {speedup:.1f}× "
        f"(row={row_elapsed:.2f}s batch={batch_elapsed:.2f}s)"
    )


def test_scan_directory_flushes_remaining_faces(batch_db: DatabaseManager) -> None:
    from ai_core import scan_directory

    class StubEngine:
        def process_image(self, path: str) -> list[dict]:
            del path
            vector = [0.0] * EXPECTED_EMBEDDING_DIMENSION
            vector[0] = 1.0
            return [
                {
                    "embedding": vector,
                    "bounding_box": {"x": 0, "y": 0, "w": 20, "h": 20},
                }
            ]

    paths = [f"/photos/scan-{index}.jpg" for index in range(5)]
    scan_directory(
        ai_engine=StubEngine(),  # type: ignore[arg-type]
        database=batch_db,
        image_paths=paths,
        face_batch_size=100,
    )

    assert batch_db.get_schema_version_info()["photos"] == 5
    assert batch_db.get_schema_version_info()["faces"] == 5
