"""Task 3.2.1 tier 1 — security path resolution and DB integrity."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

from database import (
    APP_DATA_ENV_VAR,
    DatabaseManager,
    RecordNotFoundError,
    ValidationError,
    get_app_data_dir,
)
from main import resolve_photo_source_path


def _write_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(100, 100, 100)).save(path, format="JPEG")


def test_resolve_photo_source_path_rejects_null_byte() -> None:
    with pytest.raises(ValueError, match="null byte"):
        resolve_photo_source_path("C:\\photos\\face.jpg\x00evil")


def test_resolve_photo_source_path_rejects_parent_traversal(tmp_path: Path) -> None:
    nested = tmp_path / "library" / ".." / "escape"
    with pytest.raises(ValueError, match="\\.\\."):
        resolve_photo_source_path(str(nested))


def test_resolve_photo_source_path_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jpg"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        resolve_photo_source_path(str(missing))


def test_resolve_photo_source_path_unsupported_suffix(tmp_path: Path) -> None:
    gif_path = tmp_path / "photo.gif"
    gif_path.write_bytes(b"GIF89a")
    with pytest.raises(ValueError, match="Unsupported photo extension"):
        resolve_photo_source_path(str(gif_path))


def test_resolve_photo_source_path_accepts_existing_jpeg(tmp_path: Path) -> None:
    jpeg_path = tmp_path / "valid.jpg"
    _write_jpeg(jpeg_path)
    resolved = resolve_photo_source_path(str(jpeg_path))
    assert resolved == jpeg_path.resolve()
    assert resolved.is_file()


def test_get_app_data_dir_raises_without_appdata_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(APP_DATA_ENV_VAR, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(RuntimeError, match="APPDATA"):
        get_app_data_dir()


def test_merge_person_clusters_moves_faces_and_deletes_source(
    temp_db: DatabaseManager,
    sample_embedding: list[float],
) -> None:
    target_id = temp_db.insert_person("Target")
    source_id = temp_db.insert_person("Source")
    photo_id = temp_db.insert_photo(str(Path("/photos/merge-me.jpg")))
    bbox = {"x": 0, "y": 0, "w": 32, "h": 32}
    face_a = temp_db.insert_face(photo_id, sample_embedding, bbox)
    face_b = temp_db.insert_face(photo_id, sample_embedding, bbox)
    temp_db.assign_faces_to_person([face_a, face_b], source_id)

    moved = temp_db.merge_person_clusters(target_id, source_id)

    assert moved == 2
    assert temp_db.get_face_by_id(face_a).person_id == target_id
    assert temp_db.get_face_by_id(face_b).person_id == target_id
    remaining_people = {person.id for person in temp_db.get_all_people()}
    assert remaining_people == {target_id}

    with temp_db._managed_connection() as connection:
        orphan_faces = connection.execute(
            "SELECT COUNT(*) FROM faces WHERE person_id = ?",
            (source_id,),
        ).fetchone()[0]
    assert orphan_faces == 0


def test_merge_person_clusters_rejects_same_id(temp_db: DatabaseManager) -> None:
    person_id = temp_db.insert_person("Solo")
    with pytest.raises(ValidationError, match="must differ"):
        temp_db.merge_person_clusters(person_id, person_id)


def test_merge_person_clusters_missing_source_raises(
    temp_db: DatabaseManager,
) -> None:
    target_id = temp_db.insert_person("Target")
    with pytest.raises(RecordNotFoundError, match="99999"):
        temp_db.merge_person_clusters(target_id, 99999)


def test_insert_photo_is_idempotent(temp_db: DatabaseManager) -> None:
    path = str(Path("/photos/idempotent.jpg"))
    first_id = temp_db.insert_photo(path)
    second_id = temp_db.insert_photo(path)

    assert first_id == second_id
    assert temp_db.count_photos() == 1


def test_mark_photo_as_processed_missing_photo_raises(
    temp_db: DatabaseManager,
) -> None:
    with pytest.raises(RecordNotFoundError, match="99999"):
        temp_db.mark_photo_as_processed(999_999)


def test_assign_faces_to_person_rejects_unknown_person(
    temp_db: DatabaseManager,
    sample_embedding: list[float],
) -> None:
    photo_id = temp_db.insert_photo(str(Path("/photos/orphan-face.jpg")))
    face_id = temp_db.insert_face(
        photo_id,
        sample_embedding,
        {"x": 0, "y": 0, "w": 10, "h": 10},
    )
    with pytest.raises(RecordNotFoundError, match="88888"):
        temp_db.assign_faces_to_person([face_id], 88888)
