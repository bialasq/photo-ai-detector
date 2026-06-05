-- Migration 002: columns introduced after the initial release.

ALTER TABLE photos
ADD COLUMN has_faces INTEGER NOT NULL DEFAULT 1 CHECK (has_faces IN (0, 1));

UPDATE photos
SET has_faces = CASE
    WHEN EXISTS (
        SELECT 1 FROM faces AS f WHERE f.photo_id = photos.id
    ) THEN 1
    ELSE 0
END
WHERE processed = 1;

ALTER TABLE people
ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE faces ADD COLUMN person_id INTEGER;

ALTER TABLE faces ADD COLUMN cluster_id INTEGER;
