# Tasks Index — Photo AI Detector Roadmap

Plan promptów Cursora — **63 taski** w fazach 0–4. Każdy plik `.md` to specyfikacja jednej sesji (Context, Acceptance Criteria, gotowy **Cursor Prompt**).

## Jak uruchomić task w Cursorze

1. Wybierz plik z tabeli poniżej (np. `phase-1/1.2.1-gate-api-dev-...md`).
2. W nowym chacie Agent mode:

   ```
   Wykonaj task @tasks/phase-1/1.2.1-gate-api-dev-za-photo-organizer-dev-1-krytyczne.md
   Przestrzegaj @.cursorrules
   ```

3. Duże taski P0 (8h+): użyj wzorca z `phase-1/1.1.1-EXECUTION-PROMPTS.md` — podziel na sesje z human gate między commitami.

## Struktura repo (ścieżki w promptach)

| Obszar | Ścieżka |
|--------|---------|
| Python backend | `main.py`, `ai_core.py`, `database.py` (root) |
| React UI | `src/` |
| Tauri | `src-tauri/` |
| Testy Python | `tests/` |
| Migracje SQL (plan) | `migrations/` (task 1.3.2) |

Master plan: `docs/PRODUCTION_ROADMAP.md`. Architektura docelowa: `docs/ARCHITECTURE.md`.

Import do GitHub Issues: `bash scripts/import_issues.sh` (wymaga `gh` CLI uwierzytelnionego).

---

Wygenerowane z `generate.py`. 63 taski × 4 fazy + faza 0 (rozbite drobniej niż 58 z roadmapy master).


## Sumarycznie


- **Total tasks:** 63
- **Total hours:** 331
- **Grzesiek:** 32 (162 h)
- **Luq:** 31 (169 h)
- **Phase 0:** 5 tasks (8 h)
- **Phase 1:** 16 tasks (71 h)
- **Phase 2:** 14 tasks (94 h)
- **Phase 3:** 14 tasks (97 h)
- **Phase 4:** 14 tasks (61 h)
- **P0:** 47 tasks
- **P1:** 15 tasks
- **P2:** 1 tasks

## Per phase


### Phase 0

| ID | Title | Owner | H | Prio |
|---|---|---|---|---|
| 0.1 | [PRODUCT_SCOPE.md — must-have user stories + out-of-scope](phase-0/0.1-product-scope-md-must-have-user-stories-out-of-scope.md) | Luq | 3 | P0 |
| 0.2 | [Zakup certyfikatu code signing Authenticode](phase-0/0.2-zakup-certyfikatu-code-signing-authenticode.md) | Luq | 1 | P0 |
| 0.3 | [Setup .cursorrules w roocie repo](phase-0/0.3-setup-cursorrules-w-roocie-repo.md) | Grzesiek | 1 | P0 |
| 0.4 | [TECH_DEBT.md — backlog 'nie naprawiać przy okazji'](phase-0/0.4-tech-debt-md-backlog-nie-naprawia-przy-okazji.md) | Grzesiek | 1 | P0 |
| 0.5 | [GitHub Issues templates + Labels](phase-0/0.5-github-issues-templates-labels.md) | Grzesiek | 2 | P0 |

### Phase 1

