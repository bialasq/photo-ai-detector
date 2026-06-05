# API Versioning

Photo Organizer sidecar HTTP API uses URL path versioning.

## Current versions

| Version | Prefix | Status |
| --- | --- | --- |
| Legacy | `/api/*` | **Deprecated** — maintained for existing React clients |
| v1 | `/api/v1/*` | **Current** — all new endpoints land here first |

## Rules

1. **Major version bump** (`/api/v2/`) only for breaking contract changes (removed fields, changed semantics, auth changes).
2. **Non-breaking additions** (new optional JSON fields, new endpoints) stay within the current major version.
3. **Deprecation** of legacy `/api/*` routes:
   - Announce in `CHANGELOG.md` under `[Unreleased]`.
   - Add response header on deprecated routes: `X-Deprecation: true; use /api/v1/...`
   - Minimum **two minor releases** after deprecation before removal.
4. **Removal** requires frontend migration complete + documented in release notes.

## Migration policy (Phase 1 → 3)

- Phase 1 (this track): duplicate critical endpoints under `/api/v1/`; legacy `/api/*` unchanged.
- Phase 3: frontend moves to `src/api/client.ts` v1 base URL; legacy routes receive deprecation headers.
- Phase 4+: legacy routes removed after Tauri UI no longer references them.

## Client configuration

New TypeScript helpers should use:

```text
http://127.0.0.1:8000/api/v1/
```

See `src/api/client.ts`.
