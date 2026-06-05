"""Unit tests for ai_core domain exception hierarchy (task 1.1.1)."""

from __future__ import annotations

from ai_core import (
    AICoreError,
    ClusterNotFoundError,
    ClusteringError,
    EmbeddingError,
    FaceDetectionError,
)


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
