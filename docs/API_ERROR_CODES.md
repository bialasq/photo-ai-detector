# API error codes

All failed API responses use a single JSON shape:

```json
{
  "error": "Human-readable message",
  "code": "NOT_FOUND",
  "hint": "Optional UI-safe guidance",
  "details": {}
}
```

Stack traces are **never** included in responses.

| Code | HTTP status | Meaning | Default hint |
|------|-------------|---------|--------------|
| `VALIDATION_ERROR` | 400, 422 | Request body or query parameters failed validation | Check parameters and JSON body |
| `PATH_INVALID` | 400 | Scan path rejected (missing, not a directory, drive root, traversal) | Choose an existing photo directory |
| `NOT_FOUND` | 404 | Photo, face, person, or cluster does not exist | The requested resource was not found |
| `SCAN_IN_PROGRESS` | 409 | Another folder scan is already running | Wait for the current scan to finish |
| `DB_LOCKED` | 409 | SQLite integrity / lock conflict | Retry in a moment |
| `SCAN_CANCELLED` | 409 | Scan stopped by user before completion | Start a new scan if needed |
| `AI_CORE_FAILURE` | 500 | DeepFace, embedding, or clustering failure | Check logs; restart if scan is stuck |
| `INTERNAL_ERROR` | 500 | Unexpected server or filesystem error | Check backend logs |

## Examples

**404 — unknown photo**

```json
{
  "error": "No photo found with id=999999",
  "code": "NOT_FOUND",
  "hint": "The requested resource was not found.",
  "details": null
}
```

**409 — scan already active**

```json
{
  "error": "A folder scan is already in progress. Poll GET /api/scan-status until is_active is false before starting another scan.",
  "code": "SCAN_IN_PROGRESS",
  "hint": "A folder scan is already running. Wait for it to finish or poll scan status.",
  "details": null
}
```

**422 — invalid JSON body**

```json
{
  "error": "Request validation failed",
  "code": "VALIDATION_ERROR",
  "hint": "The request was invalid. Check parameters and JSON body.",
  "details": {
    "errors": []
  }
}
```
