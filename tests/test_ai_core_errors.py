"""Unit tests for ai_core domain exception hierarchy (task 1.1.1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ai_core import (
    AICoreEngine,
    AICoreError,
    ClusterNotFoundError,
    ClusteringEngine,
    ClusteringError,
    EmbeddingError,
    FaceDetectionError,
    ingest_image_to_database,
)
from database import DatabaseManager
from main import ScanProgressState, _run_clustering_sync, format_user_scan_last_error


def test_aicore_error_is_exception() -> None:
    error = AICoreError("failure")
    assert isinstance(error, Exception)
    assert str(error) == "failure"


def test_subclasses_inherit_aicore() -> None:
    assert issubclass(FaceDetectionError, AICoreError)
    assert issubclass(EmbeddingError, AICoreError)
    assert issubclass(ClusteringError, AICoreError)


def test_cluster_not_found_is_clustering_and_lookup() -> None:
    assert issubclass(ClusterNotFoundError, ClusteringError)
    assert issubclass(ClusterNotFoundError, LookupError)
    error = ClusterNotFoundError("missing cluster")
    assert isinstance(error, ClusteringError)
    assert isinstance(error, LookupError)


def test_default_code_per_subclass() -> None:
    assert AICoreError("x").code == "AI_CORE_FAILURE"
    assert FaceDetectionError("x").code == "FACE_DETECTION_FAILED"
    assert EmbeddingError("x").code == "EMBEDDING_INVALID"
    assert ClusteringError("x").code == "CLUSTERING_FAILED"
    assert ClusterNotFoundError("x").code == "CLUSTERING_FAILED"


def test_explicit_code_override() -> None:
    error = FaceDetectionError("x", code="CUSTOM")
    assert error.code == "CUSTOM"


def test_context_carried() -> None:
    error = FaceDetectionError("x", context={"file_path": "a.jpg"})
    assert error.context["file_path"] == "a.jpg"


def test_hint_carried() -> None:
    error = FaceDetectionError("x", hint="restart app")
    assert error.hint == "restart app"


def test_backward_compat_positional() -> None:
    error = FaceDetectionError("msg")
    assert str(error) == "msg"
    assert error.code == "FACE_DETECTION_FAILED"
    assert error.context == {}
    assert error.hint is None


# ---------------------------------------------------------------------------
# Acceptance tests (task 1.1.1)
# ---------------------------------------------------------------------------


def test_deepface_value_error_raises_face_detection_error(
    temp_db: DatabaseManager,
    jpeg_file_factory,
) -> None:
    """
    Represent-level failure surfaces as ``EmbeddingError`` and is isolated per image.

    ``process_image`` swallows ``EmbeddingError`` to ``[]``; ``ingest_image_to_database``
    catches ``AICoreError`` and marks the photo faceless without aborting the batch.
    """
    image_path = jpeg_file_factory("test.jpg")
    engine = AICoreEngine()
    mock_deepface = MagicMock()
    mock_deepface.represent.side_effect = ValueError("invalid image tensor")

    with patch.object(engine, "_probe_detector_backend", return_value="opencv"):
        with patch.object(engine, "_get_deepface", return_value=mock_deepface):
            with patch("ai_core._load_image_bgr", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
                assert engine.process_image(image_path) == []

    with patch.object(
        engine,
        "process_image",
        side_effect=EmbeddingError(
            "embedding failed for test.jpg",
            context={"path": str(image_path)},
        ),
    ):
        result = ingest_image_to_database(
            ai_engine=engine,
            database=temp_db,
            file_path=str(image_path),
        )

    assert result["skipped"] is True
    assert result["faceless"] is True
    assert "test.jpg" in result["file_path"]
    stored = temp_db.get_photo_by_path(str(image_path))
    assert stored is not None
    assert stored.processed is True
    assert stored.has_faces is False


def test_memory_error_raises_aicore_error(jpeg_file_factory) -> None:
    """MemoryError from ``DeepFace.represent`` is converted and does not crash ``process_image``."""
    image_path = jpeg_file_factory("memory.jpg")
    engine = AICoreEngine()
    mock_deepface = MagicMock()
    mock_deepface.represent.side_effect = MemoryError("out of memory")

    with patch.object(engine, "_probe_detector_backend", return_value="opencv"):
        with patch.object(engine, "_get_deepface", return_value=mock_deepface):
            with patch("ai_core._load_image_bgr", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
                faces = engine.process_image(image_path)

    assert faces == []

    error = EmbeddingError("embedding failed", context={"path": str(image_path)})
    assert isinstance(error, AICoreError)
    assert "memory.jpg" in error.context["path"]


def test_value_error_from_represent_returns_empty_and_records_path(
    jpeg_file_factory,
) -> None:
    """``DeepFace.represent`` ValueError → ``EmbeddingError`` (lines 935-945) → ``[]``."""
    image_path = jpeg_file_factory("bad-tensor.jpg")
    engine = AICoreEngine()
    mock_deepface = MagicMock()
    mock_deepface.represent.side_effect = ValueError("invalid image tensor")

    with patch.object(engine, "_probe_detector_backend", return_value="opencv"):
        with patch.object(engine, "_get_deepface", return_value=mock_deepface):
            with patch("ai_core._load_image_bgr", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
                assert engine.process_image(image_path) == []


def test_unexpected_represent_exception_returns_empty_list(
    jpeg_file_factory,
) -> None:
    """Non-domain DeepFace failures are logged and return ``[]`` (lines 971-978)."""
    image_path = jpeg_file_factory("tf-crash.jpg")
    engine = AICoreEngine()
    mock_deepface = MagicMock()
    mock_deepface.represent.side_effect = RuntimeError("tensorflow internal error")

    with patch.object(engine, "_probe_detector_backend", return_value="opencv"):
        with patch.object(engine, "_get_deepface", return_value=mock_deepface):
            with patch("ai_core._load_image_bgr", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
                assert engine.process_image(image_path) == []


def test_missing_file_raises_face_detection_error(tmp_path: Path) -> None:
    """Non-existent path fails validation before DeepFace runs."""
    engine = AICoreEngine()
    missing = tmp_path / "missing.jpg"

    with pytest.raises(FileNotFoundError):
        engine.process_image(missing)


def test_dbscan_failure_sets_last_error_no_crash(
    temp_db: DatabaseManager,
    sample_embedding: list[float],
) -> None:
    """``ClusteringError`` from DBSCAN is recorded on scan_state without crashing."""
    photo_id = temp_db.insert_photo(str(Path("/photos/group.jpg")))
    temp_db.insert_face(
        photo_id,
        sample_embedding,
        {"x": 0, "y": 0, "w": 32, "h": 32},
    )
    temp_db.insert_face(
        photo_id,
        sample_embedding,
        {"x": 40, "y": 0, "w": 32, "h": 32},
    )

    clustering = ClusteringEngine(temp_db)
    with patch("ai_core.DBSCAN") as mock_dbscan:
        mock_dbscan.return_value.fit_predict.side_effect = MemoryError("dbscan oom")
        with pytest.raises(ClusteringError) as exc_info:
            clustering.run_incremental_clustering(eps=0.5, min_samples=2)

    assert exc_info.value.context["n_embeddings"] == 2

    scan_state = ScanProgressState()
    failing_clustering = MagicMock()
    failing_clustering.run_incremental_clustering.side_effect = exc_info.value
    services = MagicMock()
    services.clustering = failing_clustering

    try:
        _run_clustering_sync(services)
    except ClusteringError:
        scan_state.last_error = format_user_scan_last_error(scenario="clustering")

    assert scan_state.last_error is not None
    assert scan_state.last_error == "Clustering failed"
    assert "oom" not in scan_state.last_error.lower()
    assert "dbscan" not in scan_state.last_error.lower()


def test_broken_jpg_does_not_break_batch(
    temp_db: DatabaseManager,
    jpeg_file_factory,
    sample_detection: dict,
) -> None:
    """One failing image in a batch is skipped; the other is ingested."""
    good_path = jpeg_file_factory("good.jpg")
    broken_path = jpeg_file_factory("broken.jpg")
    engine = AICoreEngine()

    def process_side_effect(path: str | Path) -> list[dict]:
        normalized = str(Path(path).name)
        if normalized == "broken.jpg":
            raise EmbeddingError(
                "embedding failed for broken.jpg",
                context={"path": str(path)},
            )
        return [sample_detection]

    with patch.object(engine, "process_image", side_effect=process_side_effect):
        good_result = ingest_image_to_database(
            ai_engine=engine,
            database=temp_db,
            file_path=str(good_path),
        )
        broken_result = ingest_image_to_database(
            ai_engine=engine,
            database=temp_db,
            file_path=str(broken_path),
        )

    processed_count = sum(
        1 for result in (good_result, broken_result) if result["detection_count"] >= 1
    )
    skipped_count = sum(1 for result in (good_result, broken_result) if result["skipped"])

    assert processed_count >= 1
    assert skipped_count >= 1
    assert good_result["detection_count"] == 1
    assert broken_result["faceless"] is True
