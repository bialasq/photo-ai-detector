# Product Scope — Photo AI Detector / Photo Organizer

**Version target:** v1.0.0 (MVP)  
**Document owner:** Luq  
**Last updated:** 2026-06-05  
**Status:** Approved for Phase 0 — scope freeze

This document freezes the MVP scope for v1.0.0. Features not listed as must-have are out of scope unless explicitly promoted in a later phase.

---

## Product summary

Photo Organizer is a **100% offline** Windows desktop application. It scans folders of JPG/PNG images on disk, detects faces locally (DeepFace / ArcFace), clusters unknown faces (DBSCAN), and lets the user name people and browse photos by person — including group shots and faceless images (landscapes, architecture, etc.).

No cloud accounts, no upload, no multi-user server. All inference and SQLite data stay on the user's machine.

---

## Platform decision (v1.0)

| Decision | Choice |
|----------|--------|
| **Supported platform** | **Windows 10 and Windows 11, x64 only** |
| **Installer** | NSIS / MSI via Tauri bundle (see `src-tauri/tauri.conf.json`) |
| **macOS / Linux** | **Out of scope for v1.0** — candidate for v1.1+ after Windows release is stable |
| **Minimum hardware** | 64-bit CPU, 8 GB RAM recommended, ~2 GB free disk for app + models; additional space for photo library DB and thumbnail cache |

Development may run on other OSes for backend/UI work, but **v1.0.0 is shipped and supported on Windows x64 only**.

---

## Must-have user stories (v1.0)

Each story is **required** for v1.0.0 release. Implementation status reflects the codebase at scope-freeze time.

| ID | User story | Acceptance (MVP) | Status |
|----|------------|------------------|--------|
| **US-01** | As a user, I can **scan a folder** of photos so that faces are detected and stored locally. | Folder picker (Tauri dialog); recursive scan; progress (`scanning` → `clustering`); per-file errors do not abort batch; cap via `PHOTO_ORGANIZER_MAX_SCAN_FILES`. | Implemented |
| **US-02** | As a user, I can **name unnamed face clusters** so that they become people in my library. | People Profiles: unnamed clusters list; `POST /api/clusters/identify`; face-cropped avatars. | Implemented |
| **US-03** | As a user, I can **browse the gallery** and filter by person(s) and AI status. | Gallery grid; multi-person AND filter; filters `all` / `processed` / `unprocessed` / `faceless`; thumbnails. | Implemented |
| **US-04** | As a user, I can view **faceless photos** (no detected faces) separately from people photos. | `has_faces=0` after scan; gallery filter **Faceless / No Faces**; no infinite re-scan. | Implemented |
| **US-05** | As a user, I can review **noise faces** (DBSCAN outliers) and assign them to a person. | Noise Inspector UI; `GET /api/clusters/noise`; assign to new or existing person. | Implemented |
| **US-06** | As a user, I can **merge duplicate people** and search by name. | `POST /api/people/merge`; `GET /api/search?names=`. | Implemented |
| **US-07** | As a user, I can **uninstall** the application from Windows without a broken installer. | NSIS/MSI uninstall via Settings / Control Panel; app binaries removed cleanly. | Installer present; QA on clean VM required before release |
| **US-08** | As a user, my **data stays local** and the app works **offline**. | API bound to `127.0.0.1`; no required network; DB and cache on local disk. | Implemented |

### Supporting capabilities (must-have, non-UI)

- Face bounding boxes stored per face; server-side crop thumbnails for people/cluster previews.
- Group photos: one photo → many faces; gallery person filter includes group shots.
- Typed AI errors (`AICoreError` hierarchy) and global handler for uncaught domain errors (task 1.1.1).
- Python 3.12 + Tauri 2 sidecar packaging path documented in README.

---

## Explicit out-of-scope (v1.0)

The following are **not** in MVP. Do not implement them in v1.0.x without a scope change and roadmap update.

| Area | Out of scope | Notes |
|------|--------------|-------|
| **Mobile** | iOS, Android, responsive mobile web | Desktop-only product. |
| **Multi-user / network** | Accounts, sync, LAN sharing, remote API | Single user, single machine, loopback API. |
| **Cloud** | Upload, backup to cloud, cloud inference | Offline-first; no third-party photo services. |
| **EXIF / metadata editing** | Edit tags, dates, GPS in UI | Read paths from filesystem only; no metadata editor. |
| **Export integrations** | Google Photos, iCloud, Flickr, social export | No export pipelines in v1.0. |
| **macOS / Linux installers** | Native builds for non-Windows | Deferred to v1.1+. |
| **Auto-update** | Tauri updater, delta patches | Planned Phase 4; not blocking v1.0.0 feature set. |
| **GPU requirement** | CUDA as mandatory | CPU-only acceptable for v1.0; GPU optional later. |
| **FAISS / vector DB** | Replace DBSCAN with FAISS at scale | Phase 2 roadmap; current stack is SQLite + DBSCAN. |
| **In-app photo editing** | Crop, rotate, filters | Viewer/lightbox only. |
| **Duplicate photo detection** | Perceptual hash dedup | Face clustering ≠ duplicate file detection. |
| **Multi-library / profiles** | Several isolated libraries in one install | Single SQLite library per install (path TBD for production `%AppData%`). |

---

## Uninstall and user data (`%AppData%`)

**Decision (Luq, v1.0.0):**

| Item | On uninstall |
|------|----------------|
| **Application binaries** | **Removed** by NSIS/MSI uninstaller. |
| **User library (`organizer.db`, thumbnail cache, logs)** | **Preserved by default** under `%AppData%\com.photo.organizer\` (or equivalent product data path once migration lands). |
| **Rationale** | Avoid accidental loss of indexed faces and metadata; matches user expectation for photo/library apps. |
| **User cleanup** | Document manual deletion path in future `docs/USER_GUIDE.md`: *Settings → Apps → Uninstall* removes the app; to remove all data, delete the `%AppData%\com.photo.organizer` folder after uninstall. |
| **Future (v1.1+)** | Optional uninstaller checkbox: *"Remove my photo library and cached thumbnails"* — not required for v1.0.0. |

Until `%AppData%` migration is implemented (production hardening task), development builds may still use `organizer.db` at repo root; this decision applies to **shipped** v1.0.0 layout.

---

## MVP success criteria (v1.0.0)

- Clean Windows 10/11 VM: install → scan test folder (50+ images) → name clusters → gallery filters (person + faceless) → uninstall app binaries.
- No cloud dependency; scan and browse work offline after first model load.
- Documented scope: any feature request not in **Must-have** defaults to **Out-of-scope** unless the roadmap is updated.

---

## Related documents

| Document | Purpose |
|----------|---------|
| [README.md](../README.md) | Setup, architecture, API reference |
| [tasks/README.md](../tasks/README.md) | Cursor prompt plan (63 tasks, phases 0–4) |
| Task **0.1** | Source task for this file |

---

## Change control

Changes to must-have or out-of-scope lists require:

1. Update to this file (PR with Luq or Grzesiek approval).
2. Entry in `CHANGELOG.md` under `[Unreleased]`.
3. If release-impacting: bump target version or defer to next minor.
