# Security Audit — OWASP ASVS Level 1 (Self-Assessment)

> **Task:** [3.1.1 OWASP ASVS L1 self-audit](../tasks/phase-3/3.1.1-owasp-asvs-l1-self-audit.md)  
> **Deliverable filename:** `docs/SECURITY_AUDIT.md` (task file originally referenced `OWASP_ASVS_L1_AUDIT.md`; this document is the canonical audit for 3.1.1).  
> **Date:** 2026-05-27  
> **Branch reviewed:** `feat/3.1.1-security-audit`  
> **Scope:** Python FastAPI sidecar (`main.py`), persistence (`database.py`), path layer (`path_validation.py`), errors/logging, Tauri shell integration. **Audit only — no remediations in this task.**

---

## 1. Threat model

| Property | Value |
|----------|--------|
| **Deployment** | Tauri 2 desktop app + local Python sidecar (PyInstaller or `venv`) |
| **Network** | Sidecar binds **loopback only** (`127.0.0.1` / `::1`); no public WAN exposure by design |
| **Users** | Single local Windows user; no multi-tenant auth |
| **Connectivity** | Offline-first (DeepFace/TF local); no cloud API for core flows |
| **User data accessed** | User-selected photo folders (recursive scan), `%AppData%\com.photo.organizer\` (DB, logs, thumbnails, FAISS) |
| **Sensitive data** | Full filesystem paths, face **embeddings** (512-d biometric-derived vectors), bounding boxes, person names |

### In-scope threats

- Path traversal / symlink escape during scan or thumbnail serving  
- DNS rebinding against loopback HTTP from a malicious web page  
- Unvalidated input on POST endpoints  
- Stack traces or internal details leaked to the WebView client  
- Plaintext PII (paths, names) in logs or API where hashing is the project standard  
- Dev-only routes or OpenAPI exposed in release builds  
- Secrets committed to the repository  

### Out-of-scope (documented N/A)

- Server-side session management, OAuth, password storage (no remote auth)  
- TLS for public endpoints (no public endpoints)  
- Rate limiting on WAN (loopback-only)  
- Multi-user access control / RBAC  

---

## 2. Methodology

1. Read source for bind host, path validation, dev gating, error handlers, logging, DB location, and all POST routes.  
2. Cross-check with existing tests: `tests/integration/test_bind_loopback.py`, `tests/integration/test_dev_endpoints.py`, `tests/integration/test_api_docs.py`, `tests/integration/test_validators.py`, `tests/test_path_validation.py`, `tests/test_security_and_db_integrity.py`.  
3. Map findings to **relevant ASVS 4.0 Level 1** controls; mark non-applicable chapters with justification.  
4. Record gaps as prioritized remediation tasks (**no fixes in 3.1.1**).

**Legend:** ✅ Pass · ⚠️ GAP · N/A Not applicable (with reason)

---

## 3. ASVS L1 — Control matrix (relevant categories)

### V1 — Architecture, design, and threat modeling

| ID | Control (ASVS L1 summary) | Status | Evidence | Notes |
|----|---------------------------|--------|----------|-------|
| V1.1 | Documented trust boundaries | ✅ | This document; `main.py` module docstring (L1–24) | Desktop UI → loopback HTTP → sidecar → SQLite/files |
| V1.2 | Components communicate securely for context | N/A | Loopback HTTP only | No inter-service WAN; Tauri WebView → `127.0.0.1:8000` |
| V1.4 | Server only binds intended network interfaces | ✅ | `main.py::assert_loopback_bind_host` (L1879–1895); `LOOPBACK_BIND_HOSTS` (L122); `run_server` calls assert before uvicorn (L2791–2798) | Non-loopback host → `ValueError`; integration test `tests/integration/test_bind_loopback.py::test_uvicorn_subprocess_binds_loopback_only` |
| V1.5 | HTTP middleware validates Host (DNS rebinding) | ⚠️ **GAP** | No Host-check middleware in `main.py::create_application` (L1898–1928) | CORS (`CORSMiddleware` L1914–1922) does not validate `Host`. **Remediation: task 3.1.2** |
| V1.14 | Untrusted sources cannot reach admin/dev interfaces | ✅ | `logging_config.py::is_dev_mode` (L37–46); `main.py::register_dev_routes` gated (L2712–2713); `create_application` disables docs when not dev (L1900–1911) | Release: `PHOTO_ORGANIZER_DEV` unset → no `/api/dev/*`, no `/docs` |
| V1.x | CORS restricted to local/Tauri origins | ✅ | `main.py::ALLOWED_CORS_ORIGINS` (L175–186); `ALLOWED_CORS_ORIGIN_REGEX` (L188–193) | Allows `tauri://localhost`, `127.0.0.1:*`, Vite dev ports — appropriate for desktop shell |

---

### V2 — Authentication

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V2.x | Authentication mechanisms | **N/A** | No login endpoints; `GET /health` unauthenticated (L1934–1947) | Single-user local app; trust boundary is OS user session + loopback bind. Remote attacker without local code execution cannot reach sidecar if bind + Host checks hold. |

---

### V3 — Session management

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V3.x | Session tokens, timeout, logout | **N/A** | No sessions in `main.py` / `database.py` | Stateless request/response over localhost |

---

### V4 — Access control

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V4.x | Authorization per resource | **N/A** | All API consumers are the same local user via Tauri | No multi-user data separation in DB schema |
| V4.x | Dev-only operations restricted | ✅ | `main.py::register_dev_routes` (L2716–2769); `tests/integration/test_dev_endpoints.py` | `/api/dev/reset-library` returns 404 when dev off |

---

### V5 — Validation, sanitization, and encoding

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V5.1 | Input validation on all inputs | ✅ | Pydantic v2 models in `schemas.py`; `routes/v1.py::FrontendErrorLogRequest` (L24–29) | See POST inventory below |
| V5.1 | POST `/api/scan-folder` | ✅ | `schemas.py::ScanFolderRequest` (L15–32); `main.py` handler (L1949+) | `min_length`, `max_length`, strip validator |
| V5.1 | POST `/api/clusters/identify` | ✅ | `schemas.py::IdentifyClusterRequest` (L35–97); model_validator for cluster_id XOR face_id | Name regex `PERSON_NAME_PATTERN` (L10) |
| V5.1 | POST `/api/people/merge` | ✅ | `schemas.py::MergePeopleRequest` (L100–118) | Distinct IDs enforced in model_validator |
| V5.1 | POST `/api/v1/log-error` | ✅ | `routes/v1.py::FrontendErrorLogRequest` | Bounded `message` / `stack` lengths |
| V5.1 | POST `/api/v1/scan-cancel` | ✅ | No body; idempotent flag set in `routes/v1.py::cancel_scan` (L47–58) | |
| V5.1 | POST `/api/dev/*` (dev only) | ✅ | `schemas.py::DevSimulateScanRequest` (L121–141); routes only if dev (L2712+) | |
| V5.2 | Path traversal blocked (scan root) | ✅ | `path_validation.py::validate_scan_path` (L49–101) — null byte (L59–60), `..` (L64–69), symlink escape at root (L80–85), drive root (L94–99) | Tests: `tests/test_path_validation.py` |
| V5.2 | Path traversal blocked (photo serve) | ✅ | `main.py::resolve_photo_source_path` (L1068–1102) — null byte, `..`, missing file, suffix allowlist | Tests: `tests/test_security_and_db_integrity.py` |
| V5.2 | Scan sandbox — files stay under user folder | ⚠️ **GAP** | `main.py::discover_image_files_recursively` (L1182–1229) uses `os.walk`; root validated by `validate_scan_path`, but **nested directory symlinks** can resolve outside root; `(current_dir / filename).resolve()` (L1206) follows symlinks | Real read of files outside user-selected tree. **Remediation: task 3.1.4** |
| V5.3 | Output encoding / safe errors | ✅ | `errors.py::ErrorResponse` (L63–75); path errors use `path_hash` not raw path (`path_validation.py::_reject` L21–25) | API path errors do not echo full user path in `error` string |

#### POST endpoint inventory

| Method | Path | Body schema | Dev-only |
|--------|------|-------------|----------|
| POST | `/api/scan-folder` | `ScanFolderRequest` | No |
| POST | `/api/clusters/identify` | `IdentifyClusterRequest` | No |
| POST | `/api/people/merge` | `MergePeopleRequest` | No |
| POST | `/api/v1/log-error` | `FrontendErrorLogRequest` | No |
| POST | `/api/v1/scan-cancel` | — | No |
| POST | `/api/dev/reset-library` | — | Yes |
| POST | `/api/dev/simulate-scan` | `DevSimulateScanRequest` | Yes |

---

### V6 — Stored cryptography

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V6.x | Encryption of sensitive data at rest | **N/A** (conscious decision) | `database.py::serialize_embedding_blob`; DB at `get_app_data_dir()/organizer.db` (L141–162) | **Offline single-user desktop:** DB lives under `%AppData%\com.photo.organizer\`, protected by **Windows user ACLs** (same trust as user's photo library). An attacker with filesystem access to AppData can also read original images — encrypting embedding blobs alone (SQLCipher/DPAPI) adds operational complexity without meaningful uplift. Documented as accepted risk for v1.0; revisit if multi-user or sync added. |
| V6.x | No hardcoded crypto keys in repo | ✅ | Grep: no API keys/secrets in `main.py`, `database.py`, `src-tauri/` | `.gitignore` excludes `.env`, `*.db` (L41–44) |

---

### V7 — Error handling and logging

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V7.1 | Errors do not leak stack traces to client | ✅ | `errors.py::unhandled_exception_handler` (L238–248) returns generic `"Internal server error"`; `ErrorResponse.details` docstring: "never contains stack traces" (L72–74) | Stack in `JsonLogFormatter` `exc` field **server log only** (L95–96) |
| V7.1 | Domain errors mapped to stable JSON | ✅ | `errors.py::register_exception_handlers` (L252–259); `main.py::raise_http_exception_from_error` (L1331–1417) | `PathValidationError` → 400 `PATH_INVALID` |
| V7.2 | Security events logged | ✅ | Structured JSON logs via `logging_config.py::JsonLogFormatter`; scan events use `ctx` + `hash_path_for_log` (`main.py` L1218–1227) | Rotating file under AppData `logs/backend.log` |
| V7.3 | Logs avoid sensitive data (paths/PII) | ⚠️ **GAP** | `logging_config.py::hash_path_for_log` (L65–75) used in scan/path validation; **but** `main.py::ThumbnailEngine.get_or_create_thumbnail` logs plaintext `source_path` at INFO (L960–963); `ScanProgressState.set_current_file` stores full path (L492–494), exposed via `GET /api/scan-status` snapshot (L438, L1563) | Inconsistent with hashing policy; user folder names (often surnames) leak to logs and API. **New remediation task (P1)** — see GAP register |
| V7.4 | Validation errors sanitized | ✅ | `errors.py::validation_exception_handler` (L164–184) | Pydantic `ctx` values stringified |

---

### V8 — Data protection

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V8.1 | User data location documented | ✅ | `database.py::get_app_data_dir` (L61–90); `logging_config.py` module docstring (L4–9) | Windows: `%AppData%\com.photo.organizer` |
| V8.2 | Biometric embeddings handling | **N/A** (see V6) | Stored as BLOB in `faces.embedding`; **not** returned in REST JSON models (`FacePreviewItem` has bbox + URLs only, `main.py` L257–267) | Embeddings stay server-side; UI gets thumbnails by ID |
| V8.2 | Face embeddings in API responses | ✅ | No endpoint returns raw `embedding` vectors in response models | Search/gallery return `photo_id` + `file_path` only |
| V8.3 | Filesystem paths in API | ⚠️ **GAP** (PII) | `SearchResultItem.file_path` (L241–245); gallery/search handlers (e.g. L2047, L2112); `ScanStatusResponse.current_file` doc says "Basename" (L220–222) but code sets full path (L1563) | Intentional for desktop file opening, but conflicts with privacy docstring and hashing elsewhere. **Same P1 task as V7.3** |
| V8.3 | Path hashes in error details | ✅ | `path_validation.py` details `path_hash` only (L21–25); tests assert Windows not in error body (`tests/test_path_validation.py::test_api_rejects_traversal_path`) | |

---

### V9 — Communication

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V9.x | TLS for client-server | **N/A** | Loopback HTTP | TLS on `127.0.0.1` not standard for local sidecar |
| V9.x | Certificate validation | **N/A** | No outbound HTTPS in core sidecar | |

---

### V10 — Malicious code

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V10.x | Dependency vulnerability monitoring | ⚠️ **GAP** | No `pip-audit` / `npm audit` in `.github/workflows/ci.yml` lint job | **Remediation: task 3.1.5** (warning-only CI) |
| V10.x | No eval/exec of user input | ✅ | Scan/AI paths use pathlib + Pillow/OpenCV; no `eval()` on request bodies | |

---

### V11 — Business logic

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V11.x | Anti-automation / rate limits | **N/A** | Local single user | |
| V11.1 | Scan concurrency guard | ✅ | `main.py::start_folder_scan` (L1717–1741) — 409 `SCAN_IN_PROGRESS` if active | Prevents parallel full-library scans |

---

### V12 — Files and resources

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V12.1 | Untrusted paths validated before use | ✅ | Scan: `path_validation.py::validate_scan_path`; photos: `main.py::resolve_photo_source_path` | |
| V12.3 | Symlink rejection at scan entry | ✅ | `path_validation.py::_symlink_escape_detected` (L34–46) | Test: `tests/test_path_validation.py::test_rejects_symlink_outside_tree` |
| V12.4 | Scan confined to user-selected directory | ⚠️ **GAP** | Same as V5.2 — `discover_image_files_recursively` / `_execute_folder_scan_async` (L1525+) do not re-assert `resolved.is_relative_to(scan_root)` per file | **P1 — task 3.1.4** |
| V12.5 | Whole-disk scan requires explicit flag | ✅ | `validate_scan_path(..., allow_whole_disk=False)` default (L94–99); dev simulate uses normal validation | Windows drive-root test skipped on Linux CI |
| V12.x | Blocked system directories during walk | ✅ | `main.py::_is_blocked_scan_directory` (L134+); `EXCLUDED_SCAN_DIR_NAMES` applied in walk (L1199–1203) | Skips `.git`, `Program Files`, etc. |
| V12.x | Max files limit (DoS) | ✅ | `main.py::_max_scan_files` / `discover_image_files_recursively` (L1195, L1210–1215) | Default 20_000 via `PHOTO_ORGANIZER_MAX_SCAN_FILES` |

---

### V13 — API and web service

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V13.1 | API surface minimal | ✅ | Routes registered in `main.py::register_routes`; v1 router in `routes/v1.py` | No GraphQL/admin console |
| V13.2 | Content-Type / method restrictions | ✅ | FastAPI defaults; CORS methods `GET, POST, OPTIONS` only (L1919) | |
| V13.4 | Admin/dev API disabled in production | ✅ | `is_dev_mode` + conditional `register_dev_routes` (L2712); Tauri release builds should not set `PHOTO_ORGANIZER_DEV=1` (`src-tauri/src/lib.rs` sets only in debug ~L298) | |
| V13.4 | OpenAPI / Swagger disabled in release | ✅ | `create_application` sets `docs_url`, `redoc_url`, `openapi_url` to `None` when not dev (L1909–1911); `tests/integration/test_api_docs.py` | |
| V13.5 | Host header validation | ⚠️ **GAP** | Same as V1.5 | **Task 3.1.2** |

---

### V14 — Configuration

| ID | Control | Status | Evidence | Notes |
|----|---------|--------|----------|-------|
| V14.1 | No secrets in source control | ✅ | `.gitignore` — `.env`, credentials patterns; repo uses env vars for overrides (`PHOTO_ORGANIZER_*`) | |
| V14.2 | Debug features off in release | ✅ | Dev routes + stderr logging only when `PHOTO_ORGANIZER_DEV=1` (`logging_config.py::setup_logging` L130–133) | Release sidecar: file logging only at INFO |
| V14.3 | Secure defaults | ✅ | `DEFAULT_HOST = "127.0.0.1"` (L119); DB defaults to AppData not CWD (`database.py::get_db_path` L162) | Hard fail if `APPDATA` missing without override (L78–81) |
| V14.4 | Dependency audit in build pipeline | ✅ | `.github/workflows/ci.yml` — `security-audit` job (`pip-audit` + `npm audit`, warning-only) | **Task 3.1.5** — see GAP-005 |

---

### V15+ / Other ASVS chapters

| Chapter | Status | Reason |
|---------|--------|--------|
| V15 — Security headers (CSP, X-Content-Type-Options) | ✅ | **Backend:** `main.py::SecurityHeadersMiddleware` — `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` on all responses (`tests/integration/test_security_headers.py`). **WebView:** `src-tauri/tauri.conf.json` — enforcing CSP (task 3.1.3). CSP is **not** sent on API JSON/JPEG responses (WebView policy only). | Manual DevTools verification required — Tauri has no `Content-Security-Policy-Report-Only` config field |
| V16 — WebRTC / SMS / email | **N/A** | Not used |
| V17 — Internet of things | **N/A** | Desktop app |

---

## 4. Summary scorecard

| Category | ✅ | ⚠️ GAP | N/A |
|----------|---:|-------:|----:|
| V1 Architecture | 3 | 1 | 1 |
| V2–V4 Auth / session / access | 1 | 0 | 6+ |
| V5 Validation | 10 | 1 | 0 |
| V6 Crypto | 1 | 0 | 1 |
| V7 Errors & logging | 3 | 1 | 0 |
| V8 Data protection | 3 | 1 | 1 |
| V9–V11 Comm / malicious / logic | 2 | 1 | 4+ |
| V12 Files | 4 | 1 | 0 |
| V13 API | 4 | 1 | 0 |
| V14 Config | 3 | 1 | 0 |

**Release gate (3.1.1):** All ⚠️ items have assigned remediation tasks below. This audit does **not** implement fixes.

---

## 5. GAP register (prioritized)

| Priority | GAP ID | Description | Evidence | Remediation task |
|----------|--------|-------------|----------|------------------|
| **P1** | GAP-001 | **DNS rebinding:** no `Host` header validation; malicious page could script requests to `127.0.0.1:8000` with attacker `Host`. | `main.py::create_application` — no host middleware | **[3.1.2](../tasks/phase-3/3.1.2-dns-rebinding-mitigation-host-header-check.md)** DNS rebinding mitigation |
| **P1** | GAP-002 | **Scan symlink sandbox escape:** after valid scan root, `os.walk` + `.resolve()` can ingest files **outside** the user-selected folder via nested symlinks. | `main.py::discover_image_files_recursively` (L1197–1209); `path_validation.py` only checks root (L80–85) | **[3.1.4](../tasks/phase-3/3.1.4-path-validation-hardening-symlink-rejection.md)** — per-file `is_relative_to(scan_root)` or don't follow symlinks in walk |
| **P1** | GAP-003 | **PII path leakage (logs + API):** plaintext filesystem paths at INFO and in scan status, inconsistent with `hash_path_for_log`. | `main.py::ThumbnailEngine.get_or_create_thumbnail` (L960–963); `ScanProgressState.set_current_file` (L492–494, L1563); `GET /api/scan-status` returns `current_file` (L2008); doc mismatch L220–222 | **New task (proposed):** `3.1.x-logging-api-path-privacy` — use `hash_path_for_log` / basename in logs; align `current_file` with doc or hash; audit other INFO logs |
| **P2** | GAP-004 | **`last_error` in scan-status** may surface raw exception strings from ingestion/clustering to the WebView. | `ScanProgressState.last_error` (L224–227); `finish_scan(error_message=str(exc))` (L1688, L1762) | **New task (proposed):** sanitize user-facing scan errors (stable codes + safe message) |
| **P2** | GAP-005 | **Dependency vulnerability scanning** in CI (`security-audit` job: `pip-audit` + `npm audit`, warning-only). | `.github/workflows/ci.yml` — `continue-on-error: true`; does **not** fail PR | **[3.1.5](../tasks/phase-3/3.1.5-pip-audit-npm-audit-w-ci-warning-only.md)** — **Remediated (warning-only)**. **Note:** `pip-audit -r` audits **declared** requirement ranges, not the installed production venv; resolved versions may differ from a real `pip install` (conscious speed vs. precision trade-off while scans remain non-blocking). |
| **P3** | GAP-006 | **Security headers / CSP** for Tauri WebView + sidecar HTTP responses. | `main.py::SecurityHeadersMiddleware`; `src-tauri/tauri.conf.json` | **[3.1.3](../tasks/phase-3/3.1.3-security-headers-csp-w-tauri-webview-x-content-type-options.md)** — **Remediated.** **CSP notes:** (1) Tauri exposes only enforcing `csp` (no native report-only) — validate in WebView DevTools after changes. (2) `img-src` **must** include `http://127.0.0.1:8000` / `http://localhost:8000` — gallery loads thumbnails/full JPEG from sidecar `<img src>`, not `connect-src`. (3) `style-src 'unsafe-inline'` — required for React `style={{…}}` and `react-window` cell layout. (4) `script-src 'unsafe-eval'` — kept for `tauri dev` + Vite HMR; production Vite bundle uses external `.js` only (candidate to drop in follow-up after manual verification). |

### Conscious decisions (not GAPs)

| Topic | Decision | Rationale |
|-------|----------|-----------|
| **Embeddings encryption at rest** | N/A — no SQLCipher/DPAPI for v1 | Single-user offline; AppData ACL; attacker with AppData access has originals — see V6/V8 |
| **No authentication on localhost API** | N/A | Trust OS user + loopback; mitigated by bind + (pending) Host check |
| **Full `file_path` in gallery/search API** | Accepted for v1 desktop | Required to open files in OS shell; document in privacy notes; optional future: serve by `photo_id` only in UI |

---

## 6. Verification checklist (3.1.1 Definition of Done)

- [x] Relevant ASVS L1 categories evaluated (V1, V5, V7, V8, V12, V13, V14 + N/A blocks for V2–V4, V6, V9–V11, V15+)
- [x] Every ✅ row cites `file::symbol` or test path
- [x] Every N/A row has desktop/loopback justification
- [x] Every ⚠️ row maps to a remediation task (existing or proposed)
- [x] No gaps fixed in this task (documentation only)
- [x] Filename `docs/SECURITY_AUDIT.md` with cross-reference to task 3.1.1 / `OWASP_ASVS_L1_AUDIT.md` naming note
- [ ] PR review + second approval (process)
- [ ] CHANGELOG `[Unreleased]` entry (when merging)

---

*End of security audit document.*
