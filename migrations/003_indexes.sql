-- Migration 003: gallery / cluster query indexes (justified by EXPLAIN QUERY PLAN).

CREATE INDEX IF NOT EXISTS idx_photos_file_path ON photos(file_path);

CREATE INDEX IF NOT EXISTS idx_photos_processed_has_faces ON photos(processed, has_faces);

CREATE INDEX IF NOT EXISTS idx_photos_faceless ON photos(has_faces)
    WHERE processed = 1 AND has_faces = 0;

CREATE INDEX IF NOT EXISTS idx_people_name ON people(name COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces(photo_id);

CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id);

CREATE INDEX IF NOT EXISTS idx_faces_cluster_id ON faces(cluster_id);

CREATE INDEX IF NOT EXISTS idx_faces_unassigned ON faces(photo_id)
    WHERE person_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_faces_unclustered_unassigned ON faces(cluster_id)
    WHERE person_id IS NULL AND cluster_id IS NOT NULL;
