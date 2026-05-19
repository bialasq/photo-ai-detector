"""
Local SQLite persistence layer for the offline photo organizer application.

This module is the single source of truth for on-disk metadata. Binary image files
stay on the filesystem; the database stores paths, face embeddings, bounding boxes,
and person labels.

Data flow (pipeline stages):
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
  │ insert_photo│ ──► │ insert_face  │ ──► │ DBSCAN / manual │ ──► │ search by    │
  │ (ingestion) │     │ (detection)  │     │ assign_face_*   │     │ get_photos_* │
  └─────────────┘     └──────────────┘     └─────────────────┘     └──────────────┘
        photos              faces              faces.person_id         JOIN people

Table relationships:
  photos (1) ──< (N) faces (N) >── (0..1) people
  Deleting a photo CASCADE-deletes its faces (see faces.photo_id FK).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Generator, Iterable, Optional, Sequence

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

DEFAULT_DB_PATH: Final[str] = "organizer.db"

# InsightFace / ArcFace standard output size for this project.
EXPECTED_EMBEDDING_DIMENSION: Final[int] = 512

BOUNDING_BOX_KEYS: Final[tuple[str, ...]] = ("x", "y", "w", "h")

# Optional vocabulary for UI / validation (relationship column is still free TEXT).
KNOWN_RELATIONSHIPS: Final[frozenset[str]] = frozenset(
    {
        "rodzina",
        "znajomy",
        "partner",
        "wspolpracownik",
        "inny",
    }
)

PHOTO_COLUMNS: Final[str] = "id, file_path, date_added, processed"
FACE_COLUMNS: Final[str] = "id, photo_id, embedding, bounding_box, person_id"
PERSON_COLUMNS: Final[str] = "id, name, relationship"


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class DatabaseError(Exception):
    """Base exception for all database-layer failures."""


class ValidationError(DatabaseError, ValueError):
    """Raised when caller-supplied arguments fail pre-insert validation."""


class RecordNotFoundError(DatabaseError, LookupError):
    """Raised when an UPDATE/DELETE/GET targets a row that does not exist."""


class DuplicateRecordError(DatabaseError):
    """Raised when a strict insert would violate a UNIQUE constraint."""


# ---------------------------------------------------------------------------
# Serialization helpers — faces.embedding & faces.bounding_box (TEXT / JSON)
# ---------------------------------------------------------------------------

def serialize_embedding(embedding: Sequence[float]) -> str:
    """
    Serialize a face embedding vector to compact JSON text.

    Args:
        embedding: Sequence of floats (typically length 512).

    Returns:
        JSON array string stored in `faces.embedding`.

    Raises:
        ValidationError: If embedding is empty or not numeric.
    """
    if not embedding:
        raise ValidationError("embedding must contain at least one float value")

    try:
        float_values = [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise ValidationError("embedding must contain only numeric values") from exc

    return json.dumps(float_values, separators=(",", ":"))


def deserialize_embedding(text: str) -> list[float]:
    """
    Deserialize `faces.embedding` JSON text back into a Python list of floats.

    Args:
        text: Raw TEXT column value from SQLite.

    Returns:
        List of floats suitable for numpy / clustering.

    Raises:
        ValueError: If JSON structure is invalid.
    """
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError("embedding JSON must decode to a JSON array (list)")
    if not payload:
        raise ValueError("embedding JSON array must not be empty")
    return [float(value) for value in payload]


def serialize_bounding_box(bounding_box: dict[str, int]) -> str:
    """
    Serialize a face bounding box to JSON text.

    Args:
        bounding_box: Dict with integer keys x, y, w, h (pixel coordinates).

    Returns:
        JSON object string stored in `faces.bounding_box`.

    Raises:
        ValidationError: On missing keys, non-integers, or non-positive size.
    """
    if not isinstance(bounding_box, dict):
        raise ValidationError("bounding_box must be a dict")

    missing = [key for key in BOUNDING_BOX_KEYS if key not in bounding_box]
    if missing:
        raise ValidationError(f"bounding_box missing required keys: {missing}")

    try:
        x = int(bounding_box["x"])
        y = int(bounding_box["y"])
        width = int(bounding_box["w"])
        height = int(bounding_box["h"])
    except (TypeError, ValueError) as exc:
        raise ValidationError("bounding_box values x, y, w, h must be integers") from exc

    if width <= 0 or height <= 0:
        raise ValidationError("bounding_box w and h must be strictly positive")

    if x < 0 or y < 0:
        raise ValidationError("bounding_box x and y must be non-negative")

    payload = {"x": x, "y": y, "w": width, "h": height}
    return json.dumps(payload, separators=(",", ":"))


def deserialize_bounding_box(text: str) -> dict[str, int]:
    """
    Deserialize `faces.bounding_box` JSON text.

    Args:
        text: Raw TEXT column value from SQLite.

    Returns:
        Dict with keys x, y, w, h.

    Raises:
        ValueError: If JSON structure is invalid.
    """
    payload = json.loads(text)
    if not isinstance(payload, dict) or not all(key in payload for key in BOUNDING_BOX_KEYS):
        raise ValueError(
            f"bounding_box JSON must be an object containing keys {BOUNDING_BOX_KEYS}"
        )
    return {key: int(payload[key]) for key in BOUNDING_BOX_KEYS}


# ---------------------------------------------------------------------------
# Typed row models — safe data exchange between SQL and application code
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PhotoRow:
    """
    One row from the `photos` table.

    Attributes:
        id: Primary key.
        file_path: Unique absolute or relative path to the image file.
        date_added: ISO-like timestamp string assigned by SQLite DEFAULT.
        processed: True when face detection / indexing completed (stored as 0/1 in DB).
    """

    id: int
    file_path: str
    date_added: str
    processed: bool

    @classmethod
    def from_sqlite_row(cls, row: sqlite3.Row) -> PhotoRow:
        """Build PhotoRow from sqlite3.Row, converting INTEGER 0/1 → bool."""
        return cls(
            id=int(row["id"]),
            file_path=str(row["file_path"]),
            date_added=str(row["date_added"]),
            processed=bool(row["processed"]),
        )


@dataclass(frozen=True, slots=True)
class FaceRow:
    """
    One row from the `faces` table with deserialized JSON columns.

    Attributes:
        id: Primary key of the face detection record.
        photo_id: Foreign key to `photos.id`.
        embedding: Deserialized float vector (e.g. 512 dimensions).
        bounding_box: Deserialized dict {x, y, w, h}.
        person_id: Foreign key to `people.id`, or None before labeling / clustering.
    """

    id: int
    photo_id: int
    embedding: list[float]
    bounding_box: dict[str, int]
    person_id: Optional[int]

    @classmethod
    def from_sqlite_row(cls, row: sqlite3.Row) -> FaceRow:
        """Build FaceRow from sqlite3.Row, parsing JSON text columns."""
        raw_person_id = row["person_id"]
        return cls(
            id=int(row["id"]),
            photo_id=int(row["photo_id"]),
            embedding=deserialize_embedding(str(row["embedding"])),
            bounding_box=deserialize_bounding_box(str(row["bounding_box"])),
            person_id=int(raw_person_id) if raw_person_id is not None else None,
        )

    @property
    def is_assigned(self) -> bool:
        """True when this face has been linked to a person."""
        return self.person_id is not None


@dataclass(frozen=True, slots=True)
class PersonRow:
    """
    One row from the `people` table.

    Attributes:
        id: Primary key.
        name: Display name used for search (`get_photos_by_names`).
        relationship: Optional category string (e.g. 'rodzina', 'znajomy').
    """

    id: int
    name: str
    relationship: Optional[str]

    @classmethod
    def from_sqlite_row(cls, row: sqlite3.Row) -> PersonRow:
        """Build PersonRow from sqlite3.Row."""
        return cls(
            id=int(row["id"]),
            name=str(row["name"]),
            relationship=row["relationship"],
        )


# ---------------------------------------------------------------------------
# Database manager
# ---------------------------------------------------------------------------

class DatabaseManager:
    """
    Production SQLite access layer for the offline photo organizer.

    Responsibilities:
      - Schema creation (`create_tables`)
      - Parameterized CRUD for photos, faces, and people
      - Intersection search across labeled faces
      - Enforcing referential integrity via PRAGMA foreign_keys

    Connection policy:
      - `_get_connection()` opens a raw connection (caller must close).
      - `_managed_connection()` is the preferred internal wrapper (commit/rollback/close).
      - Every connection executes `PRAGMA foreign_keys = ON` immediately after connect.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        """
        Args:
            db_path: Filesystem path to the SQLite database file.
        """
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValidationError("db_path must be a non-empty string")
        self.db_path: str = db_path.strip()

    # ------------------------------------------------------------------
    # Connection layer
    # ------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """
        Establish a new sqlite3 connection with required pragmas and row factory.

        Returns:
            Open connection. Caller is responsible for commit/rollback/close unless
            using `_managed_connection`.

        Side effects:
            - Enables foreign key enforcement (mandatory for CASCADE).
            - Sets WAL journal mode for safer concurrent reads on desktop.
        """
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        # Referential integrity (CASCADE, FK checks) — required per connection in SQLite.
        connection.execute("PRAGMA foreign_keys = ON;")
        # Desktop-friendly durability / concurrency defaults.
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.execute("PRAGMA synchronous = NORMAL;")
        return connection

    @contextmanager
    def _managed_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager wrapping `_get_connection()`.

        Yields:
            Active connection with an open transaction.

        On success:
            Commits and closes.

        On failure:
            Rolls back and re-raises.
        """
        connection = self._get_connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Validation helpers (centralized — keeps public methods consistent)
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_positive_int(value: int, field_name: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"{field_name} must be a positive int, got {value!r}")
        if value <= 0:
            raise ValidationError(f"{field_name} must be > 0, got {value}")
        return value

    @staticmethod
    def _validate_non_empty_str(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field_name} must be a str, got {type(value).__name__}")
        stripped = value.strip()
        if not stripped:
            raise ValidationError(f"{field_name} must not be empty or whitespace")
        return stripped

    def _validate_embedding(
        self,
        embedding: Sequence[float],
        *,
        enforce_dimension: bool = True,
    ) -> list[float]:
        if not isinstance(embedding, Sequence) or isinstance(embedding, (str, bytes)):
            raise ValidationError("embedding must be a sequence of floats")

        try:
            vector = [float(v) for v in embedding]
        except (TypeError, ValueError) as exc:
            raise ValidationError("embedding must contain only numeric values") from exc

        if not vector:
            raise ValidationError("embedding must not be empty")

        if enforce_dimension and len(vector) != EXPECTED_EMBEDDING_DIMENSION:
            raise ValidationError(
                f"embedding must have exactly {EXPECTED_EMBEDDING_DIMENSION} floats, "
                f"got {len(vector)}"
            )
        return vector

    @staticmethod
    def _validate_bounding_box(bounding_box: dict[str, int]) -> dict[str, int]:
        # serialize_bounding_box performs full structural validation.
        deserialize_bounding_box(serialize_bounding_box(bounding_box))
        return bounding_box

    @staticmethod
    def _normalize_name_list(names: Sequence[str]) -> list[str]:
        """
        Prepare person names for intersection queries.

        - Strips whitespace
        - Removes empty entries
        - Deduplicates while preserving first-seen order
        """
        if not isinstance(names, Sequence) or isinstance(names, (str, bytes)):
            raise ValidationError("names must be a sequence of strings")

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in names:
            if not isinstance(raw, str):
                raise ValidationError("each name must be a string")
            name = raw.strip()
            if not name:
                continue
            if name not in seen:
                seen.add(name)
                normalized.append(name)
        return normalized

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def create_tables(self) -> None:
        """
        Create all tables and performance indexes if they do not already exist.

        Execution order:
          1. photos  — root entity for filesystem paths
          2. people  — must exist before faces.person_id FK
          3. faces   — child of photos; optional link to people

        Indexes:
          - idx_photos_file_path        — fast lookup / UNIQUE enforcement aid
          - idx_photos_processed        — ingestion queue (processed = 0)
          - idx_faces_photo_id          — faces per photo
          - idx_faces_person_id         — faces per person
          - idx_faces_unassigned        — partial index for clustering queue
          - idx_people_name             — name search / intersection joins
        """
        db_file = Path(self.db_path)
        parent = db_file.parent
        if parent != Path(".") and str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)

        schema_script = """
        CREATE TABLE IF NOT EXISTS photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path   TEXT UNIQUE NOT NULL,
            date_added  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed   INTEGER NOT NULL DEFAULT 0 CHECK (processed IN (0, 1))
        );

        CREATE INDEX IF NOT EXISTS idx_photos_file_path
            ON photos(file_path);

        CREATE INDEX IF NOT EXISTS idx_photos_processed
            ON photos(processed);

        CREATE TABLE IF NOT EXISTS people (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            relationship  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_people_name
            ON people(name COLLATE NOCASE);

        CREATE TABLE IF NOT EXISTS faces (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id      INTEGER NOT NULL,
            embedding     TEXT NOT NULL,
            bounding_box  TEXT NOT NULL,
            person_id     INTEGER,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_faces_photo_id
            ON faces(photo_id);

        CREATE INDEX IF NOT EXISTS idx_faces_person_id
            ON faces(person_id);

        CREATE INDEX IF NOT EXISTS idx_faces_unassigned
            ON faces(photo_id)
            WHERE person_id IS NULL;
        """

        with self._managed_connection() as connection:
            connection.executescript(schema_script)

        LOGGER.info("Database schema initialized at %s", self.db_path)

    def database_exists(self) -> bool:
        """Return True if the database file is present on disk."""
        return Path(self.db_path).is_file()

    # ------------------------------------------------------------------
    # Photos — create / update / delete / read
    # ------------------------------------------------------------------

    def insert_photo(self, file_path: str) -> int:
        """
        Register a photo by filesystem path (idempotent).

        Strategy:
          - `INSERT OR IGNORE` on UNIQUE `file_path`
          - If ignored (duplicate path), SELECT existing `id` and return it

        Args:
            file_path: Unique path to the image file.

        Returns:
            Primary key `photos.id` (new or existing).

        Raises:
            ValidationError: Empty path.
            RuntimeError: Unexpected state after INSERT OR IGNORE.
        """
        normalized_path = self._validate_non_empty_str(file_path, "file_path")

        with self._managed_connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO photos (file_path, processed)
                VALUES (?, 0)
                """,
                (normalized_path,),
            )

            if cursor.rowcount > 0:
                new_id = int(cursor.lastrowid)
                LOGGER.debug("Inserted photo id=%s path=%s", new_id, normalized_path)
                return new_id

            existing = connection.execute(
                """
                SELECT id
                FROM photos
                WHERE file_path = ?
                """,
                (normalized_path,),
            ).fetchone()

            if existing is None:
                raise RuntimeError(
                    "insert_photo failed: INSERT OR IGNORE did not insert, "
                    "and SELECT by file_path returned no row"
                )

            existing_id = int(existing["id"])
            LOGGER.debug(
                "Photo already registered id=%s path=%s", existing_id, normalized_path
            )
            return existing_id

    def mark_photo_as_processed(self, photo_id: int) -> None:
        """
        Mark a photo as fully processed (`processed = 1`).

        Args:
            photo_id: Target `photos.id`.

        Raises:
            ValidationError: Invalid id.
            RecordNotFoundError: No matching photo.
        """
        valid_id = self._validate_positive_int(photo_id, "photo_id")

        with self._managed_connection() as connection:
            result = connection.execute(
                """
                UPDATE photos
                SET processed = 1
                WHERE id = ?
                """,
                (valid_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No photo found with id={valid_id}")

        LOGGER.debug("Marked photo id=%s as processed", valid_id)

    def reset_photo_processed(self, photo_id: int) -> None:
        """
        Reset `processed` to 0 so the ingestion pipeline can re-run on this photo.

        Args:
            photo_id: Target `photos.id`.

        Raises:
            RecordNotFoundError: No matching photo.
        """
        valid_id = self._validate_positive_int(photo_id, "photo_id")

        with self._managed_connection() as connection:
            result = connection.execute(
                """
                UPDATE photos
                SET processed = 0
                WHERE id = ?
                """,
                (valid_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No photo found with id={valid_id}")

    def get_photo_by_id(self, photo_id: int) -> Optional[PhotoRow]:
        """Fetch one photo by primary key, or None if not found."""
        valid_id = self._validate_positive_int(photo_id, "photo_id")

        with self._managed_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {PHOTO_COLUMNS}
                FROM photos
                WHERE id = ?
                """,
                (valid_id,),
            ).fetchone()

        return PhotoRow.from_sqlite_row(row) if row is not None else None

    def get_photo_by_path(self, file_path: str) -> Optional[PhotoRow]:
        """Fetch one photo by unique `file_path`, or None if not found."""
        normalized_path = self._validate_non_empty_str(file_path, "file_path")

        with self._managed_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {PHOTO_COLUMNS}
                FROM photos
                WHERE file_path = ?
                """,
                (normalized_path,),
            ).fetchone()

        return PhotoRow.from_sqlite_row(row) if row is not None else None

    def get_all_photos(
        self,
        *,
        processed_only: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[PhotoRow]:
        """
        List photos with optional filtering and pagination.

        Args:
            processed_only: If True, only processed; if False, only unprocessed; if None, all.
            limit: Maximum rows (None = no limit).
            offset: SQL OFFSET (must be >= 0).
        """
        if offset < 0:
            raise ValidationError("offset must be >= 0")
        if limit is not None and limit <= 0:
            raise ValidationError("limit must be > 0 when provided")

        clauses: list[str] = []
        params: list[object] = []

        if processed_only is True:
            clauses.append("processed = 1")
        elif processed_only is False:
            clauses.append("processed = 0")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset > 0:
            limit_sql = "LIMIT -1 OFFSET ?"
            params.append(offset)

        query = f"""
            SELECT {PHOTO_COLUMNS}
            FROM photos
            {where_sql}
            ORDER BY date_added DESC, id DESC
            {limit_sql}
        """

        with self._managed_connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()

        return [PhotoRow.from_sqlite_row(row) for row in rows]

    def get_unprocessed_photos(self) -> list[PhotoRow]:
        """
        Return photos where `processed = 0` (face-detection / indexing queue).

        Ordered oldest-first so the pipeline processes files in stable ingestion order.
        """
        with self._managed_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {PHOTO_COLUMNS}
                FROM photos
                WHERE processed = 0
                ORDER BY date_added ASC, id ASC
                """
            ).fetchall()

        return [PhotoRow.from_sqlite_row(row) for row in rows]

    def count_photos(self, *, processed: Optional[bool] = None) -> int:
        """Count photos, optionally filtered by processed flag."""
        clause = ""
        params: tuple[object, ...] = ()
        if processed is True:
            clause = "WHERE processed = 1"
        elif processed is False:
            clause = "WHERE processed = 0"

        with self._managed_connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS cnt FROM photos {clause}",
                params,
            ).fetchone()

        return int(row["cnt"]) if row is not None else 0

    def delete_photo(self, photo_id: int) -> None:
        """
        Delete a photo; associated faces are removed via ON DELETE CASCADE.

        Raises:
            RecordNotFoundError: If photo_id does not exist.
        """
        valid_id = self._validate_positive_int(photo_id, "photo_id")

        with self._managed_connection() as connection:
            result = connection.execute(
                "DELETE FROM photos WHERE id = ?",
                (valid_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No photo found with id={valid_id}")

        LOGGER.info("Deleted photo id=%s (faces cascaded)", valid_id)

    # ------------------------------------------------------------------
    # People — create / update / delete / read
    # ------------------------------------------------------------------

    def insert_person(
        self,
        name: str,
        relationship: Optional[str] = None,
        *,
        validate_relationship: bool = False,
    ) -> int:
        """
        Insert a person record.

        Args:
            name: Display name (used by `get_photos_by_names`).
            relationship: Optional label (e.g. 'rodzina').
            validate_relationship: If True, relationship must be in KNOWN_RELATIONSHIPS.

        Returns:
            Generated `people.id`.
        """
        clean_name = self._validate_non_empty_str(name, "name")
        clean_relationship: Optional[str] = None

        if relationship is not None:
            clean_relationship = self._validate_non_empty_str(relationship, "relationship")
            if validate_relationship and clean_relationship not in KNOWN_RELATIONSHIPS:
                raise ValidationError(
                    f"relationship must be one of {sorted(KNOWN_RELATIONSHIPS)}, "
                    f"got {clean_relationship!r}"
                )

        with self._managed_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO people (name, relationship)
                VALUES (?, ?)
                """,
                (clean_name, clean_relationship),
            )
            new_id = int(cursor.lastrowid)

        LOGGER.debug("Inserted person id=%s name=%s", new_id, clean_name)
        return new_id

    def update_person(
        self,
        person_id: int,
        *,
        name: Optional[str] = None,
        relationship: Optional[str] = None,
        clear_relationship: bool = False,
    ) -> None:
        """
        Update person fields. At least one of name / relationship must be provided.

        Args:
            person_id: Target row.
            name: New name (optional).
            relationship: New relationship string (optional).
            clear_relationship: If True, set relationship column to NULL.
        """
        valid_id = self._validate_positive_int(person_id, "person_id")

        if name is None and relationship is None and not clear_relationship:
            raise ValidationError(
                "update_person requires name, relationship, or clear_relationship=True"
            )

        sets: list[str] = []
        params: list[object] = []

        if name is not None:
            sets.append("name = ?")
            params.append(self._validate_non_empty_str(name, "name"))

        if clear_relationship:
            sets.append("relationship = NULL")
        elif relationship is not None:
            sets.append("relationship = ?")
            params.append(self._validate_non_empty_str(relationship, "relationship"))

        params.append(valid_id)

        with self._managed_connection() as connection:
            result = connection.execute(
                f"""
                UPDATE people
                SET {", ".join(sets)}
                WHERE id = ?
                """,
                tuple(params),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No person found with id={valid_id}")

    def get_person_by_id(self, person_id: int) -> Optional[PersonRow]:
        """Fetch one person by id, or None."""
        valid_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {PERSON_COLUMNS}
                FROM people
                WHERE id = ?
                """,
                (valid_id,),
            ).fetchone()

        return PersonRow.from_sqlite_row(row) if row is not None else None

    def get_person_by_name(self, name: str) -> Optional[PersonRow]:
        """
        Fetch the first person with an exact name match (case-sensitive).

        For case-insensitive lookup, normalize names at insert time or extend this method.
        """
        clean_name = self._validate_non_empty_str(name, "name")

        with self._managed_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {PERSON_COLUMNS}
                FROM people
                WHERE name = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (clean_name,),
            ).fetchone()

        return PersonRow.from_sqlite_row(row) if row is not None else None

    def get_all_people(self) -> list[PersonRow]:
        """List all people ordered by name (case-insensitive)."""
        with self._managed_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {PERSON_COLUMNS}
                FROM people
                ORDER BY name COLLATE NOCASE ASC, id ASC
                """
            ).fetchall()

        return [PersonRow.from_sqlite_row(row) for row in rows]

    def count_people(self) -> int:
        """Return total number of person records."""
        with self._managed_connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS cnt FROM people").fetchone()
        return int(row["cnt"]) if row is not None else 0

    def delete_person(self, person_id: int) -> None:
        """
        Delete a person. Linked faces get person_id SET NULL (ON DELETE SET NULL).

        Raises:
            RecordNotFoundError: If person_id does not exist.
        """
        valid_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            result = connection.execute(
                "DELETE FROM people WHERE id = ?",
                (valid_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No person found with id={valid_id}")

        LOGGER.info("Deleted person id=%s (face links nulled)", valid_id)

    # ------------------------------------------------------------------
    # Faces — create / update / delete / read
    # ------------------------------------------------------------------

    def insert_face(
        self,
        photo_id: int,
        embedding: list[float],
        bounding_box: dict[str, int],
        *,
        enforce_embedding_dimension: bool = True,
    ) -> int:
        """
        Persist one detected face for a photo.

        Args:
            photo_id: Parent photo primary key.
            embedding: Face vector (default: must be exactly 512 floats).
            bounding_box: Dict with x, y, w, h pixel coordinates.
            enforce_embedding_dimension: Set False for tests / alternate models.

        Returns:
            Generated `faces.id`.

        Raises:
            RecordNotFoundError: Parent photo does not exist.
            ValidationError: Invalid embedding or bounding box.
        """
        valid_photo_id = self._validate_positive_int(photo_id, "photo_id")
        vector = self._validate_embedding(
            embedding,
            enforce_dimension=enforce_embedding_dimension,
        )
        valid_box = self._validate_bounding_box(bounding_box)

        embedding_json = serialize_embedding(vector)
        bounding_box_json = serialize_bounding_box(valid_box)

        with self._managed_connection() as connection:
            parent = connection.execute(
                "SELECT id FROM photos WHERE id = ?",
                (valid_photo_id,),
            ).fetchone()
            if parent is None:
                raise RecordNotFoundError(f"No photo found with id={valid_photo_id}")

            cursor = connection.execute(
                """
                INSERT INTO faces (photo_id, embedding, bounding_box, person_id)
                VALUES (?, ?, ?, NULL)
                """,
                (valid_photo_id, embedding_json, bounding_box_json),
            )
            face_id = int(cursor.lastrowid)

        LOGGER.debug(
            "Inserted face id=%s for photo_id=%s", face_id, valid_photo_id
        )
        return face_id

    def assign_face_to_person(self, face_id: int, person_id: int) -> None:
        """
        Link a face row to an existing person (after clustering or manual labeling).

        Args:
            face_id: Target face row.
            person_id: Target person row.

        Raises:
            RecordNotFoundError: Face or person does not exist.
        """
        valid_face_id = self._validate_positive_int(face_id, "face_id")
        valid_person_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            person = connection.execute(
                "SELECT id FROM people WHERE id = ?",
                (valid_person_id,),
            ).fetchone()
            if person is None:
                raise RecordNotFoundError(f"No person found with id={valid_person_id}")

            result = connection.execute(
                """
                UPDATE faces
                SET person_id = ?
                WHERE id = ?
                """,
                (valid_person_id, valid_face_id),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No face found with id={valid_face_id}")

        LOGGER.debug(
            "Assigned face id=%s to person id=%s", valid_face_id, valid_person_id
        )

    def unassign_face(self, face_id: int) -> None:
        """
        Set `faces.person_id` back to NULL (e.g. after mis-label correction).

        Raises:
            RecordNotFoundError: Face does not exist.
        """
        valid_face_id = self._validate_positive_int(face_id, "face_id")

        with self._managed_connection() as connection:
            result = connection.execute(
                """
                UPDATE faces
                SET person_id = NULL
                WHERE id = ?
                """,
                (valid_face_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No face found with id={valid_face_id}")

    def assign_faces_to_person(
        self,
        face_ids: Iterable[int],
        person_id: int,
    ) -> int:
        """
        Batch-assign multiple faces to one person in a single transaction.

        Returns:
            Number of face rows updated.
        """
        valid_person_id = self._validate_positive_int(person_id, "person_id")
        id_list = [
            self._validate_positive_int(face_id, "face_id") for face_id in face_ids
        ]
        if not id_list:
            return 0

        placeholders = ", ".join("?" for _ in id_list)

        with self._managed_connection() as connection:
            person = connection.execute(
                "SELECT id FROM people WHERE id = ?",
                (valid_person_id,),
            ).fetchone()
            if person is None:
                raise RecordNotFoundError(f"No person found with id={valid_person_id}")

            result = connection.execute(
                f"""
                UPDATE faces
                SET person_id = ?
                WHERE id IN ({placeholders})
                """,
                (valid_person_id, *id_list),
            )
            updated = int(result.rowcount)

        LOGGER.debug(
            "Batch-assigned %s face(s) to person id=%s", updated, valid_person_id
        )
        return updated

    def get_face_by_id(self, face_id: int) -> Optional[FaceRow]:
        """Fetch one face by id, or None."""
        valid_id = self._validate_positive_int(face_id, "face_id")

        with self._managed_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE id = ?
                """,
                (valid_id,),
            ).fetchone()

        return FaceRow.from_sqlite_row(row) if row is not None else None

    def get_faces_for_photo(self, photo_id: int) -> list[FaceRow]:
        """Return all face detections belonging to a photo."""
        valid_photo_id = self._validate_positive_int(photo_id, "photo_id")

        with self._managed_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE photo_id = ?
                ORDER BY id ASC
                """,
                (valid_photo_id,),
            ).fetchall()

        return [FaceRow.from_sqlite_row(row) for row in rows]

    def get_faces_for_person(self, person_id: int) -> list[FaceRow]:
        """Return all faces currently assigned to a person."""
        valid_person_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE person_id = ?
                ORDER BY photo_id ASC, id ASC
                """,
                (valid_person_id,),
            ).fetchall()

        return [FaceRow.from_sqlite_row(row) for row in rows]

    def get_unassigned_faces(self) -> list[FaceRow]:
        """
        Return faces with `person_id IS NULL`.

        Primary input queue for DBSCAN clustering: embeddings exist, labels do not.
        Uses partial index idx_faces_unassigned when present.
        """
        with self._managed_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE person_id IS NULL
                ORDER BY photo_id ASC, id ASC
                """
            ).fetchall()

        return [FaceRow.from_sqlite_row(row) for row in rows]

    def count_unassigned_faces(self) -> int:
        """Return number of faces awaiting clustering / manual assignment."""
        with self._managed_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM faces
                WHERE person_id IS NULL
                """
            ).fetchone()
        return int(row["cnt"]) if row is not None else 0

    def delete_face(self, face_id: int) -> None:
        """Delete a single face row."""
        valid_id = self._validate_positive_int(face_id, "face_id")

        with self._managed_connection() as connection:
            result = connection.execute(
                "DELETE FROM faces WHERE id = ?",
                (valid_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No face found with id={valid_id}")

    def delete_faces_for_photo(self, photo_id: int) -> int:
        """
        Delete all faces for a photo (without deleting the photo itself).

        Returns:
            Number of deleted face rows.
        """
        valid_photo_id = self._validate_positive_int(photo_id, "photo_id")

        with self._managed_connection() as connection:
            result = connection.execute(
                "DELETE FROM faces WHERE photo_id = ?",
                (valid_photo_id,),
            )
            return int(result.rowcount)

    # ------------------------------------------------------------------
    # Search — intersection across people names
    # ------------------------------------------------------------------

    def get_photos_by_names(self, names: list[str]) -> list[PhotoRow]:
        """
        Return photos that contain ALL listed people simultaneously (set intersection).

        Matching rules:
          - Names are stripped and empty strings ignored.
          - Duplicate names in the input list are deduplicated.
          - A photo matches iff COUNT(DISTINCT pe.name) for assigned faces on that photo
            equals the number of required distinct names, and each name is present.

        Example:
          names=["Anna", "Bartek"] → only photos where BOTH Anna and Bartek appear
          (each via at least one labeled face on the same photo).

        Args:
            names: List of person display names.

        Returns:
            Matching PhotoRow list, newest first.
        """
        required_names = self._normalize_name_list(names)
        if not required_names:
            return []

        name_placeholders = ", ".join("?" for _ in required_names)
        required_count = len(required_names)

        # Intersection via correlated subquery — every required name must appear on photo.
        query = f"""
            SELECT {PHOTO_COLUMNS}
            FROM photos AS p
            WHERE (
                SELECT COUNT(DISTINCT pe.name)
                FROM faces AS f
                INNER JOIN people AS pe ON pe.id = f.person_id
                WHERE f.photo_id = p.id
                  AND pe.name IN ({name_placeholders})
            ) = ?
            ORDER BY p.date_added DESC, p.id DESC
        """

        parameters: tuple[object, ...] = tuple(required_names) + (required_count,)

        with self._managed_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        results = [PhotoRow.from_sqlite_row(row) for row in rows]
        LOGGER.debug(
            "get_photos_by_names(%r) → %s photo(s)", required_names, len(results)
        )
        return results

    def get_photos_by_person_ids(self, person_ids: Sequence[int]) -> list[PhotoRow]:
        """
        Intersection search by `people.id` instead of display name.

        Useful when multiple DB rows could theoretically share a name string.
        """
        if not isinstance(person_ids, Sequence) or isinstance(person_ids, (str, bytes)):
            raise ValidationError("person_ids must be a sequence of integers")

        unique_ids: list[int] = []
        seen: set[int] = set()
        for raw_id in person_ids:
            valid_id = self._validate_positive_int(int(raw_id), "person_id")
            if valid_id not in seen:
                seen.add(valid_id)
                unique_ids.append(valid_id)

        if not unique_ids:
            return []

        id_placeholders = ", ".join("?" for _ in unique_ids)
        required_count = len(unique_ids)

        query = f"""
            SELECT {PHOTO_COLUMNS}
            FROM photos AS p
            WHERE (
                SELECT COUNT(DISTINCT f.person_id)
                FROM faces AS f
                WHERE f.photo_id = p.id
                  AND f.person_id IN ({id_placeholders})
            ) = ?
            ORDER BY p.date_added DESC, p.id DESC
        """

        parameters: tuple[object, ...] = tuple(unique_ids) + (required_count,)

        with self._managed_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return [PhotoRow.from_sqlite_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Maintenance / diagnostics
    # ------------------------------------------------------------------

    def vacuum(self) -> None:
        """Rebuild database file and reclaim unused space (maintenance operation)."""
        with self._managed_connection() as connection:
            connection.execute("VACUUM")

    def integrity_check(self) -> str:
        """
        Run SQLite PRAGMA integrity_check.

        Returns:
            'ok' when database is healthy, otherwise an error description string.
        """
        with self._managed_connection() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row is not None else "unknown"

    def get_schema_version_info(self) -> dict[str, int]:
        """
        Return row counts per table (quick health snapshot for logging / UI).
        """
        with self._managed_connection() as connection:
            photos = connection.execute("SELECT COUNT(*) FROM photos").fetchone()
            people = connection.execute("SELECT COUNT(*) FROM people").fetchone()
            faces = connection.execute("SELECT COUNT(*) FROM faces").fetchone()
            unassigned = connection.execute(
                "SELECT COUNT(*) FROM faces WHERE person_id IS NULL"
            ).fetchone()

        return {
            "photos": int(photos[0]),
            "people": int(people[0]),
            "faces": int(faces[0]),
            "faces_unassigned": int(unassigned[0]),
        }


# ---------------------------------------------------------------------------
# Smoke test (run: python database.py)
# ---------------------------------------------------------------------------

def _run_smoke_test() -> None:
    """
    Exhaustive in-memory integration test for schema, CRUD, and intersection logic.

    Uses a temporary file in the working directory; removed in finally block.
    """
    test_db_path = "_organizer_smoke_test.db"
    manager = DatabaseManager(db_path=test_db_path)

    try:
        assert not manager.database_exists()
        manager.create_tables()
        assert manager.database_exists()
        assert manager.integrity_check() == "ok"

        solo_path = r"C:\Photos\event\solo_001.jpg"
        duo_path = r"C:\Photos\event\duo_002.jpg"

        solo_photo_id = manager.insert_photo(solo_path)
        duo_photo_id = manager.insert_photo(duo_path)
        assert manager.insert_photo(duo_path) == duo_photo_id  # idempotent

        assert manager.get_photo_by_path(solo_path) is not None
        assert manager.get_photo_by_id(solo_photo_id) is not None

        anna_id = manager.insert_person("Anna", relationship="rodzina")
        bartek_id = manager.insert_person("Bartek", relationship="znajomy")

        anna_face_solo = manager.insert_face(
            photo_id=solo_photo_id,
            embedding=[0.01] * EXPECTED_EMBEDDING_DIMENSION,
            bounding_box={"x": 1, "y": 2, "w": 100, "h": 120},
        )
        manager.assign_face_to_person(anna_face_solo, anna_id)

        anna_face_duo = manager.insert_face(
            photo_id=duo_photo_id,
            embedding=[0.02] * EXPECTED_EMBEDDING_DIMENSION,
            bounding_box={"x": 10, "y": 20, "w": 80, "h": 90},
        )
        bartek_face_duo = manager.insert_face(
            photo_id=duo_photo_id,
            embedding=[0.03] * EXPECTED_EMBEDDING_DIMENSION,
            bounding_box={"x": 200, "y": 40, "w": 70, "h": 85},
        )
        updated = manager.assign_faces_to_person([anna_face_duo], anna_id)
        assert updated == 1
        manager.assign_face_to_person(bartek_face_duo, bartek_id)

        manager.mark_photo_as_processed(solo_photo_id)
        assert manager.get_unprocessed_photos() == [
            manager.get_photo_by_id(duo_photo_id)
        ]

        assert manager.count_unassigned_faces() == 0
        assert len(manager.get_unassigned_faces()) == 0

        assert len(manager.get_faces_for_photo(duo_photo_id)) == 2
        assert len(manager.get_faces_for_person(anna_id)) >= 2

        only_anna = manager.get_photos_by_names(["Anna"])
        assert {p.id for p in only_anna} == {solo_photo_id, duo_photo_id}

        anna_and_bartek = manager.get_photos_by_names(["Anna", "Bartek"])
        assert len(anna_and_bartek) == 1 and anna_and_bartek[0].id == duo_photo_id

        assert manager.get_photos_by_names(["Anna", "Ghost"]) == []
        assert manager.get_photos_by_names([]) == []

        by_ids = manager.get_photos_by_person_ids([anna_id, bartek_id])
        assert len(by_ids) == 1 and by_ids[0].id == duo_photo_id

        stats = manager.get_schema_version_info()
        assert stats["photos"] == 2
        assert stats["faces"] == 3
        assert stats["people"] == 2

        manager.unassign_face(anna_face_solo)
        assert manager.get_face_by_id(anna_face_solo).person_id is None
        manager.assign_face_to_person(anna_face_solo, anna_id)

        manager.reset_photo_processed(solo_photo_id)
        assert manager.get_photo_by_id(solo_photo_id).processed is False

        # Validation guards
        try:
            manager.insert_face(
                solo_photo_id,
                embedding=[1.0] * 10,
                bounding_box={"x": 0, "y": 0, "w": 10, "h": 10},
            )
            raise AssertionError("expected ValidationError for short embedding")
        except ValidationError:
            pass

        try:
            manager.insert_face(
                solo_photo_id,
                embedding=[1.0] * EXPECTED_EMBEDDING_DIMENSION,
                bounding_box={"x": 0, "y": 0, "w": 0, "h": 10},
            )
            raise AssertionError("expected ValidationError for zero width")
        except ValidationError:
            pass

        print("database.py smoke test: OK")
    finally:
        Path(test_db_path).unlink(missing_ok=True)
        wal = Path(f"{test_db_path}-wal")
        shm = Path(f"{test_db_path}-shm")
        wal.unlink(missing_ok=True)
        shm.unlink(missing_ok=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _run_smoke_test()
