# Audit Log — backward review of solo commits

> Przegląd commitów wykonanych bez review (Luq niedostępny). Druga para oczu
> pod nieobecność recenzenta. Każdy commit czytany linijka po linijce pod kątem
> błędów, których zielone testy nie wyłapią (styki integracyjne, semantyka, security).

## 2026-06 — Faza 1 krytyczne commity (security + dane)

| Commit | Task | Werdykt | Uwagi |
|---|---|---|---|
| efd992e | 1.2.1 dev API gating | ✅ CLEAN | default FALSE, gating przy rejestracji routera, Rust `#[cfg(debug_assertions)]` compile-time, test usuwa env (testuje produkcję) |
| 487b4db | 1.2.2 AppData path | ✅ CLEAN | copy2 non-destructive, kopiuje WAL/SHM, idempotentne, no-overwrite; Rust `app_data_dir()` spójny z Python `get_db_path()` |
| 42e477c | 1.2.4/1.2.5 docs + loopback | ✅ CLEAN | bind fail-closed (`assert_loopback_bind_host`), docs+redoc+openapi=None w release, test integracyjny z prawdziwym netstat |
| 02648b7 | 1.3.2/1.3.1 migracje + indeksy | ✅ CLEAN | transakcja+rollback+restore, `wal_checkpoint(FULL)` przed backup, indeksy EXPLAIN-verified |

**Wniosek:** 4/4 czyste. W każdym znaleziono elementy ponad wymagania promptu
(`.strip()`, WAL/SHM copy, netstat test, wal_checkpoint). Fundament Fazy 1
potwierdzony — Faza 2 może budować na tym bezpiecznie.

## TECH_DEBT zidentyfikowany podczas audytu

| # | Plik / obszar | Problem | Priorytet |
|---|---|---|---|
| TD-1 | `database.get_app_data_dir` | Twardy `RuntimeError` gdy brak `APPDATA` i brak `PHOTO_ORGANIZER_DB_PATH`. Nieosiągalne w prod (Tauri zawsze wstrzykuje ścieżkę), ale brak graceful fallback. | low |
| TD-2 | packaging (task 4.x) | NIE pakować `docs/api/openapi.json` do MSI — zawiera pełen schemat API włącznie z `/api/dev/*`. | med (przed release) |
| TD-3 | `database.create_tables` legacy bootstrap | Podwójne źródło prawdy o schemacie: legacy `_apply_migrations` (ALTER) vs pliki `001/002.sql`. Mogą się rozsynchronizować przy przyszłej zmianie schematu. | med |
| TD-4 | `tests/integration/test_dev_endpoints.py` | `reset-library` test kasuje DB — brak asercji że `db_path` zawiera `tmp` (gdyby ktoś usunął fixturę, kasowałby prawdziwą bazę). | low |
| TD-5 | ai_core RetinaFace detector | ~~RetinaFace nie buduje się na TF 2.21/Keras 3~~ **RESOLVED** (commit na fix/td-5): TF_USE_LEGACY_KERAS=1 via keras_legacy_env.py (main.py, ai_core.py, benchmark.py) + lib.rs + run_app.bat. tf-keras w requirements.txt. Zweryfikowane: live-ai detector_backend=retinaface; regresja =0 → opencv. | ~~HIGH~~ DONE |
## NIE audytowane (zrobione solo, ale nie-krytyczne — do przeglądu gdy Luq wróci)

1.1.1 error handling, 1.1.2 logging, 1.1.3 ErrorResponse, 1.3.3 batch inserts,
oraz cała niezacommitowana warstwa Fazy 2 (1.1.4, 1.2.3, 1.4.3, 2.1.x, 2.2.x, 2.3.x).