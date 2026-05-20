"""
Local SQLite persistence layer for the offline photo organizer application.

This module is the single source of truth for on-disk metadata. Binary image files
stay on the filesystem; the database stores paths, face embeddings (BLOB), bounding
boxes, DBSCAN cluster identifiers, and person labels.

Data flow (pipeline stages):
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
  │ insert_photo│ ──► │ insert_face  │ ──► │ DBSCAN / manual │ ──► │ search by    │
  │ (ingestion) │     │ (detection)  │     │ cluster_id      │     │ get_photos_* │
  └─────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
        photos              faces              faces.person_id         JOIN people

Table relationships:
  photos (1) ──< (N) faces (N) >── (0..1) people
  Deleting a photo CASCADE-deletes its faces (see faces.photo_id FK).
  faces.cluster_id groups detections before a user assigns a name via people.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Generator, Iterable, Optional, Sequence

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

DEFAULT_DB_PATH: Final[str] = "organizer.db"

# InsightFace / ArcFace standard output size for this project.
EXPECTED_EMBEDDING_DIMENSION: Final[int] = 512

# float32 little-endian packing for faces.embedding BLOB column.
EMBEDDING_BLOB_FORMAT: Final[str] = f"{EXPECTED_EMBEDDING_DIMENSION}f"
EMBEDDING_BLOB_BYTE_SIZE: Final[int] = EXPECTED_EMBEDDING_DIMENSION * 4

BOUNDING_BOX_KEYS: Final[tuple[str, ...]] = ("x", "y", "w", "h")

# DBSCAN noise is persisted as NULL in faces.cluster_id (never a named cluster id).
# Legitimate multi-face groups use cluster_id >= 0.
NAMED_CLUSTER_ID_MIN: Final[int] = 0

PHOTO_COLUMNS: Final[str] = "id, file_path, date_added, processed"
FACE_COLUMNS: Final[str] = (
    "id, photo_id, embedding, bounding_box, cluster_id, person_id"
)
PERSON_COLUMNS: Final[str] = "id, name, created_at"


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
# Serialization helpers — faces.embedding (BLOB) & faces.bounding_box (TEXT)
# ---------------------------------------------------------------------------


def serialize_embedding_blob(embedding: Sequence[float]) -> bytes:
    """
    Serialize a face embedding vector to a float32 little-endian BLOB.

    Args:
        embedding: Sequence of floats (typically length 512).

    Returns:
        Raw bytes stored in `faces.embedding`.

    Raises:
        ValidationError: If embedding is empty, non-numeric, or wrong length.
    """
    if not embedding:
        raise ValidationError("embedding must contain at least one float value")

    try:
        float_values = [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise ValidationError("embedding must contain only numeric values") from exc

    if len(float_values) != EXPECTED_EMBEDDING_DIMENSION:
        raise ValidationError(
            f"embedding must have exactly {EXPECTED_EMBEDDING_DIMENSION} floats, "
            f"got {len(float_values)}"
        )

    return struct.pack(EMBEDDING_BLOB_FORMAT, *float_values)


def deserialize_embedding_blob(data: bytes | memoryview) -> list[float]:
    """
    Deserialize `faces.embedding` BLOB bytes into a Python list of floats.

    Args:
        data: Raw BLOB from SQLite.

    Returns:
        List of floats suitable for numpy / clustering.

    Raises:
        ValueError: If byte length is not a multiple of four or count mismatches.
    """
    raw = bytes(data)
    if len(raw) % 4 != 0:
        raise ValueError("embedding BLOB length must be a multiple of 4 bytes")
    count = len(raw) // 4
    if count == 0:
        raise ValueError("embedding BLOB must not be empty")
    return [float(value) for value in struct.unpack(f"{count}f", raw)]


def serialize_embedding_json(embedding: Sequence[float]) -> str:
    """
    Legacy JSON serializer retained for migration reads of older TEXT columns.

    Args:
        embedding: Sequence of floats.

    Returns:
        JSON array string.
    """
    if not embedding:
        raise ValidationError("embedding must contain at least one float value")

    try:
        float_values = [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise ValidationError("embedding must contain only numeric values") from exc

    return json.dumps(float_values, separators=(",", ":"))


def deserialize_embedding_json(text: str) -> list[float]:
    """
    Deserialize legacy JSON TEXT embeddings.

    Args:
        text: Raw TEXT column value from SQLite.

    Returns:
        List of floats.

    Raises:
        ValueError: If JSON structure is invalid.
    """
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError("embedding JSON must decode to a JSON array (list)")
    if not payload:
        raise ValueError("embedding JSON array must not be empty")
    return [float(value) for value in payload]


def deserialize_embedding_value(raw: object) -> list[float]:
    """
    Deserialize `faces.embedding` regardless of on-disk representation (BLOB or legacy TEXT).

    Args:
        raw: Value returned by sqlite3 for the embedding column.

    Returns:
        List of floats.

    Raises:
        ValueError: If the value cannot be interpreted.
    """
    if raw is None:
        raise ValueError("embedding column must not be NULL")

    if isinstance(raw, (bytes, memoryview, bytearray)):
        return deserialize_embedding_blob(raw)

    text = str(raw).strip()
    if not text:
        raise ValueError("embedding column must not be empty")

    if text.startswith("[") or text.startswith("{"):
        return deserialize_embedding_json(text)

    try:
        return deserialize_embedding_blob(text.encode("latin-1"))
    except (ValueError, struct.error):
        return deserialize_embedding_json(text)


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
    One row from the `faces` table with deserialized columns.

    Attributes:
        id: Primary key of the face detection record.
        photo_id: Foreign key to `photos.id`.
        embedding: Deserialized float vector (e.g. 512 dimensions).
        bounding_box: Deserialized dict {x, y, w, h}.
        cluster_id: DBSCAN cluster label persisted after clustering, or None.
        person_id: Foreign key to `people.id`, or None before labeling.
    """

    id: int
    photo_id: int
    embedding: list[float]
    bounding_box: dict[str, int]
    cluster_id: Optional[int]
    person_id: Optional[int]

    @classmethod
    def from_sqlite_row(cls, row: sqlite3.Row) -> FaceRow:
        """Build FaceRow from sqlite3.Row, parsing BLOB / JSON columns."""
        raw_cluster_id = row["cluster_id"]
        raw_person_id = row["person_id"]
        return cls(
            id=int(row["id"]),
            photo_id=int(row["photo_id"]),
            embedding=deserialize_embedding_value(row["embedding"]),
            bounding_box=deserialize_bounding_box(str(row["bounding_box"])),
            cluster_id=int(raw_cluster_id) if raw_cluster_id is not None else None,
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
        name: Display name; None until the user assigns a label.
        created_at: ISO-like timestamp string assigned by SQLite DEFAULT.
    """

    id: int
    name: Optional[str]
    created_at: str

    @classmethod
    def from_sqlite_row(cls, row: sqlite3.Row) -> PersonRow:
        """Build PersonRow from sqlite3.Row."""
        raw_name = row["name"]
        return cls(
            id=int(row["id"]),
            name=str(raw_name) if raw_name is not None else None,
            created_at=str(row["created_at"]),
        )


@dataclass(frozen=True, slots=True)
class PersonWithFaceCount:
    """
    Aggregated person record for UI galleries.

    Attributes:
        id: `people.id`.
        name: Assigned display name, or None.
        face_count: Number of faces linked via `faces.person_id`.
        exemplar_photo_path: File path of one representative photo, or None.
    """

    id: int
    name: Optional[str]
    face_count: int
    exemplar_photo_path: Optional[str]

    @classmethod
    def from_sqlite_row(cls, row: sqlite3.Row) -> PersonWithFaceCount:
        """Build PersonWithFaceCount from a joined aggregate query row."""
        raw_name = row["name"]
        raw_path = row["exemplar_photo_path"]
        return cls(
            id=int(row["id"]),
            name=str(raw_name) if raw_name is not None else None,
            face_count=int(row["face_count"]),
            exemplar_photo_path=str(raw_path) if raw_path is not None else None,
        )


# ---------------------------------------------------------------------------
# Database manager
# ---------------------------------------------------------------------------


class DatabaseManager:
    """
    Production SQLite access layer for the offline photo organizer.

    Responsibilities:
      - Schema creation and lightweight migrations (`create_tables`)
      - Parameterized CRUD for photos, faces, and people
      - Cluster → person assignment (`assign_name_to_cluster`, `merge_person_clusters`)
      - Intersection search across labeled faces
      - Enforcing referential integrity via PRAGMA foreign_keys

    Connection policy:
      - `_get_connection()` opens a raw connection (caller must close).
      - `_managed_connection()` is the preferred internal wrapper (commit/rollback/close).
      - Every connection executes `PRAGMA foreign_keys = ON` immediately after connect.
      - Every SQL statement is logged at DEBUG via `_execute` / `_executemany` / `_executescript`.
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
        connection.execute("PRAGMA foreign_keys = ON;")
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

    @staticmethod
    def _execute(
        connection: sqlite3.Connection,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> sqlite3.Cursor:
        """
        Execute a parameterized statement and log the query.

        Args:
            connection: Active SQLite connection.
            sql: SQL statement with `?` placeholders.
            parameters: Bound parameter sequence.

        Returns:
            sqlite3.Cursor after execution.
        """
        LOGGER.debug("SQL execute: %s | params=%s", sql.strip(), tuple(parameters))
        return connection.execute(sql, tuple(parameters))

    @staticmethod
    def _executemany(
        connection: sqlite3.Connection,
        sql: str,
        parameters_seq: Iterable[Sequence[Any]],
    ) -> sqlite3.Cursor:
        """Execute a statement for many parameter sets with logging."""
        param_list = [tuple(params) for params in parameters_seq]
        LOGGER.debug(
            "SQL executemany: %s | batch_size=%s",
            sql.strip(),
            len(param_list),
        )
        return connection.executemany(sql, param_list)

    @staticmethod
    def _executescript(connection: sqlite3.Connection, script: str) -> sqlite3.Cursor:
        """Execute a multi-statement DDL script with logging."""
        LOGGER.debug("SQL executescript: %s", script.strip())
        return connection.executescript(script)

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

    @staticmethod
    def _validate_cluster_id(value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"cluster_id must be an int, got {value!r}")
        return value

    @classmethod
    def _validate_named_cluster_id(cls, value: int) -> int:
        """Validate a DBSCAN cluster label eligible for naming (0, 1, 2, …)."""
        cluster_id = cls._validate_cluster_id(value)
        if cluster_id < NAMED_CLUSTER_ID_MIN:
            raise ValidationError(
                f"cluster_id must be >= {NAMED_CLUSTER_ID_MIN} for named clusters, "
                f"got {cluster_id}"
            )
        return cluster_id

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

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = DatabaseManager._execute(
            connection,
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
        rows = DatabaseManager._execute(
            connection,
            f"PRAGMA table_info({table_name})",
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        """
        Bring older on-disk schemas forward without destructive rebuilds.

        Adds missing columns introduced after the initial release:
          - faces.cluster_id
          - faces.person_id (legacy databases created before people FK)
          - people.created_at (legacy people table without timestamp)
        """
        if self._table_exists(connection, "people"):
            people_columns = self._table_columns(connection, "people")
            if "created_at" not in people_columns:
                self._execute(
                    connection,
                    """
                    ALTER TABLE people
                    ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    """,
                )

        if self._table_exists(connection, "faces"):
            face_columns = self._table_columns(connection, "faces")
            if "person_id" not in face_columns:
                self._execute(
                    connection,
                    "ALTER TABLE faces ADD COLUMN person_id INTEGER",
                )
            if "cluster_id" not in face_columns:
                self._execute(
                    connection,
                    "ALTER TABLE faces ADD COLUMN cluster_id INTEGER",
                )

    def _ensure_schema_indexes(self, connection: sqlite3.Connection) -> None:
        """
        Create performance indexes after tables and migrations are complete.

        Must run only when referenced columns exist (legacy DB files upgraded via ALTER).
        """
        index_statements: list[str] = [
            "CREATE INDEX IF NOT EXISTS idx_photos_file_path ON photos(file_path)",
            "CREATE INDEX IF NOT EXISTS idx_photos_processed ON photos(processed)",
            "CREATE INDEX IF NOT EXISTS idx_people_name ON people(name COLLATE NOCASE)",
            "CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces(photo_id)",
            "CREATE INDEX IF NOT EXISTS idx_faces_unassigned ON faces(photo_id) WHERE person_id IS NULL",
        ]

        if self._table_exists(connection, "faces"):
            face_columns = self._table_columns(connection, "faces")
            if "person_id" in face_columns:
                index_statements.append(
                    "CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id)"
                )
            if "cluster_id" in face_columns:
                index_statements.extend(
                    [
                        "CREATE INDEX IF NOT EXISTS idx_faces_cluster_id ON faces(cluster_id)",
                        (
                            "CREATE INDEX IF NOT EXISTS idx_faces_unclustered_unassigned "
                            "ON faces(cluster_id) "
                            "WHERE person_id IS NULL AND cluster_id IS NOT NULL"
                        ),
                    ]
                )

        for statement in index_statements:
            self._execute(connection, statement)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def create_tables(self) -> None:
        """
        Create all tables and performance indexes if they do not already exist.

        Execution order (critical for legacy organizer.db files):
          1. CREATE TABLE IF NOT EXISTS — never fails on old files missing new columns
          2. _apply_migrations — ALTER TABLE adds cluster_id / created_at / person_id
          3. _ensure_schema_indexes — indexes only after columns exist
        """
        db_file = Path(self.db_path)
        parent = db_file.parent
        if parent != Path(".") and str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)

        tables_script = """
        CREATE TABLE IF NOT EXISTS photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path   TEXT UNIQUE NOT NULL,
            date_added  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed   INTEGER NOT NULL DEFAULT 0 CHECK (processed IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS people (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS faces (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id      INTEGER NOT NULL,
            embedding     BLOB NOT NULL,
            bounding_box  TEXT NOT NULL,
            cluster_id    INTEGER,
            person_id     INTEGER,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE SET NULL
        );
        """

        with self._managed_connection() as connection:
            self._executescript(connection, tables_script)
            self._apply_migrations(connection)
            self._ensure_schema_indexes(connection)

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
            cursor = self._execute(
                connection,
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

            existing = self._execute(
                connection,
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
            result = self._execute(
                connection,
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
            result = self._execute(
                connection,
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
            row = self._execute(
                connection,
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
            row = self._execute(
                connection,
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
            rows = self._execute(connection, query, tuple(params)).fetchall()

        return [PhotoRow.from_sqlite_row(row) for row in rows]

    def get_unprocessed_photos(self) -> list[PhotoRow]:
        """
        Return photos where `processed = 0` (face-detection / indexing queue).

        Ordered oldest-first so the pipeline processes files in stable ingestion order.
        """
        with self._managed_connection() as connection:
            rows = self._execute(
                connection,
                f"""
                SELECT {PHOTO_COLUMNS}
                FROM photos
                WHERE processed = 0
                ORDER BY date_added ASC, id ASC
                """,
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
            row = self._execute(
                connection,
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
            result = self._execute(
                connection,
                "DELETE FROM photos WHERE id = ?",
                (valid_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No photo found with id={valid_id}")

        LOGGER.info("Deleted photo id=%s (faces cascaded)", valid_id)

    # ------------------------------------------------------------------
    # People — create / update / delete / read / cluster assignment
    # ------------------------------------------------------------------

    def insert_person(
        self,
        name: str,
        *,
        relationship: Optional[str] = None,
    ) -> int:
        """
        Insert a person record with an assigned display name.

        Args:
            name: Display name (used by `get_photos_by_names`).
            relationship: Deprecated ignored parameter kept for caller compatibility.

        Returns:
            Generated `people.id`.
        """
        del relationship
        clean_name = self._validate_non_empty_str(name, "name")

        with self._managed_connection() as connection:
            cursor = self._execute(
                connection,
                """
                INSERT INTO people (name)
                VALUES (?)
                """,
                (clean_name,),
            )
            new_id = int(cursor.lastrowid)

        LOGGER.debug("Inserted person id=%s name=%s", new_id, clean_name)
        return new_id

    def update_person(
        self,
        person_id: int,
        *,
        name: Optional[str] = None,
    ) -> None:
        """
        Update a person's display name.

        Args:
            person_id: Target row.
            name: New name (required for any update).

        Raises:
            ValidationError: When name is not provided.
            RecordNotFoundError: When the person row does not exist.
        """
        valid_id = self._validate_positive_int(person_id, "person_id")

        if name is None:
            raise ValidationError("update_person requires name")

        clean_name = self._validate_non_empty_str(name, "name")

        with self._managed_connection() as connection:
            result = self._execute(
                connection,
                """
                UPDATE people
                SET name = ?
                WHERE id = ?
                """,
                (clean_name, valid_id),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No person found with id={valid_id}")

    def get_person_by_id(self, person_id: int) -> Optional[PersonRow]:
        """Fetch one person by id, or None."""
        valid_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            row = self._execute(
                connection,
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

        Only rows with a non-NULL name are considered.
        """
        clean_name = self._validate_non_empty_str(name, "name")

        with self._managed_connection() as connection:
            row = self._execute(
                connection,
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
        """List all people ordered by name (case-insensitive), then id."""
        with self._managed_connection() as connection:
            rows = self._execute(
                connection,
                f"""
                SELECT {PERSON_COLUMNS}
                FROM people
                ORDER BY name COLLATE NOCASE ASC, id ASC
                """,
            ).fetchall()

        return [PersonRow.from_sqlite_row(row) for row in rows]

    def count_people(self) -> int:
        """Return total number of person records."""
        with self._managed_connection() as connection:
            row = self._execute(
                connection,
                "SELECT COUNT(*) AS cnt FROM people",
            ).fetchone()
        return int(row["cnt"]) if row is not None else 0

    def delete_person(self, person_id: int) -> None:
        """
        Delete a person. Linked faces get person_id SET NULL (ON DELETE SET NULL).

        Raises:
            RecordNotFoundError: If person_id does not exist.
        """
        valid_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            result = self._execute(
                connection,
                "DELETE FROM people WHERE id = ?",
                (valid_id,),
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"No person found with id={valid_id}")

        LOGGER.info("Deleted person id=%s (face links nulled)", valid_id)

    def get_unnamed_clusters(self) -> list[int]:
        """
        Return unique DBSCAN `cluster_id` values that are not yet linked to `people`.

        Only legitimate named clusters (``cluster_id >= 0``) are returned. DBSCAN noise
        faces stored with ``cluster_id IS NULL`` are excluded so the UI is not flooded
        with single-face background detections.
        """
        with self._managed_connection() as connection:
            rows = self._execute(
                connection,
                """
                SELECT DISTINCT cluster_id
                FROM faces
                WHERE cluster_id IS NOT NULL
                  AND cluster_id >= ?
                  AND person_id IS NULL
                ORDER BY cluster_id ASC
                """,
                (NAMED_CLUSTER_ID_MIN,),
            ).fetchall()

        cluster_ids = [int(row["cluster_id"]) for row in rows]
        LOGGER.debug("get_unnamed_clusters → %s cluster(s)", len(cluster_ids))
        return cluster_ids

    def get_noise_faces(self) -> list[FaceRow]:
        """
        Return DBSCAN noise faces: unassigned and without a named cluster label.

        Noise is stored as ``cluster_id IS NULL`` (legacy rows may use ``-1``).
        """
        with self._managed_connection() as connection:
            rows = self._execute(
                connection,
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE person_id IS NULL
                  AND (cluster_id IS NULL OR cluster_id < ?)
                ORDER BY id ASC
                """,
                (NAMED_CLUSTER_ID_MIN,),
            ).fetchall()

        faces = [FaceRow.from_sqlite_row(row) for row in rows]
        LOGGER.debug("get_noise_faces → %s face(s)", len(faces))
        return faces

    def _get_noise_face_or_raise(self, face_id: int) -> FaceRow:
        valid_face_id = self._validate_positive_int(face_id, "face_id")
        face = self.get_face_by_id(valid_face_id)
        if face is None:
            raise RecordNotFoundError(f"No face found with id={valid_face_id}")
        if face.person_id is not None:
            raise ValidationError(f"face_id={valid_face_id} is already assigned to a person")
        if face.cluster_id is not None and face.cluster_id >= NAMED_CLUSTER_ID_MIN:
            raise ValidationError(
                f"face_id={valid_face_id} belongs to cluster_id={face.cluster_id}, "
                "not noise"
            )
        return face

    def assign_name_to_noise_face(self, face_id: int, name: str) -> int:
        """
        Create a ``people`` row and link a single DBSCAN noise face to it.

        Returns:
            Newly created ``people.id``.
        """
        self._get_noise_face_or_raise(face_id)
        clean_name = self._validate_non_empty_str(name, "name")
        valid_face_id = self._validate_positive_int(face_id, "face_id")

        with self._managed_connection() as connection:
            cursor = self._execute(
                connection,
                """
                INSERT INTO people (name)
                VALUES (?)
                """,
                (clean_name,),
            )
            person_id = int(cursor.lastrowid)

            result = self._execute(
                connection,
                """
                UPDATE faces
                SET person_id = ?
                WHERE id = ?
                  AND person_id IS NULL
                  AND (cluster_id IS NULL OR cluster_id < ?)
                """,
                (person_id, valid_face_id, NAMED_CLUSTER_ID_MIN),
            )
            if int(result.rowcount) == 0:
                raise RecordNotFoundError(
                    f"Noise face id={valid_face_id} could not be assigned"
                )

        LOGGER.info(
            "Assigned noise face_id=%s to new person_id=%s name=%r",
            valid_face_id,
            person_id,
            clean_name,
        )
        return person_id

    def assign_noise_face_to_person(self, face_id: int, person_id: int) -> str:
        """
        Link a DBSCAN noise face to an existing named person.

        Returns:
            The person's display name.
        """
        self._get_noise_face_or_raise(face_id)
        valid_face_id = self._validate_positive_int(face_id, "face_id")
        valid_person_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            person = self._execute(
                connection,
                "SELECT id, name FROM people WHERE id = ?",
                (valid_person_id,),
            ).fetchone()
            if person is None:
                raise RecordNotFoundError(f"No person found with id={valid_person_id}")

            person_name = str(person["name"])
            result = self._execute(
                connection,
                """
                UPDATE faces
                SET person_id = ?
                WHERE id = ?
                  AND person_id IS NULL
                  AND (cluster_id IS NULL OR cluster_id < ?)
                """,
                (valid_person_id, valid_face_id, NAMED_CLUSTER_ID_MIN),
            )
            if int(result.rowcount) == 0:
                raise RecordNotFoundError(
                    f"Noise face id={valid_face_id} could not be assigned"
                )

        LOGGER.info(
            "Assigned noise face_id=%s to existing person_id=%s name=%r",
            valid_face_id,
            valid_person_id,
            person_name,
        )
        return person_name

    def get_exemplar_photo_id_for_cluster(self, cluster_id: int) -> int:
        """
        Return the photo_id of the representative face for an unnamed cluster.

        Uses the lowest ``faces.id`` among unassigned rows in the cluster so inbox
        thumbnails stay stable across requests.
        """
        valid_cluster_id = self._validate_named_cluster_id(cluster_id)

        with self._managed_connection() as connection:
            row = self._execute(
                connection,
                """
                SELECT photo_id
                FROM faces
                WHERE cluster_id = ?
                  AND person_id IS NULL
                ORDER BY id ASC
                LIMIT 1
                """,
                (valid_cluster_id,),
            ).fetchone()

        if row is None:
            raise RecordNotFoundError(
                f"No unassigned faces found for cluster_id={valid_cluster_id}"
            )

        photo_id = int(row["photo_id"])
        LOGGER.debug(
            "get_exemplar_photo_id_for_cluster cluster_id=%s → photo_id=%s",
            valid_cluster_id,
            photo_id,
        )
        return photo_id

    def assign_name_to_cluster(self, cluster_id: int, name: str) -> int:
        """
        Name a DBSCAN cluster by creating a `people` row and linking all member faces.

        Args:
            cluster_id: DBSCAN label stored on face rows.
            name: User-provided display name.

        Returns:
            Newly created `people.id`.

        Raises:
            ValidationError: Invalid arguments.
            RecordNotFoundError: No unassigned faces exist for the cluster_id.
        """
        valid_cluster_id = self._validate_named_cluster_id(cluster_id)
        clean_name = self._validate_non_empty_str(name, "name")

        with self._managed_connection() as connection:
            cluster_faces = self._execute(
                connection,
                """
                SELECT COUNT(*) AS cnt
                FROM faces
                WHERE cluster_id = ?
                  AND cluster_id >= ?
                  AND person_id IS NULL
                """,
                (valid_cluster_id, NAMED_CLUSTER_ID_MIN),
            ).fetchone()

            face_count = int(cluster_faces["cnt"]) if cluster_faces is not None else 0
            if face_count == 0:
                raise RecordNotFoundError(
                    f"No unassigned faces found for cluster_id={valid_cluster_id}"
                )

            cursor = self._execute(
                connection,
                """
                INSERT INTO people (name)
                VALUES (?)
                """,
                (clean_name,),
            )
            person_id = int(cursor.lastrowid)

            self._execute(
                connection,
                """
                UPDATE faces
                SET person_id = ?
                WHERE cluster_id = ?
                  AND person_id IS NULL
                """,
                (person_id, valid_cluster_id),
            )

        LOGGER.info(
            "Assigned cluster_id=%s to person_id=%s name=%r (%s face(s))",
            valid_cluster_id,
            person_id,
            clean_name,
            face_count,
        )
        return person_id

    def merge_person_clusters(self, target_person_id: int, source_person_id: int) -> int:
        """
        Merge two labeled persons by moving all faces to the target and deleting the source.

        Args:
            target_person_id: Person row that survives the merge.
            source_person_id: Person row removed after reassignment.

        Returns:
            Number of face rows reassigned from source to target.

        Raises:
            ValidationError: When ids are invalid or identical.
            RecordNotFoundError: When either person row does not exist.
        """
        valid_target_id = self._validate_positive_int(target_person_id, "target_person_id")
        valid_source_id = self._validate_positive_int(source_person_id, "source_person_id")

        if valid_target_id == valid_source_id:
            raise ValidationError("target_person_id and source_person_id must differ")

        with self._managed_connection() as connection:
            target_row = self._execute(
                connection,
                "SELECT id FROM people WHERE id = ?",
                (valid_target_id,),
            ).fetchone()
            if target_row is None:
                raise RecordNotFoundError(f"No person found with id={valid_target_id}")

            source_row = self._execute(
                connection,
                "SELECT id FROM people WHERE id = ?",
                (valid_source_id,),
            ).fetchone()
            if source_row is None:
                raise RecordNotFoundError(f"No person found with id={valid_source_id}")

            reassigned = self._execute(
                connection,
                """
                UPDATE faces
                SET person_id = ?
                WHERE person_id = ?
                """,
                (valid_target_id, valid_source_id),
            )
            moved_faces = int(reassigned.rowcount)

            deleted = self._execute(
                connection,
                "DELETE FROM people WHERE id = ?",
                (valid_source_id,),
            )
            if deleted.rowcount == 0:
                raise RecordNotFoundError(f"No person found with id={valid_source_id}")

        LOGGER.info(
            "Merged person_id=%s into person_id=%s (%s face(s) moved)",
            valid_source_id,
            valid_target_id,
            moved_faces,
        )
        return moved_faces

    def get_all_people_with_face_counts(self) -> list[PersonWithFaceCount]:
        """
        Return all people with aggregate face counts and one exemplar photo path.

        The exemplar path is taken from the lowest face id linked to each person,
        providing a stable thumbnail for UI cluster galleries.
        """
        with self._managed_connection() as connection:
            rows = self._execute(
                connection,
                """
                SELECT
                    pe.id AS id,
                    pe.name AS name,
                    COUNT(f.id) AS face_count,
                    (
                        SELECT p.file_path
                        FROM faces AS fx
                        INNER JOIN photos AS p ON p.id = fx.photo_id
                        WHERE fx.person_id = pe.id
                        ORDER BY fx.id ASC
                        LIMIT 1
                    ) AS exemplar_photo_path
                FROM people AS pe
                LEFT JOIN faces AS f ON f.person_id = pe.id
                GROUP BY pe.id, pe.name, pe.created_at
                ORDER BY pe.name COLLATE NOCASE ASC, pe.id ASC
                """,
            ).fetchall()

        results = [PersonWithFaceCount.from_sqlite_row(row) for row in rows]
        LOGGER.debug("get_all_people_with_face_counts → %s person(s)", len(results))
        return results

    def get_exemplar_photo_id_for_person(self, person_id: int) -> int:
        """
        Return the photo_id of the representative face for a person record.

        Uses the lowest ``faces.id`` linked to the person for stable thumbnails.
        """
        valid_person_id = self._validate_positive_int(person_id, "person_id")

        with self._managed_connection() as connection:
            row = self._execute(
                connection,
                """
                SELECT photo_id
                FROM faces
                WHERE person_id = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (valid_person_id,),
            ).fetchone()

        if row is None:
            raise RecordNotFoundError(
                f"No faces found for person_id={valid_person_id}"
            )

        photo_id = int(row["photo_id"])
        LOGGER.debug(
            "get_exemplar_photo_id_for_person person_id=%s → photo_id=%s",
            valid_person_id,
            photo_id,
        )
        return photo_id

    # ------------------------------------------------------------------
    # Faces — create / update / delete / read
    # ------------------------------------------------------------------

    def insert_face(
        self,
        photo_id: int,
        embedding: list[float],
        bounding_box: dict[str, int],
        *,
        cluster_id: Optional[int] = None,
        enforce_embedding_dimension: bool = True,
    ) -> int:
        """
        Persist one detected face for a photo.

        Args:
            photo_id: Parent photo primary key.
            embedding: Face vector (default: must be exactly 512 floats).
            bounding_box: Dict with x, y, w, h pixel coordinates.
            cluster_id: Optional DBSCAN cluster label assigned after clustering.
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
        valid_cluster_id: Optional[int] = None
        if cluster_id is not None:
            valid_cluster_id = self._validate_cluster_id(cluster_id)

        embedding_blob = serialize_embedding_blob(vector)
        bounding_box_json = serialize_bounding_box(valid_box)

        with self._managed_connection() as connection:
            parent = self._execute(
                connection,
                "SELECT id FROM photos WHERE id = ?",
                (valid_photo_id,),
            ).fetchone()
            if parent is None:
                raise RecordNotFoundError(f"No photo found with id={valid_photo_id}")

            cursor = self._execute(
                connection,
                """
                INSERT INTO faces (photo_id, embedding, bounding_box, cluster_id, person_id)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (valid_photo_id, embedding_blob, bounding_box_json, valid_cluster_id),
            )
            face_id = int(cursor.lastrowid)

        LOGGER.debug(
            "Inserted face id=%s for photo_id=%s cluster_id=%s",
            face_id,
            valid_photo_id,
            valid_cluster_id,
        )
        return face_id

    def update_faces_cluster_id(
        self,
        face_ids: Sequence[int],
        cluster_id: int,
    ) -> int:
        """
        Persist DBSCAN cluster labels on face rows after clustering.

        Args:
            face_ids: Face primary keys to update.
            cluster_id: DBSCAN label (including synthetic singleton ids).

        Returns:
            Number of face rows updated.
        """
        valid_cluster_id = self._validate_named_cluster_id(cluster_id)
        id_list = [
            self._validate_positive_int(face_id, "face_id") for face_id in face_ids
        ]
        if not id_list:
            return 0

        placeholders = ", ".join("?" for _ in id_list)

        with self._managed_connection() as connection:
            result = self._execute(
                connection,
                f"""
                UPDATE faces
                SET cluster_id = ?
                WHERE id IN ({placeholders})
                """,
                (valid_cluster_id, *id_list),
            )
            updated = int(result.rowcount)

        LOGGER.debug(
            "Updated cluster_id=%s on %s face(s)", valid_cluster_id, updated
        )
        return updated

    def clear_faces_cluster_id(self, face_ids: Sequence[int]) -> int:
        """
        Clear ``faces.cluster_id`` (set to NULL) for DBSCAN noise / discarded detections.

        Args:
            face_ids: Face primary keys to update.

        Returns:
            Number of face rows updated.
        """
        id_list = [
            self._validate_positive_int(face_id, "face_id") for face_id in face_ids
        ]
        if not id_list:
            return 0

        placeholders = ", ".join("?" for _ in id_list)

        with self._managed_connection() as connection:
            result = self._execute(
                connection,
                f"""
                UPDATE faces
                SET cluster_id = NULL
                WHERE id IN ({placeholders})
                """,
                tuple(id_list),
            )
            updated = int(result.rowcount)

        LOGGER.debug("Cleared cluster_id on %s face(s) (DBSCAN noise)", updated)
        return updated

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
            person = self._execute(
                connection,
                "SELECT id FROM people WHERE id = ?",
                (valid_person_id,),
            ).fetchone()
            if person is None:
                raise RecordNotFoundError(f"No person found with id={valid_person_id}")

            result = self._execute(
                connection,
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
            result = self._execute(
                connection,
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
            person = self._execute(
                connection,
                "SELECT id FROM people WHERE id = ?",
                (valid_person_id,),
            ).fetchone()
            if person is None:
                raise RecordNotFoundError(f"No person found with id={valid_person_id}")

            result = self._execute(
                connection,
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
            row = self._execute(
                connection,
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
            rows = self._execute(
                connection,
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
            rows = self._execute(
                connection,
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE person_id = ?
                ORDER BY photo_id ASC, id ASC
                """,
                (valid_person_id,),
            ).fetchall()

        return [FaceRow.from_sqlite_row(row) for row in rows]

    def get_faces_for_cluster(self, cluster_id: int) -> list[FaceRow]:
        """Return all faces sharing a DBSCAN cluster_id."""
        valid_cluster_id = self._validate_named_cluster_id(cluster_id)

        with self._managed_connection() as connection:
            rows = self._execute(
                connection,
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE cluster_id = ?
                ORDER BY photo_id ASC, id ASC
                """,
                (valid_cluster_id,),
            ).fetchall()

        return [FaceRow.from_sqlite_row(row) for row in rows]

    def get_unassigned_faces(self) -> list[FaceRow]:
        """
        Return faces with `person_id IS NULL`.

        Primary input queue for DBSCAN clustering: embeddings exist, labels do not.
        Uses partial index idx_faces_unassigned when present.
        """
        with self._managed_connection() as connection:
            rows = self._execute(
                connection,
                f"""
                SELECT {FACE_COLUMNS}
                FROM faces
                WHERE person_id IS NULL
                ORDER BY photo_id ASC, id ASC
                """,
            ).fetchall()

        return [FaceRow.from_sqlite_row(row) for row in rows]

    def count_unassigned_faces(self) -> int:
        """Return number of faces awaiting clustering / manual assignment."""
        with self._managed_connection() as connection:
            row = self._execute(
                connection,
                """
                SELECT COUNT(*) AS cnt
                FROM faces
                WHERE person_id IS NULL
                """,
            ).fetchone()
        return int(row["cnt"]) if row is not None else 0

    def delete_face(self, face_id: int) -> None:
        """Delete a single face row."""
        valid_id = self._validate_positive_int(face_id, "face_id")

        with self._managed_connection() as connection:
            result = self._execute(
                connection,
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
            result = self._execute(
                connection,
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
          - Only people with non-NULL names participate.
          - A photo matches iff COUNT(DISTINCT pe.name) for assigned faces on that photo
            equals the number of required distinct names, and each name is present.

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

        query = f"""
            SELECT {PHOTO_COLUMNS}
            FROM photos AS p
            WHERE (
                SELECT COUNT(DISTINCT pe.name)
                FROM faces AS f
                INNER JOIN people AS pe ON pe.id = f.person_id
                WHERE f.photo_id = p.id
                  AND pe.name IS NOT NULL
                  AND pe.name IN ({name_placeholders})
            ) = ?
            ORDER BY p.date_added DESC, p.id DESC
        """

        parameters: tuple[object, ...] = tuple(required_names) + (required_count,)

        with self._managed_connection() as connection:
            rows = self._execute(connection, query, parameters).fetchall()

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
            rows = self._execute(connection, query, parameters).fetchall()

        return [PhotoRow.from_sqlite_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Maintenance / diagnostics
    # ------------------------------------------------------------------

    def clear_all_ingestion_data(self) -> dict[str, int]:
        """
        Delete every row from ``faces``, ``people``, and ``photos``.

        DBSCAN cluster labels live on ``faces.cluster_id``; there is no separate
        ``clusters`` table. Schema, indexes, and migrations are preserved.

        Returns:
            Mapping of table name → rows removed.
        """
        with self._managed_connection() as connection:
            faces_before = int(
                self._execute(connection, "SELECT COUNT(*) FROM faces").fetchone()[0]
            )
            people_before = int(
                self._execute(connection, "SELECT COUNT(*) FROM people").fetchone()[0]
            )
            photos_before = int(
                self._execute(connection, "SELECT COUNT(*) FROM photos").fetchone()[0]
            )

            self._execute(connection, "DELETE FROM faces")
            self._execute(connection, "DELETE FROM people")
            self._execute(connection, "DELETE FROM photos")

        removed = {
            "faces": faces_before,
            "people": people_before,
            "photos": photos_before,
        }
        LOGGER.warning(
            "Cleared all ingestion data from %s: faces=%s people=%s photos=%s",
            self.db_path,
            removed["faces"],
            removed["people"],
            removed["photos"],
        )
        return removed

    def vacuum(self) -> None:
        """Rebuild database file and reclaim unused space (maintenance operation)."""
        with self._managed_connection() as connection:
            self._execute(connection, "VACUUM")

    def integrity_check(self) -> str:
        """
        Run SQLite PRAGMA integrity_check.

        Returns:
            'ok' when database is healthy, otherwise an error description string.
        """
        with self._managed_connection() as connection:
            row = self._execute(connection, "PRAGMA integrity_check").fetchone()
        return str(row[0]) if row is not None else "unknown"

    def get_schema_version_info(self) -> dict[str, int]:
        """
        Return row counts per table (quick health snapshot for logging / UI).
        """
        with self._managed_connection() as connection:
            photos = self._execute(connection, "SELECT COUNT(*) FROM photos").fetchone()
            people = self._execute(connection, "SELECT COUNT(*) FROM people").fetchone()
            faces = self._execute(connection, "SELECT COUNT(*) FROM faces").fetchone()
            unassigned = self._execute(
                connection,
                "SELECT COUNT(*) FROM faces WHERE person_id IS NULL",
            ).fetchone()
            unnamed_clusters = self._execute(
                connection,
                """
                SELECT COUNT(DISTINCT cluster_id)
                FROM faces
                WHERE cluster_id IS NOT NULL
                  AND cluster_id >= ?
                  AND person_id IS NULL
                """,
                (NAMED_CLUSTER_ID_MIN,),
            ).fetchone()

        return {
            "photos": int(photos[0]),
            "people": int(people[0]),
            "faces": int(faces[0]),
            "faces_unassigned": int(unassigned[0]),
            "unnamed_clusters": int(unnamed_clusters[0]),
        }


# ---------------------------------------------------------------------------
# Smoke test (run: python database.py)
# ---------------------------------------------------------------------------


def _run_legacy_schema_migration_test() -> None:
    """
    Regression: existing organizer.db files created before cluster_id must upgrade cleanly.

    Simulates the pre-migration faces table, then runs create_tables() which previously
    crashed with sqlite3.OperationalError: no such column: cluster_id.
    """
    legacy_db_path = "_legacy_schema_migration_test.db"
    legacy_path = Path(legacy_db_path)

    try:
        connection = sqlite3.connect(legacy_db_path)
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
                name TEXT NOT NULL,
                relationship TEXT
            );
            CREATE TABLE faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_id INTEGER NOT NULL,
                embedding TEXT NOT NULL,
                bounding_box TEXT NOT NULL,
                person_id INTEGER
            );
            """
        )
        connection.commit()
        connection.close()

        legacy_manager = DatabaseManager(db_path=legacy_db_path)
        legacy_manager.create_tables()

        with legacy_manager._managed_connection() as upgraded:
            face_columns = legacy_manager._table_columns(upgraded, "faces")
            assert "cluster_id" in face_columns

        legacy_manager.update_faces_cluster_id([1], 0)
        print("legacy schema migration test: OK")
    finally:
        legacy_path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(f"{legacy_db_path}{suffix}").unlink(missing_ok=True)


def _run_smoke_test() -> None:
    """
    Exhaustive in-memory integration test for schema, CRUD, clustering, and intersection logic.

    Uses a temporary file in the working directory; removed in finally block.
    """
    _run_legacy_schema_migration_test()

    test_db_path = "_organizer_smoke_test.db"
    manager = DatabaseManager(db_path=test_db_path)

    try:
        assert not manager.database_exists()
        manager.create_tables()
        assert manager.database_exists()
        assert manager.integrity_check() == "ok"

        solo_path = r"C:\Photos\event\solo_001.jpg"
        duo_path = r"C:\Photos\event\duo_002.jpg"
        group_path = r"C:\Photos\event\group_003.jpg"

        solo_photo_id = manager.insert_photo(solo_path)
        duo_photo_id = manager.insert_photo(duo_path)
        group_photo_id = manager.insert_photo(group_path)
        assert manager.insert_photo(duo_path) == duo_photo_id

        assert manager.get_photo_by_path(solo_path) is not None
        assert manager.get_photo_by_id(solo_photo_id) is not None

        anna_id = manager.insert_person("Anna")
        bartek_id = manager.insert_person("Bartek")

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

        cluster_a_faces = [
            manager.insert_face(
                photo_id=group_photo_id,
                embedding=[0.11] * EXPECTED_EMBEDDING_DIMENSION,
                bounding_box={"x": 5, "y": 5, "w": 50, "h": 50},
            ),
            manager.insert_face(
                photo_id=group_photo_id,
                embedding=[0.12] * EXPECTED_EMBEDDING_DIMENSION,
                bounding_box={"x": 60, "y": 5, "w": 50, "h": 50},
            ),
        ]
        cluster_b_face = manager.insert_face(
            photo_id=group_photo_id,
            embedding=[0.21] * EXPECTED_EMBEDDING_DIMENSION,
            bounding_box={"x": 120, "y": 5, "w": 50, "h": 50},
        )

        cluster_a_id = 7
        cluster_b_id = 8
        assert manager.update_faces_cluster_id(cluster_a_faces, cluster_a_id) == 2
        assert manager.update_faces_cluster_id([cluster_b_face], cluster_b_id) == 1

        unnamed = manager.get_unnamed_clusters()
        assert set(unnamed) == {cluster_a_id, cluster_b_id}

        magda_id = manager.assign_name_to_cluster(cluster_a_id, "Magda")
        assert magda_id > 0
        assert cluster_a_id not in manager.get_unnamed_clusters()
        assert manager.get_unnamed_clusters() == [cluster_b_id]

        zosia_id = manager.assign_name_to_cluster(cluster_b_id, "Zosia")
        merged_faces = manager.merge_person_clusters(magda_id, zosia_id)
        assert merged_faces == 1
        assert manager.get_person_by_id(zosia_id) is None
        assert len(manager.get_faces_for_person(magda_id)) == 3

        people_summary = manager.get_all_people_with_face_counts()
        summary_by_name = {entry.name: entry for entry in people_summary if entry.name}
        assert summary_by_name["Anna"].face_count == 2
        assert summary_by_name["Anna"].exemplar_photo_path == solo_path
        assert summary_by_name["Magda"].face_count == 3
        assert summary_by_name["Magda"].exemplar_photo_path == group_path

        manager.mark_photo_as_processed(solo_photo_id)
        assert manager.get_unprocessed_photos() == [
            manager.get_photo_by_id(duo_photo_id),
            manager.get_photo_by_id(group_photo_id),
        ]

        assert manager.count_unassigned_faces() == 0
        assert len(manager.get_unassigned_faces()) == 0

        assert len(manager.get_faces_for_photo(duo_photo_id)) == 2
        assert len(manager.get_faces_for_person(anna_id)) >= 2
        assert len(manager.get_faces_for_cluster(cluster_a_id)) == 2

        only_anna = manager.get_photos_by_names(["Anna"])
        assert {photo.id for photo in only_anna} == {solo_photo_id, duo_photo_id}

        anna_and_bartek = manager.get_photos_by_names(["Anna", "Bartek"])
        assert len(anna_and_bartek) == 1 and anna_and_bartek[0].id == duo_photo_id

        assert manager.get_photos_by_names(["Anna", "Ghost"]) == []
        assert manager.get_photos_by_names([]) == []

        by_ids = manager.get_photos_by_person_ids([anna_id, bartek_id])
        assert len(by_ids) == 1 and by_ids[0].id == duo_photo_id

        stats = manager.get_schema_version_info()
        assert stats["photos"] == 3
        assert stats["faces"] == 6
        assert stats["people"] == 3
        assert stats["unnamed_clusters"] == 0

        manager.unassign_face(anna_face_solo)
        assert manager.get_face_by_id(anna_face_solo).person_id is None
        manager.assign_face_to_person(anna_face_solo, anna_id)

        manager.reset_photo_processed(solo_photo_id)
        assert manager.get_photo_by_id(solo_photo_id).processed is False

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

        try:
            manager.assign_name_to_cluster(9999, "Nobody")
            raise AssertionError("expected RecordNotFoundError for missing cluster")
        except RecordNotFoundError:
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