| ID | Title | Owner | H | Prio |
|---|---|---|---|---|
| 1.1.1 | [ai_core.py graceful error handling (DeepFace + DBSCAN)](phase-1/1.1.1-ai-core-py-graceful-error-handling-deepface-dbscan.md) | Grzesiek | 8 | P0 |
| 1.1.2 | [Structured logging w %AppData% z rotacją](phase-1/1.1.2-structured-logging-w-appdata-z-rotacj.md) | Grzesiek | 6 | P0 |
| 1.1.3 | [Standard error responses {error, code, hint}](phase-1/1.1.3-standard-error-responses-error-code-hint.md) | Grzesiek | 4 | P0 |
| 1.1.4 | [React Error Boundaries (App, Gallery, PeopleGrid)](phase-1/1.1.4-react-error-boundaries-app-gallery-peoplegrid.md) | Luq | 5 | P0 |
| 1.2.1 | [Gate /api/dev/* za PHOTO_ORGANIZER_DEV=1 [KRYTYCZNE]](phase-1/1.2.1-gate-api-dev-za-photo-organizer-dev-1-krytyczne.md) | Grzesiek | 3 | P0 |
| 1.2.2 | [Przenieś DB do %AppData%\com.photo.organizer\organizer.db [KRYTYCZNE]](phase-1/1.2.2-przenie-db-do-appdata-com-photo-organizer-organizer-db-kryty.md) | Grzesiek | 4 | P0 |
| 1.2.3 | [Path traversal validation w endpointach skanu](phase-1/1.2.3-path-traversal-validation-w-endpointach-skanu.md) | Luq | 4 | P0 |
| 1.2.4 | [Wyłącz /docs i /redoc w release](phase-1/1.2.4-wy-cz-docs-i-redoc-w-release.md) | Grzesiek | 1 | P0 |
| 1.2.5 | [Bind tylko 127.0.0.1 + assert w starcie](phase-1/1.2.5-bind-tylko-127-0-0-1-assert-w-starcie.md) | Grzesiek | 1 | P0 |
| 1.3.1 | [DB indeksy: faces(person_id), photos(processed, has_faces)](phase-1/1.3.1-db-indeksy-faces-person-id-photos-processed-has-faces.md) | Luq | 4 | P0 |
| 1.3.2 | [Schema versioning + numbered SQL migrations (no Alembic)](phase-1/1.3.2-schema-versioning-numbered-sql-migrations-no-alembic.md) | Grzesiek | 8 | P0 |
| 1.3.3 | [Batch inserts (50-100) w transakcji](phase-1/1.3.3-batch-inserts-50-100-w-transakcji.md) | Grzesiek | 5 | P0 |
| 1.3.4 | [PRAGMA foreign_keys=ON + integrity_check przy starcie](phase-1/1.3.4-pragma-foreign-keys-on-integrity-check-przy-starcie.md) | Luq | 2 | P0 |
| 1.4.1 | [/api/v1/ prefix dla nowych endpointów](phase-1/1.4.1-api-v1-prefix-dla-nowych-endpoint-w.md) | Grzesiek | 4 | P1 |
| 1.4.2 | [Pydantic v2 validators na wszystkich POST](phase-1/1.4.2-pydantic-v2-validators-na-wszystkich-post.md) | Grzesiek | 6 | P0 |
| 1.4.3 | [Scan cancellation: POST /api/v1/scan-cancel + UI button](phase-1/1.4.3-scan-cancellation-post-api-v1-scan-cancel-ui-button.md) | Luq | 6 | P0 |

### Phase 2

| ID | Title | Owner | H | Prio |
|---|---|---|---|---|
| 2.0.1 | [Benchmark baseline: skan 1k/5k/10k zdjęć [PRZED FAISS]](phase-2/2.0.1-benchmark-baseline-skan-1k-5k-10k-zdj-przed-faiss.md) | Luq | 4 | P0 |
| 2.0.2 | [docs/BENCHMARKS.md + scripts/benchmark.py w repo](phase-2/2.0.2-docs-benchmarks-md-scripts-benchmark-py-w-repo.md) | Grzesiek | 3 | P0 |
| 2.1.1 | [Batch detection 32 obrazów w ai_core](phase-2/2.1.1-batch-detection-32-obraz-w-w-ai-core.md) | Grzesiek | 10 | P0 |
| 2.1.2 | [Async I/O dla load obrazów (PIL) + decouple od inference](phase-2/2.1.2-async-i-o-dla-load-obraz-w-pil-decouple-od-inference.md) | Grzesiek | 8 | P1 |
| 2.1.3 | [GPU detection (CUDA) za flagą + graceful fallback](phase-2/2.1.3-gpu-detection-cuda-za-flag-graceful-fallback.md) | Luq | 6 | P1 |
| 2.1.4 | [/api/v1/progress — fazy (scan/embed/cluster) + ETA](phase-2/2.1.4-api-v1-progress-fazy-scan-embed-cluster-eta.md) | Grzesiek | 7 | P1 |
| 2.1.5 | [Cancellation tokens w batch loop (wzmocnienie 1.4.3)](phase-2/2.1.5-cancellation-tokens-w-batch-loop-wzmocnienie-1-4-3.md) | Grzesiek | 3 | P1 |
| 2.2.1 | [FAISS integration: IndexFlatIP + persist w AppData](phase-2/2.2.1-faiss-integration-indexflatip-persist-w-appdata.md) | Grzesiek | 12 | P0 |
| 2.2.2 | [Incremental clustering: FAISS top-k → assign or new](phase-2/2.2.2-incremental-clustering-faiss-top-k-assign-or-new.md) | Luq | 8 | P0 |
| 2.2.3 | [Silhouette / Davies-Bouldin score po re-clusteringu](phase-2/2.2.3-silhouette-davies-bouldin-score-po-re-clusteringu.md) | Luq | 6 | P1 |
| 2.2.4 | [Auto-tune eps, min_samples (grid search)](phase-2/2.2.4-auto-tune-eps-min-samples-grid-search.md) | Grzesiek | 7 | P2 |
| 2.3.1 | [react-window w Gallery przy >200 zdjęć](phase-2/2.3.1-react-window-w-gallery-przy-200-zdj.md) | Luq | 8 | P0 |
| 2.3.2 | [IntersectionObserver lazy load miniaturek](phase-2/2.3.2-intersectionobserver-lazy-load-miniaturek.md) | Luq | 6 | P0 |
| 2.3.3 | [Cache miniaturek LRU eviction (limit 500 MB w AppData)](phase-2/2.3.3-cache-miniaturek-lru-eviction-limit-500-mb-w-appdata.md) | Grzesiek | 6 | P1 |

### Phase 3

| ID | Title | Owner | H | Prio |
|---|---|---|---|---|
| 3.1.1 | [OWASP ASVS L1 self-audit](phase-3/3.1.1-owasp-asvs-l1-self-audit.md) | Luq | 8 | P0 |
| 3.1.2 | [DNS rebinding mitigation (Host header check)](phase-3/3.1.2-dns-rebinding-mitigation-host-header-check.md) | Luq | 4 | P1 |
| 3.1.3 | [Security headers (CSP w Tauri webview, X-Content-Type-Options)](phase-3/3.1.3-security-headers-csp-w-tauri-webview-x-content-type-options.md) | Grzesiek | 4 | P1 |
| 3.1.4 | [Path validation hardening + symlink rejection](phase-3/3.1.4-path-validation-hardening-symlink-rejection.md) | Luq | 5 | P0 |
| 3.1.5 | [pip-audit + npm audit w CI (warning only)](phase-3/3.1.5-pip-audit-npm-audit-w-ci-warning-only.md) | Grzesiek | 4 | P1 |
| 3.2.1 | [Unit tests Python: 80%+ coverage na database/main/ai_core](phase-3/3.2.1-unit-tests-python-80-coverage-na-database-main-ai-core.md) | Luq | 18 | P0 |
| 3.2.2 | [Integration tests: FastAPI TestClient dla każdego endpointu](phase-3/3.2.2-integration-tests-fastapi-testclient-dla-ka-dego-endpointu.md) | Luq | 10 | P0 |
| 3.2.3 | [E2E tests: Playwright na Tauri webview](phase-3/3.2.3-e2e-tests-playwright-na-tauri-webview.md) | Luq | 8 | P1 |
| 3.2.4 | [Benchmarks jako pytest-benchmark, fail na regresji >20%](phase-3/3.2.4-benchmarks-jako-pytest-benchmark-fail-na-regresji-20.md) | Grzesiek | 6 | P1 |
| 3.2.5 | [GitHub Actions ci.yml (pytest + npm build + lint)](phase-3/3.2.5-github-actions-ci-yml-pytest-npm-build-lint.md) | Grzesiek | 7 | P0 |
| 3.2.6 | [Vitest: FaceCropImage, Gallery filters, PeopleGrid](phase-3/3.2.6-vitest-facecropimage-gallery-filters-peoplegrid.md) | Luq | 8 | P0 |
| 3.3.1 | [README sekcja Production Install + Building from Source](phase-3/3.3.1-readme-sekcja-production-install-building-from-source.md) | Luq | 4 | P0 |
| 3.3.2 | [docs/ARCHITECTURE.md + Mermaid diagram](phase-3/3.3.2-docs-architecture-md-mermaid-diagram.md) | Luq | 5 | P0 |
| 3.3.3 | [docs/USER_GUIDE.md ze screenshotami](phase-3/3.3.3-docs-user-guide-md-ze-screenshotami.md) | Luq | 6 | P0 |

### Phase 4

| ID | Title | Owner | H | Prio |
|---|---|---|---|---|
| 4.1.1 | [Tauri updater plugin + signed GitHub Releases feed](phase-4/4.1.1-tauri-updater-plugin-signed-github-releases-feed.md) | Grzesiek | 8 | P1 |
| 4.1.2 | [Code signing MSI Authenticode [BLOCKER]](phase-4/4.1.2-code-signing-msi-authenticode-blocker.md) | Luq | 6 | P0 |
| 4.1.4 | [Release workflow: tag v* → build → sign → upload](phase-4/4.1.4-release-workflow-tag-v-build-sign-upload.md) | Grzesiek | 5 | P0 |
| 4.1.5 | [Splash screen 'Loading AI models...' (cold start)](phase-4/4.1.5-splash-screen-loading-ai-models-cold-start.md) | Luq | 4 | P0 |
| 4.2.1 | [Sentry integration, DEFAULT OFF, opt-in w Settings](phase-4/4.2.1-sentry-integration-default-off-opt-in-w-settings.md) | Grzesiek | 6 | P1 |
| 4.2.2 | [/api/v1/health rozszerzony (db_ok, model_loaded, schema_version, disk_free)](phase-4/4.2.2-api-v1-health-rozszerzony-db-ok-model-loaded-schema-version.md) | Grzesiek | 3 | P0 |
| 4.2.3 | [Help → 'Copy diagnostics' do schowka](phase-4/4.2.3-help-copy-diagnostics-do-schowka.md) | Luq | 4 | P0 |
| 4.3.1 | [Backup feature: Help → Export library → ZIP](phase-4/4.3.1-backup-feature-help-export-library-zip.md) | Luq | 6 | P0 |
| 4.3.2 | [Data retention: cleanup cache miniaturek >90 dni](phase-4/4.3.2-data-retention-cleanup-cache-miniaturek-90-dni.md) | Grzesiek | 4 | P1 |
| 4.3.3 | [Migration system smoke test (1.0 → 1.1 mock schema bump)](phase-4/4.3.3-migration-system-smoke-test-1-0-1-1-mock-schema-bump.md) | Grzesiek | 5 | P0 |
| 4.4.1 | [CHANGELOG.md + LICENSE](phase-4/4.4.1-changelog-md-license.md) | Luq | 2 | P0 |
| 4.4.2 | [THIRD_PARTY_NOTICES.md (TF, DeepFace, OpenCV, ArcFace, FAISS)](phase-4/4.4.2-third-party-notices-md-tf-deepface-opencv-arcface-faiss.md) | Luq | 3 | P0 |
| 4.4.3 | [SECURITY.md (responsible disclosure)](phase-4/4.4.3-security-md-responsible-disclosure.md) | Luq | 1 | P0 |
| 4.5.1 | [Release VM Test [RELEASE GATE]](phase-4/4.5.1-release-vm-test-release-gate.md) | Luq | 4 | P0 |