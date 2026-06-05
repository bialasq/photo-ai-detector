-- Migration 001: core tables (pre-has_faces baseline for upgrade path).

CREATE TABLE IF NOT EXISTS photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path   TEXT UNIQUE NOT NULL,
    date_added  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed   INTEGER NOT NULL DEFAULT 0 CHECK (processed IN (0, 1))
);

CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT
);

CREATE TABLE IF NOT EXISTS faces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id      INTEGER NOT NULL,
    embedding     BLOB NOT NULL,
    bounding_box  TEXT NOT NULL,
    FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE
);
