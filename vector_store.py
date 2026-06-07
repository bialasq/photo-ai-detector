"""
FAISS vector store for face embedding search (tasks 2.2.1 / 2.2.2).

Uses ``faiss-cpu`` (IndexFlatIP + L2-normalized vectors = cosine similarity).
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np

from database import EXPECTED_EMBEDDING_DIMENSION, DatabaseManager
from log_privacy import hash_path_for_log

if TYPE_CHECKING:
    import faiss

LOGGER = logging.getLogger(__name__)

FAISS_INDEX_FILENAME = "faiss.index"
FAISS_IDS_SUFFIX = ".ids.pkl"


def get_faiss_index_path(app_data_dir: Path) -> Path:
    return app_data_dir / FAISS_INDEX_FILENAME


def _import_faiss() -> type:
    try:
        import faiss as faiss_module
    except ImportError as exc:
        raise RuntimeError(
            "faiss-cpu is not installed. Run: pip install faiss-cpu>=1.7.4"
        ) from exc
    return faiss_module


class FaceVectorStore:
    """In-memory FAISS index with face_id mapping and disk persistence."""

    def __init__(self, *, dim: int = EXPECTED_EMBEDDING_DIMENSION) -> None:
        faiss = _import_faiss()
        self.dim = dim
        self.index: faiss.IndexFlatIP = faiss.IndexFlatIP(dim)
        self.face_ids: list[int] = []

    def __len__(self) -> int:
        return len(self.face_ids)

    @staticmethod
    def _normalize(embedding: np.ndarray | list[float]) -> np.ndarray:
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vector.shape[0] != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding dimension must be {EXPECTED_EMBEDDING_DIMENSION}, got {vector.shape[0]}"
            )
        norm = float(np.linalg.norm(vector))
        if norm <= 0.0:
            raise ValueError("embedding norm must be positive")
        return (vector / norm).astype(np.float32)

    def add(self, face_id: int, embedding: np.ndarray | list[float]) -> None:
        """Append one L2-normalized embedding with its database face_id."""
        if face_id <= 0:
            raise ValueError("face_id must be positive")
        normalized = self._normalize(embedding)
        self.index.add(normalized.reshape(1, -1))
        self.face_ids.append(int(face_id))

    def search(
        self,
        embedding: np.ndarray | list[float],
        *,
        k: int = 10,
    ) -> list[tuple[int, float]]:
        """
        Return up to ``k`` (face_id, cosine_similarity) pairs sorted by similarity desc.
        """
        if len(self.face_ids) == 0:
            return []

        normalized = self._normalize(embedding)
        query_k = min(max(1, k), len(self.face_ids))
        distances, indices = self.index.search(normalized.reshape(1, -1), query_k)

        results: list[tuple[int, float]] = []
        for index, similarity in zip(indices[0], distances[0], strict=True):
            if index < 0:
                continue
            results.append((self.face_ids[int(index)], float(similarity)))
        return results

    def save(self, path: Path) -> None:
        faiss = _import_faiss()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(path))
        ids_path = path.with_suffix(FAISS_IDS_SUFFIX)
        ids_path.write_bytes(pickle.dumps(self.face_ids))
        LOGGER.info(
            "Saved FAISS index",
            extra={
                "ctx": {
                    "event": "faiss.save",
                    "vector_count": len(self.face_ids),
                    "path_hash": hash_path_for_log(path),
                }
            },
        )

    def load(self, path: Path) -> None:
        faiss = _import_faiss()
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"FAISS index not found: {path}")

        self.index = faiss.read_index(str(path))
        ids_path = path.with_suffix(FAISS_IDS_SUFFIX)
        if not ids_path.is_file():
            raise FileNotFoundError(f"FAISS id map not found: {ids_path}")

        self.face_ids = pickle.loads(ids_path.read_bytes())
        if self.index.ntotal != len(self.face_ids):
            raise ValueError(
                f"FAISS index size {self.index.ntotal} != id map length {len(self.face_ids)}"
            )
        LOGGER.info(
            "Loaded FAISS index",
            extra={
                "ctx": {
                    "event": "faiss.load",
                    "vector_count": len(self.face_ids),
                    "path_hash": hash_path_for_log(path),
                }
            },
        )

    @classmethod
    def rebuild_from_database(cls, database: DatabaseManager) -> FaceVectorStore:
        """Build a fresh index from all faces currently stored in SQLite."""
        store = cls()
        for face in database.iter_all_faces_with_embeddings():
            store.add(face.id, face.embedding)
        LOGGER.info("Rebuilt FAISS index from database (%s faces)", len(store))
        return store

    @classmethod
    def load_or_rebuild(cls, path: Path, database: DatabaseManager) -> FaceVectorStore:
        if path.is_file() and path.with_suffix(FAISS_IDS_SUFFIX).is_file():
            try:
                store = cls()
                store.load(path)
                return store
            except Exception as exc:  # noqa: BLE001 — rebuild on corruption
                LOGGER.warning("FAISS load failed (%s) — rebuilding from DB", exc)
        return cls.rebuild_from_database(database)


class VectorStoreProtocol(Protocol):
    def search(
        self,
        embedding: np.ndarray | list[float],
        *,
        k: int = 10,
    ) -> list[tuple[int, float]]: ...
