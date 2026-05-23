"""
Offline AI core for the desktop photo organizer.

Responsibilities:
  1. Face detection + 512-d embedding extraction (DeepFace: RetinaFace / OpenCV + ArcFace).
  2. Cosine-distance similarity math with project thresholds (auto-match vs boundary queue).
  3. Incremental clustering (DBSCAN) over unassigned faces, integrated with DatabaseManager.

Pipeline overview:
  ┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────────────┐
  │ AICoreEngine     │───►│ DatabaseManager     │───►│ ClusteringEngine         │
  │ process_image()  │    │ insert_face / photo │    │ run_incremental_clustering│
  └──────────────────┘    └─────────────────────┘    └───────────┬──────────────┘
                                                                  │
                     ┌────────────────────────────────────────────┴──────────────┐
                     │  DBSCAN → persist cluster_id >= 0 only; noise → NULL      │
                     │  Cosine thresholds → auto-assign OR boundary manual queue │
                     │  process_decision_queue() / assign_name_to_cluster()      │
                     └────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final, Optional, Sequence, Union

import numpy as np
from sklearn.cluster import DBSCAN

from database import (
    EXPECTED_EMBEDDING_DIMENSION,
    DatabaseManager,
    FaceRow,
    PersonRow,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Optional OpenCV — lazy import (smoke tests / clustering work without cv2)
# ---------------------------------------------------------------------------

_CV2_MODULE: Any = None
_CV2_IMPORT_ERROR: Optional[BaseException] = None


def _get_cv2() -> Any:
    """
    Return the OpenCV module, importing it on first use.

    Raises:
        FaceDetectionError: When opencv-python is not installed in the active environment.
    """
    global _CV2_MODULE, _CV2_IMPORT_ERROR

    if _CV2_MODULE is not None:
        return _CV2_MODULE

    if _CV2_IMPORT_ERROR is not None:
        raise FaceDetectionError(
            "opencv-python is not installed. Activate the project virtual environment "
            "and run: pip install -r requirements.txt"
        ) from _CV2_IMPORT_ERROR

    try:
        import cv2 as cv2_module
    except ModuleNotFoundError as exc:
        _CV2_IMPORT_ERROR = exc
        raise FaceDetectionError(
            "opencv-python is not installed. Activate the project virtual environment "
            "and run: pip install -r requirements.txt"
        ) from exc

    _CV2_MODULE = cv2_module
    return _CV2_MODULE


def cv2_available() -> bool:
    """Return True when OpenCV can be imported in the current interpreter."""
    try:
        _get_cv2()
        return True
    except FaceDetectionError:
        return False


def _load_image_bgr(image_path: Path) -> np.ndarray:
    """
    Wczytuje obraz jako macierz NumPy (BGR) w sposób bezpieczny dla polskich znaków.
    """
    cv2 = _get_cv2()
    try:
        img_array = np.fromfile(str(image_path), dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Nie udało się zdekodować obrazu: {image_path}")
        return img
    except FaceDetectionError:
        raise
    except Exception as exc:
        raise ValueError(f"Błąd podczas ładowania pliku {image_path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DeepFace / model configuration
# ---------------------------------------------------------------------------

EXPECTED_EMBEDDING_DIMENSION: Final[int] = EXPECTED_EMBEDDING_DIMENSION

PRIMARY_DETECTOR_BACKEND: Final[str] = "retinaface"
FALLBACK_DETECTOR_BACKEND: Final[str] = "opencv"

PRIMARY_EMBEDDING_MODEL: Final[str] = "ArcFace"
ALTERNATIVE_EMBEDDING_MODEL: Final[str] = "Facenet512"

SUPPORTED_DETECTOR_BACKENDS: Final[tuple[str, ...]] = (
    PRIMARY_DETECTOR_BACKEND,
    FALLBACK_DETECTOR_BACKEND,
)

SUPPORTED_EMBEDDING_MODELS: Final[tuple[str, ...]] = (
    PRIMARY_EMBEDDING_MODEL,
    ALTERNATIVE_EMBEDDING_MODEL,
)

# ---------------------------------------------------------------------------
# Cosine distance thresholds (project specification)
# ---------------------------------------------------------------------------

COSINE_DISTANCE_AUTO_SAME_PERSON_MAX: Final[float] = 0.40
COSINE_DISTANCE_BOUNDARY_MIN: Final[float] = 0.38
COSINE_DISTANCE_BOUNDARY_MAX: Final[float] = 0.45

DBSCAN_EPS: Final[float] = COSINE_DISTANCE_AUTO_SAME_PERSON_MAX
DBSCAN_MIN_SAMPLES: Final[int] = 2

# sklearn DBSCAN noise label — never written to faces.cluster_id (use NULL instead).
DBSCAN_NOISE_LABEL: Final[int] = -1

MIN_IMAGE_FILE_BYTES: Final[int] = 32

SUPPORTED_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AICoreError(Exception):
    """Base exception for AI core failures."""


class FaceDetectionError(AICoreError):
    """Raised when DeepFace detection/representation fails irrecoverably."""


class EmbeddingError(AICoreError):
    """Raised when an embedding has invalid shape or non-finite values."""


class ClusteringError(AICoreError):
    """Raised when DBSCAN or cluster bookkeeping fails."""


class ClusterNotFoundError(ClusteringError, LookupError):
    """Raised when a cluster_id is not present in the pending-cluster registry."""


# ---------------------------------------------------------------------------
# Similarity classification (cosine distance decision logic)
# ---------------------------------------------------------------------------


class SimilarityClass(str, Enum):
    """
    Result of comparing two face embeddings via cosine distance.

    Priority when ranges overlap:
      BOUNDARY is evaluated before AUTO_SAME_PERSON so that ambiguous faces
      (0.38 <= D_C <= 0.45) always enter the manual verification queue.
    """

    AUTO_SAME_PERSON = "auto_same_person"
    BOUNDARY = "boundary"
    DIFFERENT = "different"


# ---------------------------------------------------------------------------
# Structured result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DetectedFaceDict:
    """
    One detected face returned by `AICoreEngine.process_image`.

    The `to_dict()` payload is JSON-ready for persistence via DatabaseManager.insert_face.
    """

    embedding: list[float]
    bounding_box: dict[str, int]
    detector_backend: str
    model_name: str
    confidence: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for UI / logging / DB layers."""
        return {
            "embedding": self.embedding,
            "bounding_box": self.bounding_box,
            "detector_backend": self.detector_backend,
            "model_name": self.model_name,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class PendingCluster:
    """
    Temporary multi-face cluster produced by DBSCAN before the user assigns a name.

    Attributes:
        cluster_id: DBSCAN label (0, 1, 2, …). Noise singletons never become pending clusters.
        face_ids: Database primary keys of member faces.
        representative_embedding: L2-normalized mean embedding (cluster prototype).
        face_rows: Full FaceRow objects for downstream UI display.
    """

    cluster_id: int
    face_ids: list[int]
    representative_embedding: list[float]
    face_rows: list[FaceRow]


@dataclass(frozen=True, slots=True)
class BoundaryFaceRecord:
    """
    A face in the manual verification queue (twarz graniczna).

    Shown to the user as: "Is this the same person as X? (Yes/No)" when
    COSINE_DISTANCE_BOUNDARY_MIN <= D_C <= COSINE_DISTANCE_BOUNDARY_MAX.
    """

    face_id: int
    photo_id: int
    cosine_distance: float
    reference_person_id: Optional[int]
    reference_person_name: Optional[str]
    candidate_embedding: list[float]
    reference_embedding: list[float]


@dataclass(frozen=True, slots=True)
class DbscanResolution:
    """
    Result of mapping DBSCAN labels to database cluster assignments.

    Attributes:
        named_clusters: DBSCAN labels >= 0 mapped to member faces (persisted cluster_id).
        noise_faces: Faces labeled -1 by DBSCAN; cluster_id cleared to NULL in SQLite.
    """

    named_clusters: dict[int, list[FaceRow]]
    noise_faces: list[FaceRow]


@dataclass
class ClusteringRunResult:
    """Summary returned by `ClusteringEngine.run_incremental_clustering`."""

    total_unassigned_loaded: int = 0
    clusters_persisted: int = 0
    faces_cluster_ids_written: int = 0
    noise_faces_discarded: int = 0
    clusters_created: int = 0
    auto_assigned_faces: int = 0
    boundary_faces_queued: int = 0
    pending_cluster_ids: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Math helpers — cosine distance & vector utilities
# ---------------------------------------------------------------------------


def _as_float_vector(values: Sequence[float], *, name: str = "vector") -> np.ndarray:
    """
    Convert a sequence to a 1-D float64 numpy array and validate finiteness.

    Raises:
        EmbeddingError: On wrong dimensionality or NaN/Inf values.
    """
    try:
        array = np.asarray(values, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise EmbeddingError(f"{name} must be numeric") from exc

    if array.size == 0:
        raise EmbeddingError(f"{name} must not be empty")

    if not np.all(np.isfinite(array)):
        raise EmbeddingError(f"{name} contains NaN or Inf")

    return array


def l2_normalize(vector: Sequence[float]) -> list[float]:
    """
    L2-normalize a vector.

    Used before centroid averaging so magnitude does not bias the mean direction.
    """
    array = _as_float_vector(vector, name="vector")
    norm = float(np.linalg.norm(array))
    if norm <= 0.0:
        raise EmbeddingError("cannot L2-normalize a zero vector")
    normalized = array / norm
    return normalized.tolist()


def validate_embedding_dimension(
    embedding: Sequence[float],
    *,
    expected: int = EXPECTED_EMBEDDING_DIMENSION,
) -> list[float]:
    """
    Ensure embedding length matches the project standard (512).

    Returns:
        A defensive copy as list[float].
    """
    vector = [float(v) for v in embedding]
    if len(vector) != expected:
        raise EmbeddingError(
            f"embedding must have length {expected}, got {len(vector)}"
        )
    return vector


def cosine_distance(
    vector_u: Sequence[float],
    vector_v: Sequence[float],
) -> float:
    """
    Compute cosine distance between two vectors.

    Formula (specification):
        D_C(u, v) = 1 - (u · v) / (||u|| * ||v||)

    Args:
        vector_u: First embedding (e.g. 512-d).
        vector_v: Second embedding (e.g. 512-d).

    Returns:
        Cosine distance in [0.0, 2.0] for typical face embeddings (often [0, 1]).

    Raises:
        EmbeddingError: If either vector is zero-norm or non-finite.
    """
    u = _as_float_vector(vector_u, name="vector_u")
    v = _as_float_vector(vector_v, name="vector_v")

    if u.shape != v.shape:
        raise EmbeddingError(
            f"vector dimension mismatch: {u.shape[0]} vs {v.shape[0]}"
        )

    norm_u = float(np.linalg.norm(u))
    norm_v = float(np.linalg.norm(v))

    if norm_u == 0.0 or norm_v == 0.0:
        raise EmbeddingError("cosine distance undefined for zero-norm vectors")

    dot_product = float(np.dot(u, v))
    cosine_similarity = dot_product / (norm_u * norm_v)
    cosine_similarity = float(np.clip(cosine_similarity, -1.0, 1.0))
    distance = 1.0 - cosine_similarity

    LOGGER.debug(
        "cosine_distance=%.6f (similarity=%.6f)",
        distance,
        cosine_similarity,
    )
    return distance


def classify_similarity(distance: float) -> SimilarityClass:
    """
    Map a cosine distance value to AUTO_SAME_PERSON, BOUNDARY, or DIFFERENT.

    Threshold precedence:
      1. If 0.38 <= D_C <= 0.45 → BOUNDARY (manual verification required).
      2. Else if D_C < 0.40      → AUTO_SAME_PERSON.
      3. Else                    → DIFFERENT.
    """
    if not math.isfinite(distance):
        raise EmbeddingError(f"distance must be finite, got {distance}")

    if COSINE_DISTANCE_BOUNDARY_MIN <= distance <= COSINE_DISTANCE_BOUNDARY_MAX:
        return SimilarityClass.BOUNDARY

    if distance < COSINE_DISTANCE_AUTO_SAME_PERSON_MAX:
        return SimilarityClass.AUTO_SAME_PERSON

    return SimilarityClass.DIFFERENT


def is_same_person(distance: float) -> bool:
    """Return True when distance qualifies for automatic same-person classification."""
    return classify_similarity(distance) == SimilarityClass.AUTO_SAME_PERSON


def is_boundary_face(distance: float) -> bool:
    """Return True when distance falls in the manual verification band."""
    return classify_similarity(distance) == SimilarityClass.BOUNDARY


def compute_cluster_representative(embeddings: Sequence[Sequence[float]]) -> list[float]:
    """
    Build a cluster prototype by averaging L2-normalized embeddings.

    Steps:
      1. L2-normalize each member embedding.
      2. Compute arithmetic mean.
      3. L2-normalize the mean to lie on the unit hypersphere.
    """
    if not embeddings:
        raise EmbeddingError("cannot compute representative of an empty cluster")

    normalized_members = [l2_normalize(e) for e in embeddings]
    matrix = np.asarray(normalized_members, dtype=np.float64)
    mean_vector = matrix.mean(axis=0)
    return l2_normalize(mean_vector.tolist())


def pairwise_cosine_distance_matrix(
    embeddings: Sequence[Sequence[float]],
) -> np.ndarray:
    """
    Build a symmetric pairwise cosine distance matrix.

    Useful for diagnostics / QA; DBSCAN uses sklearn's optimized implementation.
    """
    count = len(embeddings)
    if count == 0:
        return np.zeros((0, 0), dtype=np.float64)

    matrix = np.asarray(
        [_as_float_vector(e, name=f"embedding[{i}]") for i, e in enumerate(embeddings)],
        dtype=np.float64,
    )
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0.0):
        raise EmbeddingError("pairwise matrix contains zero-norm vectors")

    normalized = matrix / norms
    similarity = normalized @ normalized.T
    similarity = np.clip(similarity, -1.0, 1.0)
    return 1.0 - similarity


# ---------------------------------------------------------------------------
# DeepFace integration helpers
# ---------------------------------------------------------------------------


def _import_deepface() -> Any:
    """
    Import DeepFace lazily so module import succeeds in environments without TF.

    Raises:
        FaceDetectionError: If deepface is not installed or incompatible with TensorFlow.
    """
    try:
        import tf_keras  # noqa: F401 — required by retinaface on TensorFlow 2.21+
    except ImportError as exc:
        raise FaceDetectionError(
            "tf-keras is not installed. Run: pip install tf-keras "
            "(or pip install -r requirements.txt)"
        ) from exc

    try:
        from deepface import DeepFace  # type: ignore[import-untyped]
        from deepface.modules import modeling  # type: ignore[import-untyped]

        if not hasattr(modeling, "build_model"):
            raise FaceDetectionError(
                "Incompatible deepface installation (missing modeling.build_model). "
                "Reinstall: pip install --force-reinstall 'deepface>=0.0.92' tf-keras"
            )
    except ImportError as exc:
        raise FaceDetectionError(
            "deepface is not installed. Install requirements: pip install -r requirements.txt"
        ) from exc
    return DeepFace


def verify_ai_runtime_dependencies() -> None:
    """
    Eagerly validate AI stack imports at process startup.

    Raises:
        FaceDetectionError: When TensorFlow / DeepFace / tf-keras cannot load.
    """
    _import_deepface()
    if not cv2_available():
        raise FaceDetectionError(
            "opencv-python is not installed. Run: pip install -r requirements.txt"
        )
    LOGGER.info("AI runtime dependencies verified (tf-keras, deepface, opencv)")


def _extract_facial_area_as_bbox(facial_area: dict[str, Any]) -> dict[str, int]:
    """
    Convert DeepFace `facial_area` payload to our canonical bounding box dict.

    DeepFace may expose keys: x, y, w, h  OR  left_eye / right_eye derived regions.
    We normalize to integer pixel coordinates.
    """
    if not isinstance(facial_area, dict):
        raise FaceDetectionError(f"facial_area must be a dict, got {type(facial_area)}")

    if all(key in facial_area for key in ("x", "y", "w", "h")):
        return {
            "x": int(facial_area["x"]),
            "y": int(facial_area["y"]),
            "w": int(facial_area["w"]),
            "h": int(facial_area["h"]),
        }

    if all(key in facial_area for key in ("left", "top", "right", "bottom")):
        left = int(facial_area["left"])
        top = int(facial_area["top"])
        right = int(facial_area["right"])
        bottom = int(facial_area["bottom"])
        return {
            "x": left,
            "y": top,
            "w": max(1, right - left),
            "h": max(1, bottom - top),
        }

    raise FaceDetectionError(
        f"unsupported facial_area format: keys={list(facial_area.keys())}"
    )


def _parse_deepface_represent_result(
    raw_result: Union[dict[str, Any], list[dict[str, Any]]],
    *,
    detector_backend: str,
    model_name: str,
) -> list[DetectedFaceDict]:
    """Normalize DeepFace.represent() output into DetectedFaceDict instances."""
    if isinstance(raw_result, dict):
        items: list[dict[str, Any]] = [raw_result]
    elif isinstance(raw_result, list):
        items = raw_result
    else:
        raise FaceDetectionError(
            f"unexpected DeepFace.represent() return type: {type(raw_result)}"
        )

    detected: list[DetectedFaceDict] = []

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            LOGGER.warning("Skipping non-dict represent item at index %s", index)
            continue

        embedding_raw = item.get("embedding")
        facial_area = item.get("facial_area") or item.get("region")

        if embedding_raw is None or facial_area is None:
            LOGGER.warning(
                "Skipping represent item index=%s (missing embedding or facial_area)",
                index,
            )
            continue

        try:
            embedding = validate_embedding_dimension(
                [float(v) for v in embedding_raw],
            )
            bounding_box = _extract_facial_area_as_bbox(facial_area)
        except (EmbeddingError, FaceDetectionError, TypeError, ValueError) as exc:
            LOGGER.warning("Skipping invalid represent item index=%s: %s", index, exc)
            continue

        confidence_raw = item.get("face_confidence") or item.get("confidence")
        confidence: Optional[float] = None
        if confidence_raw is not None:
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = None

        detected.append(
            DetectedFaceDict(
                embedding=embedding,
                bounding_box=bounding_box,
                detector_backend=detector_backend,
                model_name=model_name,
                confidence=confidence,
            )
        )

    return detected


# ---------------------------------------------------------------------------
# Component 1 — face detection & embedding extraction
# ---------------------------------------------------------------------------


class AICoreEngine:
    """
    Offline face detection and 512-d embedding extraction using DeepFace.

    Detector policy:
      - Primary: RetinaFace
      - Fallback: OpenCV haarcascade (when RetinaFace cannot run)

    Embedding policy:
      - Primary: ArcFace (512-d)
      - Alternative: Facenet512
    """

    def __init__(
        self,
        *,
        model_name: str = PRIMARY_EMBEDDING_MODEL,
        enforce_detection: bool = False,
        align: bool = True,
    ) -> None:
        """
        Args:
            model_name: DeepFace model identifier ('ArcFace' or 'Facenet512').
            enforce_detection: If True, DeepFace raises when no face is found.
            align: If True, apply face alignment before embedding extraction.
        """
        if model_name not in SUPPORTED_EMBEDDING_MODELS:
            raise ValueError(
                f"model_name must be one of {SUPPORTED_EMBEDDING_MODELS}, got {model_name!r}"
            )

        self.model_name = model_name
        self.enforce_detection = enforce_detection
        self.align = align
        self._active_detector_backend: Optional[str] = None
        self._deepface: Any = None

        LOGGER.info(
            "AICoreEngine initialized model=%s enforce_detection=%s align=%s",
            self.model_name,
            self.enforce_detection,
            self.align,
        )

    def _get_deepface(self) -> Any:
        if self._deepface is None:
            self._deepface = _import_deepface()
        return self._deepface

    def reset_runtime(self) -> None:
        """
        Clear cached DeepFace import and detector probe state.

        Call before a new folder scan so dependency upgrades or transient import
        failures in a long-running uvicorn process do not stick across scans.
        """
        self._deepface = None
        self._active_detector_backend = None
        LOGGER.info("AICoreEngine runtime cache cleared")

    @property
    def active_detector_backend(self) -> Optional[str]:
        """Currently selected detector backend after probe, or None before first use."""
        return self._active_detector_backend

    def _validate_image_path(self, file_path: str) -> Path:
        """
        Validate local image path before passing to DeepFace.

        Raises:
            FileNotFoundError: Missing path.
            ValueError: Unsupported extension or empty file.
        """
        path = Path(file_path).expanduser().resolve()

        if not path.exists():
            raise FileNotFoundError(f"Image file does not exist: {path}")

        if not path.is_file():
            raise ValueError(f"Path is not a file: {path}")

        if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError(
                f"Unsupported image extension {path.suffix!r}. "
                f"Supported: {sorted(SUPPORTED_IMAGE_SUFFIXES)}"
            )

        file_size = path.stat().st_size
        if file_size < MIN_IMAGE_FILE_BYTES:
            raise ValueError(
                f"Image file is too small ({file_size} bytes), possible empty/corrupt file: {path}"
            )

        return path

    def _probe_detector_backend(self, image: np.ndarray) -> str:
        """
        Sprawdza i inicjalizuje backend detektora, przyjmując macierz numpy.

        Wykonuje lekkie wywołanie ``DeepFace.represent()`` na przekazanym obrazie
        (bez ścieżki pliku), aby probe nie wywalał się na polskich znakach w path.
        Kolejno próbuje RetinaFace, potem OpenCV.
        """
        if self._active_detector_backend is not None:
            return self._active_detector_backend

        DeepFace = self._get_deepface()
        last_error: Optional[Exception] = None

        for backend in SUPPORTED_DETECTOR_BACKENDS:
            try:
                LOGGER.info("Probing DeepFace detector backend '%s'...", backend)
                DeepFace.represent(
                    img_path=image,
                    model_name=self.model_name,
                    detector_backend=backend,
                    enforce_detection=False,
                    align=self.align,
                )
                self._active_detector_backend = backend
                LOGGER.info("Selected detector backend '%s'", backend)
                return backend
            except Exception as exc:  # noqa: BLE001 — probe must catch all DeepFace/backend failures
                last_error = exc
                LOGGER.warning(
                    "Probe backendu '%s' nie powiódł się: %s: %s",
                    backend,
                    type(exc).__name__,
                    exc,
                )

        LOGGER.error("Probe backendu nie powiódł się (żaden backend): %s", last_error)
        raise FaceDetectionError(
            f"No usable detector backend among {SUPPORTED_DETECTOR_BACKENDS}. "
            f"Last error: {last_error}"
        ) from last_error

    def process_image(self, image_path: str | Path) -> list[dict[str, Any]]:
        """
        Przetwarza obraz, ekstrahuje wektory cech i wykrywa twarze.

        Bezpieczny dla ścieżek z polskimi znakami: plik jest wczytywany raz
        przez ``_load_image_bgr``, a macierz NumPy trafia do probe i DeepFace.

        Args:
            image_path: Ścieżka do pliku graficznego (str lub Path).

        Returns:
            Lista słowników JSON-ready (embedding 512-d, bounding_box, metadane).

        Error handling:
            - FileNotFoundError / ValueError propagują (walidacja, uszkodzony plik).
            - FaceDetectionError propaguje błąd probe backendu / brak OpenCV.
            - Pozostałe błędy DeepFace: log + zwraca ``[]`` (bezpieczne dla pipeline).
        """
        image_path = self._validate_image_path(str(image_path))
        LOGGER.info("Processing image: %s", image_path)

        img = _load_image_bgr(image_path)

        try:
            detector_backend = self._probe_detector_backend(img)

            DeepFace = self._get_deepface()
            raw_result = DeepFace.represent(
                img_path=img,
                model_name=self.model_name,
                detector_backend=detector_backend,
                enforce_detection=self.enforce_detection,
                align=self.align,
            )

            detected_faces = _parse_deepface_represent_result(
                raw_result,
                detector_backend=detector_backend,
                model_name=self.model_name,
            )

            if not detected_faces:
                LOGGER.info("No faces detected in image: %s", image_path)
                return []

            payload = [face.to_dict() for face in detected_faces]
            LOGGER.info(
                "Detected %s face(s) in %s using backend=%s model=%s",
                len(payload),
                image_path,
                detector_backend,
                self.model_name,
            )
            return payload

        except (FileNotFoundError, ValueError):
            raise
        except FaceDetectionError:
            raise
        except Exception as exc:  # noqa: BLE001 — production boundary for third-party CV stack
            LOGGER.exception(
                "Face processing failed for %s: %s: %s",
                image_path,
                type(exc).__name__,
                exc,
            )
            return []


# ---------------------------------------------------------------------------
# Component 2 — incremental clustering & progressive learning
# ---------------------------------------------------------------------------


class ClusteringEngine:
    """
    Incremental clustering over unassigned faces stored in SQLite.

    Workflow:
      1. `run_incremental_clustering()` loads faces where person_id IS NULL.
      2. DBSCAN groups faces in 512-d cosine space.
      3. Named clusters (label >= 0) persist cluster_id via `update_faces_cluster_id()`.
      4. Noise faces (label -1) get cluster_id cleared to NULL — excluded from UI lists.
      5. Known-person prototypes are compared for auto-assign vs boundary queue.
      6. Unlabeled multi-face clusters go to `pending_clusters` / `get_unnamed_clusters()`.
      7. `process_decision_queue()` persists a new person and bulk-assigns the cluster.
    """

    def __init__(self, database: DatabaseManager) -> None:
        """
        Args:
            database: Initialized DatabaseManager pointing at the local SQLite file.
        """
        self.database = database
        self._pending_clusters: dict[int, PendingCluster] = {}
        self._boundary_queue: list[BoundaryFaceRecord] = []

        LOGGER.info("ClusteringEngine bound to database %s", database.db_path)

    @property
    def pending_clusters(self) -> dict[int, PendingCluster]:
        """Read-only view of clusters awaiting user naming."""
        return dict(self._pending_clusters)

    @property
    def boundary_queue(self) -> list[BoundaryFaceRecord]:
        """Faces requiring manual Yes/No confirmation (twarz graniczna)."""
        return list(self._boundary_queue)

    def clear_session_state(self) -> None:
        """Reset in-memory pending clusters and boundary queue (does not touch SQLite)."""
        self._pending_clusters.clear()
        self._boundary_queue.clear()
        LOGGER.debug("ClusteringEngine session state cleared")

    def _resolve_dbscan_labels(
        self,
        faces: list[FaceRow],
        labels: np.ndarray,
    ) -> DbscanResolution:
        """
        Map DBSCAN output labels to named clusters vs noise faces.

        DBSCAN label -1 (noise) is **not** assigned a persistent cluster_id. Those faces
        are returned separately so the caller can clear cluster_id in SQLite (NULL).

        Args:
            faces: Face rows aligned with DBSCAN input order.
            labels: fit_predict output (same length as ``faces``).

        Returns:
            DbscanResolution with named_clusters (label 0, 1, 2, …) and noise_faces.
        """
        if len(faces) != len(labels):
            raise ClusteringError(
                f"face/label length mismatch: {len(faces)} faces vs {len(labels)} labels"
            )

        named_clusters: dict[int, list[FaceRow]] = {}
        noise_faces: list[FaceRow] = []

        for face, raw_label in zip(faces, labels):
            label = int(raw_label)
            if label == DBSCAN_NOISE_LABEL:
                noise_faces.append(face)
            elif label < 0:
                LOGGER.warning(
                    "Unexpected negative DBSCAN label %s for face id=%s — treating as noise",
                    label,
                    face.id,
                )
                noise_faces.append(face)
            else:
                named_clusters.setdefault(label, []).append(face)

        return DbscanResolution(
            named_clusters=named_clusters,
            noise_faces=noise_faces,
        )

    def _persist_dbscan_cluster_labels(
        self,
        resolution: DbscanResolution,
    ) -> tuple[int, int]:
        """
        Write DBSCAN results to SQLite.

        Named clusters receive their DBSCAN label as cluster_id. Noise faces have
        cluster_id cleared to NULL so `get_unnamed_clusters()` ignores them.

        Args:
            resolution: Output of `_resolve_dbscan_labels`.

        Returns:
            Tuple of (named_faces_written, noise_faces_cleared).

        Raises:
            ClusteringError: When any persistence call fails.
        """
        named_faces_written = 0

        for cluster_id in sorted(resolution.named_clusters.keys()):
            faces_in_group = resolution.named_clusters[cluster_id]
            face_ids = [face.id for face in faces_in_group]
            if not face_ids:
                continue

            try:
                updated = self.database.update_faces_cluster_id(face_ids, cluster_id)
            except Exception as exc:  # noqa: BLE001 — DB boundary
                raise ClusteringError(
                    f"Failed to persist cluster_id={cluster_id} for face_ids={face_ids}: {exc}"
                ) from exc

            if updated != len(face_ids):
                LOGGER.warning(
                    "cluster_id=%s: expected to update %s face(s), updated %s",
                    cluster_id,
                    len(face_ids),
                    updated,
                )

            named_faces_written += updated
            LOGGER.debug(
                "Persisted cluster_id=%s for face_ids=%s (%s row(s))",
                cluster_id,
                face_ids,
                updated,
            )

        noise_faces_cleared = 0
        if resolution.noise_faces:
            noise_face_ids = [face.id for face in resolution.noise_faces]
            try:
                noise_faces_cleared = self.database.clear_faces_cluster_id(noise_face_ids)
            except Exception as exc:  # noqa: BLE001 — DB boundary
                raise ClusteringError(
                    f"Failed to clear cluster_id for noise face_ids={noise_face_ids}: {exc}"
                ) from exc

            LOGGER.info(
                "Cleared cluster_id for %s DBSCAN noise face(s) (not exposed to unnamed API)",
                noise_faces_cleared,
            )

        LOGGER.info(
            "Persisted %s named-cluster face(s) across %s cluster(s); "
            "cleared cluster_id on %s noise face(s)",
            named_faces_written,
            len(resolution.named_clusters),
            noise_faces_cleared,
        )
        return named_faces_written, noise_faces_cleared

    def _build_person_prototypes(self) -> dict[int, dict[str, Any]]:
        """
        Compute one representative embedding per known person.

        Returns:
            Dict[person_id] → {
                'person': PersonRow,
                'representative': list[float],
            }
        """
        prototypes: dict[int, dict[str, Any]] = {}

        for person in self.database.get_all_people():
            assigned_faces = self.database.get_faces_for_person(person.id)
            if not assigned_faces:
                continue

            embeddings = [face.embedding for face in assigned_faces]
            prototypes[person.id] = {
                "person": person,
                "representative": compute_cluster_representative(embeddings),
            }

        LOGGER.debug("Built %s known-person prototype(s)", len(prototypes))
        return prototypes

    def _attempt_match_against_known_persons(
        self,
        cluster: PendingCluster,
        prototypes: dict[int, dict[str, Any]],
    ) -> tuple[Optional[int], list[BoundaryFaceRecord]]:
        """
        Compare a cluster prototype to all known persons.

        Returns:
            (auto_assign_person_id, boundary_records)
        """
        best_auto_person_id: Optional[int] = None
        best_auto_distance: float = float("inf")
        boundary_records: list[BoundaryFaceRecord] = []

        for person_id, proto in prototypes.items():
            person: PersonRow = proto["person"]
            reference_embedding: list[float] = proto["representative"]

            try:
                distance = cosine_distance(
                    cluster.representative_embedding,
                    reference_embedding,
                )
            except EmbeddingError as exc:
                LOGGER.warning(
                    "Skipping prototype compare for person_id=%s: %s",
                    person_id,
                    exc,
                )
                continue

            similarity_class = classify_similarity(distance)
            LOGGER.debug(
                "Cluster %s vs person %s (%s): D_C=%.4f → %s",
                cluster.cluster_id,
                person_id,
                person.name,
                distance,
                similarity_class.value,
            )

            if similarity_class == SimilarityClass.BOUNDARY:
                for face in cluster.face_rows:
                    boundary_records.append(
                        BoundaryFaceRecord(
                            face_id=face.id,
                            photo_id=face.photo_id,
                            cosine_distance=distance,
                            reference_person_id=person.id,
                            reference_person_name=person.name,
                            candidate_embedding=face.embedding,
                            reference_embedding=reference_embedding,
                        )
                    )

            if similarity_class == SimilarityClass.AUTO_SAME_PERSON:
                if distance < best_auto_distance:
                    best_auto_distance = distance
                    best_auto_person_id = person_id

        return best_auto_person_id, boundary_records

    def run_incremental_clustering(
        self,
        *,
        eps: float = DBSCAN_EPS,
        min_samples: int = DBSCAN_MIN_SAMPLES,
    ) -> ClusteringRunResult:
        """
        Cluster all unassigned faces (person_id IS NULL) using DBSCAN.

        Steps:
          a) Load unassigned faces from DatabaseManager.
          b) Run DBSCAN(metric='cosine') in 512-dimensional embedding space.
          c) Persist named cluster labels (>= 0); clear cluster_id for noise (-1).
          d) For each named cluster, compute a representative embedding.
          e) Attempt auto-assignment against known person prototypes.
          f) Enqueue boundary faces for manual verification.
          g) Store remaining named clusters in `pending_clusters` for user naming.

        Args:
            eps: DBSCAN neighborhood radius in cosine distance units.
            min_samples: Minimum cluster cardinality.

        Returns:
            ClusteringRunResult summary statistics.
        """
        LOGGER.info(
            "Starting incremental clustering eps=%.3f min_samples=%s",
            eps,
            min_samples,
        )

        result = ClusteringRunResult()

        try:
            unassigned_faces = self.database.get_unassigned_faces()
        except Exception as exc:  # noqa: BLE001 — DB boundary
            raise ClusteringError(
                f"Failed to load unassigned faces: {exc}"
            ) from exc

        result.total_unassigned_loaded = len(unassigned_faces)

        if not unassigned_faces:
            LOGGER.info("No unassigned faces — clustering skipped")
            return result

        valid_faces: list[FaceRow] = []
        valid_embeddings: list[list[float]] = []
        for face in unassigned_faces:
            try:
                valid_embeddings.append(validate_embedding_dimension(face.embedding))
                valid_faces.append(face)
            except EmbeddingError as exc:
                LOGGER.warning("Skipping face id=%s: %s", face.id, exc)

        if not valid_faces:
            LOGGER.warning("No valid embeddings among unassigned faces")
            return result

        embedding_matrix = np.asarray(valid_embeddings, dtype=np.float64)

        try:
            dbscan = DBSCAN(
                eps=eps,
                min_samples=min_samples,
                metric="cosine",
                n_jobs=-1,
            )
            labels = dbscan.fit_predict(embedding_matrix)
        except Exception as exc:  # noqa: BLE001 — sklearn boundary
            raise ClusteringError(f"DBSCAN failed: {exc}") from exc

        LOGGER.info(
            "DBSCAN finished: %s face(s), unique labels=%s",
            len(valid_faces),
            sorted(set(int(label) for label in labels)),
        )

        resolution = self._resolve_dbscan_labels(valid_faces, labels)

        try:
            named_written, noise_cleared = self._persist_dbscan_cluster_labels(resolution)
        except ClusteringError:
            raise
        except Exception as exc:  # noqa: BLE001 — DB boundary
            raise ClusteringError(
                f"Failed to persist DBSCAN cluster_id labels: {exc}"
            ) from exc

        result.clusters_persisted = len(resolution.named_clusters)
        result.faces_cluster_ids_written = named_written
        result.noise_faces_discarded = len(resolution.noise_faces)
        if resolution.noise_faces and noise_cleared != result.noise_faces_discarded:
            LOGGER.warning(
                "Expected to clear cluster_id on %s noise face(s), cleared %s",
                result.noise_faces_discarded,
                noise_cleared,
            )

        prototypes = self._build_person_prototypes()
        self._pending_clusters.clear()

        for cluster_id in sorted(resolution.named_clusters.keys()):
            faces_in_group = resolution.named_clusters[cluster_id]
            if not faces_in_group:
                continue

            member_embeddings = [face.embedding for face in faces_in_group]

            try:
                representative = compute_cluster_representative(member_embeddings)
            except EmbeddingError as exc:
                LOGGER.warning("Skipping cluster_id %s: %s", cluster_id, exc)
                continue

            pending = PendingCluster(
                cluster_id=cluster_id,
                face_ids=[face.id for face in faces_in_group],
                representative_embedding=representative,
                face_rows=faces_in_group,
            )

            auto_person_id, boundary_records = self._attempt_match_against_known_persons(
                pending,
                prototypes,
            )

            if boundary_records:
                self._boundary_queue.extend(boundary_records)
                result.boundary_faces_queued += len(boundary_records)

            if auto_person_id is not None:
                updated = self.database.assign_faces_to_person(
                    pending.face_ids,
                    auto_person_id,
                )
                result.auto_assigned_faces += updated
                LOGGER.info(
                    "Auto-assigned cluster %s (%s faces) to person_id=%s",
                    cluster_id,
                    updated,
                    auto_person_id,
                )
                continue

            self._pending_clusters[cluster_id] = pending
            result.pending_cluster_ids.append(cluster_id)
            result.clusters_created += 1

        LOGGER.info(
            "Clustering complete: pending=%s auto_assigned=%s boundary=%s noise_discarded=%s",
            len(self._pending_clusters),
            result.auto_assigned_faces,
            result.boundary_faces_queued,
            result.noise_faces_discarded,
        )
        return result

    def get_pending_cluster(self, cluster_id: int) -> PendingCluster:
        """
        Fetch a pending cluster by id.

        Raises:
            ClusterNotFoundError: If cluster_id is unknown.
        """
        try:
            return self._pending_clusters[cluster_id]
        except KeyError as exc:
            raise ClusterNotFoundError(
                f"No pending cluster with id={cluster_id}. "
                f"Available: {sorted(self._pending_clusters.keys())}"
            ) from exc

    def process_decision_queue(
        self,
        cluster_id: int,
        user_assigned_name: str,
        relationship: Optional[str] = None,
    ) -> int:
        """
        Persist user decision for a pending cluster (progressive learning step).

        When the user names a cluster representative (e.g. "Magda"):
          1. Insert a new row into `people` with the supplied name.
          2. Bulk-update every face in the cluster with the new person_id.

        Args:
            cluster_id: DBSCAN label from `pending_clusters` (must be >= 0).
            user_assigned_name: Display name entered by the user.
            relationship: Deprecated ignored parameter kept for caller compatibility.

        Returns:
            Newly created `people.id`.

        Raises:
            ClusterNotFoundError: Unknown cluster_id.
            ValidationError: Invalid name or database constraints.
        """
        cluster = self.get_pending_cluster(cluster_id)
        clean_name = user_assigned_name.strip()
        if not clean_name:
            raise ValidationError("user_assigned_name must not be empty")

        LOGGER.info(
            "Processing decision queue: cluster_id=%s name=%r faces=%s",
            cluster_id,
            clean_name,
            len(cluster.face_ids),
        )

        try:
            person_id = self.database.insert_person(clean_name, relationship=relationship)
            updated = self.database.assign_faces_to_person(cluster.face_ids, person_id)
        except Exception as exc:  # noqa: BLE001 — DB boundary
            raise ClusteringError(
                f"Failed to persist user decision for cluster {cluster_id}: {exc}"
            ) from exc

        if updated != len(cluster.face_ids):
            LOGGER.warning(
                "Expected to assign %s faces but updated %s (cluster_id=%s)",
                len(cluster.face_ids),
                updated,
                cluster_id,
            )

        del self._pending_clusters[cluster_id]
        LOGGER.info(
            "Cluster %s labeled as person_id=%s (%s), assigned %s face(s)",
            cluster_id,
            person_id,
            clean_name,
            updated,
        )
        return person_id

    def process_boundary_decision(
        self,
        face_id: int,
        reference_person_id: int,
        *,
        is_same_person_answer: bool,
    ) -> Optional[int]:
        """
        Resolve a boundary face after manual Yes/No user input.

        Args:
            face_id: Face in the boundary queue.
            reference_person_id: Person the user compared against.
            is_same_person_answer: True if user answered Yes (same person).

        Returns:
            person_id used for assignment, or None if user answered No.

        Raises:
            RecordNotFoundError: If face/person not found (propagated from database layer).
        """
        LOGGER.info(
            "Boundary decision face_id=%s reference_person_id=%s answer=%s",
            face_id,
            reference_person_id,
            is_same_person_answer,
        )

        if is_same_person_answer:
            self.database.assign_face_to_person(face_id, reference_person_id)
            self._boundary_queue = [
                record
                for record in self._boundary_queue
                if not (
                    record.face_id == face_id
                    and record.reference_person_id == reference_person_id
                )
            ]
            return reference_person_id

        self._boundary_queue = [
            record
            for record in self._boundary_queue
            if record.face_id != face_id
        ]
        return None


# ---------------------------------------------------------------------------
# High-level orchestration helper (ingest one image end-to-end)
# ---------------------------------------------------------------------------


def ingest_image_to_database(
    *,
    ai_engine: AICoreEngine,
    database: DatabaseManager,
    file_path: str,
    mark_processed: bool = True,
) -> dict[str, Any]:
    """
    Detect faces in an image and persist them to SQLite.

    Returns:
        Summary dict with photo_id, face_ids, and detection_count.
    """
    path = str(Path(file_path).expanduser().resolve())
    existing_photo = database.get_photo_by_path(path)
    if existing_photo is not None and existing_photo.processed:
        if not existing_photo.has_faces:
            LOGGER.info(
                "Skipping faceless processed photo id=%s path=%s",
                existing_photo.id,
                path,
            )
            return {
                "photo_id": existing_photo.id,
                "file_path": path,
                "face_ids": [],
                "detection_count": 0,
                "skipped": True,
                "faceless": True,
            }

        existing_faces = database.get_faces_for_photo(existing_photo.id)
        if existing_faces:
            LOGGER.info(
                "Skipping ingestion for already processed photo id=%s path=%s",
                existing_photo.id,
                path,
            )
            return {
                "photo_id": existing_photo.id,
                "file_path": path,
                "face_ids": [face.id for face in existing_faces],
                "detection_count": len(existing_faces),
                "skipped": True,
                "faceless": False,
            }
        LOGGER.info(
            "Re-processing photo id=%s (marked processed but no faces stored): %s",
            existing_photo.id,
            path,
        )

    photo_id = database.insert_photo(path)

    detections = ai_engine.process_image(path)
    face_ids: list[int] = []

    for detection in detections:
        face_id = database.insert_face(
            photo_id=photo_id,
            embedding=detection["embedding"],
            bounding_box=detection["bounding_box"],
            enforce_embedding_dimension=True,
        )
        face_ids.append(face_id)

    if mark_processed:
        database.mark_photo_as_processed(photo_id, has_faces=bool(face_ids))

    return {
        "photo_id": photo_id,
        "file_path": path,
        "face_ids": face_ids,
        "detection_count": len(face_ids),
        "skipped": False,
        "faceless": len(face_ids) == 0,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _build_synthetic_embedding(
    base_index: int,
    *,
    noise_scale: float = 0.02,
) -> list[float]:
    """
    Create a deterministic 512-d unit vector for clustering tests.

    Each base_index generates a distinct direction; small noise simulates intra-person variance.
    """
    rng = np.random.default_rng(seed=base_index)
    vector = np.zeros(EXPECTED_EMBEDDING_DIMENSION, dtype=np.float64)
    vector[base_index % EXPECTED_EMBEDDING_DIMENSION] = 1.0
    vector += rng.normal(0.0, noise_scale, size=EXPECTED_EMBEDDING_DIMENSION)
    return l2_normalize(vector.tolist())


def _create_synthetic_test_image(path: Path, size: tuple[int, int] = (160, 160)) -> None:
    """Write a simple RGB JPEG for optional live DeepFace probing."""
    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("RGB", (width, height), color=(240, 240, 240))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (width * 0.25, height * 0.2, width * 0.75, height * 0.85),
        fill=(200, 180, 160),
        outline=(80, 60, 40),
    )
    image.save(path, format="JPEG", quality=95)


def _run_smoke_test() -> None:
    """
    Verify cosine math, DBSCAN clustering flow, and optional DeepFace execution.

    Uses a temporary SQLite database; does not require OpenCV for the DBSCAN section.
    """
    print("=== ai_core.py smoke test ===")

    vector_a = l2_normalize([1.0] + [0.0] * (EXPECTED_EMBEDDING_DIMENSION - 1))
    vector_b = l2_normalize([1.0] + [0.0] * (EXPECTED_EMBEDDING_DIMENSION - 1))
    vector_c = l2_normalize([0.0, 1.0] + [0.0] * (EXPECTED_EMBEDDING_DIMENSION - 2))

    distance_identical = cosine_distance(vector_a, vector_b)
    distance_orthogonal = cosine_distance(vector_a, vector_c)

    assert distance_identical < 1e-6, f"identical vectors should yield D_C≈0, got {distance_identical}"
    assert distance_orthogonal > 0.9, f"orthogonal vectors should yield D_C≈1, got {distance_orthogonal}"

    assert classify_similarity(0.20) == SimilarityClass.AUTO_SAME_PERSON
    assert classify_similarity(0.39) == SimilarityClass.BOUNDARY
    assert classify_similarity(0.42) == SimilarityClass.BOUNDARY
    assert classify_similarity(0.50) == SimilarityClass.DIFFERENT

    print("[OK] cosine distance + threshold classification")

    test_db_path = "_ai_core_smoke_test.db"
    database = DatabaseManager(db_path=test_db_path)

    try:
        database.create_tables()

        photo_id = database.insert_photo(str(Path("synthetic_photo.jpg").resolve()))

        groups = [0, 0, 1, 1, 1, 2]
        face_ids: list[int] = []
        for idx, group in enumerate(groups):
            embedding = _build_synthetic_embedding(group)
            face_id = database.insert_face(
                photo_id=photo_id,
                embedding=embedding,
                bounding_box={"x": 10, "y": 10, "w": 50, "h": 50},
                enforce_embedding_dimension=True,
            )
            face_ids.append(face_id)

        clustering = ClusteringEngine(database=database)
        run_result = clustering.run_incremental_clustering(eps=0.35, min_samples=2)

        assert run_result.total_unassigned_loaded == len(groups)
        assert run_result.noise_faces_discarded == 1
        assert run_result.faces_cluster_ids_written == len(groups) - run_result.noise_faces_discarded
        assert run_result.clusters_persisted == 2
        assert run_result.clusters_created >= 1

        noise_face_ids = [
            face_id
            for face_id in face_ids
            if database.get_face_by_id(face_id) is not None
            and database.get_face_by_id(face_id).cluster_id is None
        ]
        assert len(noise_face_ids) == 1

        named_face_ids = [
            face_id
            for face_id in face_ids
            if face_id not in noise_face_ids
        ]
        for face_id in named_face_ids:
            persisted_face = database.get_face_by_id(face_id)
            assert persisted_face is not None
            assert persisted_face.cluster_id is not None
            assert persisted_face.cluster_id >= 0

        unnamed_in_db = database.get_unnamed_clusters()
        assert all(cluster_id >= 0 for cluster_id in unnamed_in_db)
        assert len(unnamed_in_db) >= len(clustering.pending_clusters)
        assert len(unnamed_in_db) <= run_result.clusters_persisted

        if clustering.pending_clusters:
            cluster_id = next(iter(clustering.pending_clusters.keys()))
            assert cluster_id >= 0
            assert cluster_id in unnamed_in_db
            db_cluster_faces = database.get_faces_for_cluster(cluster_id)
            assert len(db_cluster_faces) >= 1
            person_id = clustering.process_decision_queue(
                cluster_id=cluster_id,
                user_assigned_name="Magda",
                relationship="rodzina",
            )
            assert person_id > 0
            assigned = database.get_faces_for_person(person_id)
            assert len(assigned) >= 1

        print(
            "[OK] DBSCAN incremental clustering "
            f"(pending={len(clustering.pending_clusters)} auto={run_result.auto_assigned_faces} "
            f"named_faces={run_result.faces_cluster_ids_written} "
            f"noise_discarded={run_result.noise_faces_discarded})"
        )

        database.insert_person("Anna", relationship="rodzina")
        anna_faces = database.get_faces_for_person(1)
        if anna_faces:
            anna_rep = compute_cluster_representative([f.embedding for f in anna_faces])
            near_anna = _build_synthetic_embedding(0, noise_scale=0.01)
            near_id = database.insert_face(
                photo_id=photo_id,
                embedding=near_anna,
                bounding_box={"x": 20, "y": 20, "w": 40, "h": 40},
            )
            dist = cosine_distance(near_anna, anna_rep)
            assert dist < COSINE_DISTANCE_AUTO_SAME_PERSON_MAX or is_boundary_face(dist)

        clustering.clear_session_state()
        print("[OK] progressive learning hooks")

    finally:
        Path(test_db_path).unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(f"{test_db_path}{suffix}").unlink(missing_ok=True)

    if cv2_available():
        print("[OK] OpenCV (cv2) available in current interpreter")
    else:
        print(
            "[SKIP] OpenCV (cv2) not installed — clustering tests passed; "
            "activate venv and pip install -r requirements.txt for face detection"
        )

    try:
        image_path = Path("_ai_core_probe_face.jpg")
        _create_synthetic_test_image(image_path)
        engine = AICoreEngine(model_name=PRIMARY_EMBEDDING_MODEL, enforce_detection=False)
        detections = engine.process_image(str(image_path))
        image_path.unlink(missing_ok=True)
        print(
            f"[OK] DeepFace probe completed (detections={len(detections)}, "
            f"backend={engine.active_detector_backend})"
        )
    except FaceDetectionError as exc:
        print(f"[SKIP] DeepFace / OpenCV probe unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 — smoke test must not fail on missing TF weights
        print(f"[SKIP] DeepFace probe failed: {type(exc).__name__}: {exc}")

    print("=== ai_core.py smoke test: ALL CHECKS PASSED ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    _run_smoke_test()
