# Database indexes (task 1.3.1)

Indexes are applied by migration `migrations/003_indexes.sql` after schema
migrations `001` and `002`. Each index below was chosen from `EXPLAIN QUERY PLAN`
on gallery and cluster-identification queries — no index was added without a
measured full-table scan on a seeded database (~2k photos / faces).

## Query plan summary

| Query | Before (no indexes) | After |
| --- | --- | --- |
| Gallery faceless (`processed=1 AND has_faces=0`) | `SCAN TABLE photos` | `SEARCH photos USING INDEX idx_photos_faceless` |
| Gallery processed only | `SCAN TABLE photos` | `SEARCH photos USING INDEX idx_photos_processed_has_faces` |
| Faces for person | `SCAN TABLE faces` | `SEARCH faces USING INDEX idx_faces_person_id` |
| Photos by cluster (join) | `SCAN TABLE faces` | `SEARCH faces USING INDEX idx_faces_cluster_id` |
| Unnamed cluster summaries (CTE) | `SCAN TABLE faces` | `SEARCH faces USING INDEX idx_faces_unclustered_unassigned` |

Automated checks: `tests/perf/test_query_plans.py` (asserts no `SCAN TABLE` on
`photos` / `faces` for the five gallery queries above).

## Indexes

| Index | Table | Columns | Rationale |
| --- | --- | --- | --- |
| `idx_photos_file_path` | `photos` | `file_path` | Ingestion dedup / lookup by path (`get_photo_by_path`, `INSERT OR IGNORE` recovery) |
| `idx_photos_processed_has_faces` | `photos` | `(processed, has_faces)` | Gallery filters on processed state and faceless flag |
| `idx_photos_faceless` | `photos` | `has_faces` WHERE `processed=1 AND has_faces=0` | Partial index for faceless-only gallery (smaller, faster) |
| `idx_people_name` | `people` | `name COLLATE NOCASE` | Name search / people list ordering |
| `idx_faces_photo_id` | `faces` | `photo_id` | Join photos↔faces, cascade lookups |
| `idx_faces_person_id` | `faces` | `person_id` | Person gallery, search-by-name joins |
| `idx_faces_cluster_id` | `faces` | `cluster_id` | Cluster gallery and exemplar selection |
| `idx_faces_unassigned` | `faces` | `photo_id` WHERE `person_id IS NULL` | Unassigned face workflows |
| `idx_faces_unclustered_unassigned` | `faces` | `cluster_id` WHERE `person_id IS NULL AND cluster_id IS NOT NULL` | Unnamed cluster list (identification UI) |

## Trade-offs

- Every index slows writes slightly during scan/ingest. The set above targets
  read-heavy gallery and cluster views only.
- No embedding index — vector search is out of scope for v1.0 (see
  `docs/PRODUCT_SCOPE.md`).

## Verification

```bash
python -m pytest tests/perf/test_query_plans.py -q
python -m pytest tests/perf/test_query_plans.py -m slow -q  # 50k faceless benchmark
```
