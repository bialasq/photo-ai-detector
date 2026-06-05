# Photo AI Detector — Production-Ready Roadmap (Master Plan v1.0)

> **Status repozytorium (stan startowy):** Tauri 2 + React + FastAPI + SQLite + DeepFace, NSIS/MSI sidecar, brak CI, brak `pytest`/`vitest`, `/api/dev/*` zawsze włączone, smoke testy tylko w `database.py` / `ai_core.py`.
> **Cel:** v1.0.0 Windows MSI — instalator na czystej VM, offline-only, stabilny na 100k+ twarzy, podpisany binarką, z CI i 80% coverage na krytycznych modułach.
> **Zespół:** **Grzesiek** (Cursor codeviber, backend/automation) + **Luq** (senior fullstack, review/frontend/QA/release).
> **Harmonogram:** 12 tygodni / 200–280 h (47% Cursor, 53% manual — zgodnie z PDF).
> **Źródła:** Photo_AI_Detector_Production_Roadmap.pdf (PDF) + analiza techniczna (Cursor). Każdy task ma adnotację źródła.

---

## Spis treści

1. [Cursor Operating Contract — kontrakt z Cursorem (anty-halucynacja)](#1-cursor-operating-contract)
2. [Team Working Agreement — jak pracujemy razem](#2-team-working-agreement)
3. [Definicja Production-Ready](#3-definicja-production-ready)
4. [Mapa faz i timeline](#4-mapa-faz-i-timeline)
5. [Faza 0 — Scope Freeze (przed startem)](#faza-0--scope-freeze)
6. [Faza 1 — Stability + Security + Data (tyg. 1–3)](#faza-1--stability--security--data-tyg-1-3)
7. [Faza 2 — Performance & Scaling (tyg. 4–6)](#faza-2--performance--scaling-tyg-4-6)
8. [Faza 3 — Testing, Security Audit & CI/CD (tyg. 7–9)](#faza-3--testing-security-audit--cicd-tyg-7-9)
9. [Faza 4 — Deployment, Monitoring & Release (tyg. 10–12)](#faza-4--deployment-monitoring--release-tyg-10-12)
10. [Biblioteka promptów Cursora (per task)](#10-biblioteka-promptów-cursora)
11. [Ryzyka i mitigacje](#11-ryzyka-i-mitigacje)
12. [Cadence: daily / weekly / release](#12-cadence)
13. [Release Gate — checklist v1.0.0](#13-release-gate)

---

## 1. Cursor Operating Contract

> **Zapisz to jako `.cursorrules` w roocie repo.** To jest niepodlegający negocjacjom kontrakt operacyjny dla każdej sesji Cursora w tym projekcie. Wkleić raz, egzekwować zawsze.

### 1.1 Role i mindset

```
ROLA: Jesteś Principal/Senior Software Engineer w stacku, którego dotyczy zadanie:
  - Python: FastAPI 0.115+, Pydantic v2, SQLite, pytest, asyncio, type hints.
  - JS/TS: React 18, TypeScript strict, Vite, Tauri 2 (Rust glue), Vitest.
  - Rust: Tauri 2 commands, sidecar lifecycle, tauri.conf.json.
  - DevOps: GitHub Actions, PyInstaller, NSIS, code signing Authenticode.
  - ML: DeepFace, ArcFace embeddings, DBSCAN, FAISS (przy migracji).

MINDSET: Działasz jak ktoś, kto BĘDZIE utrzymywał ten kod 3 lata.
Każda linia, którą piszesz, musi przejść code review seniora bez komentarzy.
```

### 1.2 Anti-hallucination rules (twarde)

```
1. ZAKAZ ZGADYWANIA API. Jeśli nie jesteś PEWIEN sygnatury funkcji, parametru,
   nazwy modułu lub zachowania biblioteki — ZATRZYMAJ SIĘ i:
   a) odczytaj plik źródłowy z repo PRZED edycją,
   b) zacytuj fragment z dokumentacji (link + cytat), albo
   c) zapytaj człowieka "nie znam X, daj mi snippet z docs / repo".

2. ZAKAZ WYMYŚLANIA ŚCIEŻEK PLIKÓW I IMPORTÓW. Każdy import/ścieżka musi
   pochodzić z faktycznego listingu drzewa repo, który właśnie odczytałeś.

3. ZAKAZ WYMYŚLANIA ZALEŻNOŚCI. Nie dodawaj pakietów do requirements.txt /
   package.json bez:
   - jawnego polecenia człowieka, ALBO
   - uzasadnienia (dlaczego stdlib/istniejąca biblioteka nie wystarczy)
     + linku do PyPI/npm z wersją + rozmiarem.

4. ZAKAZ "PRAWIE PRAWDZIWEGO KODU". Jeśli nie wiesz, jak działa konkretny
   fragment legacy w repo, NIE refaktoryzuj go "obok". Otwórz plik, przeczytaj,
   potem edytuj.

5. ZAKAZ CICHEGO USUWANIA FUNKCJONALNOŚCI. Jakakolwiek zmiana, która usuwa
   istniejące zachowanie (endpoint, parametr, kolumnę DB, filtr UI), musi być
   ZGŁOSZONA jawnie ("usuwam X bo Y, alternatywa: Z") PRZED zmianą.

6. ZAKAZ STUB'ÓW UDAJĄCYCH DZIAŁAJĄCY KOD. Żadnych `# TODO: implement`,
   `pass`, `raise NotImplementedError` w mergowalnym kodzie. Albo robisz całość,
   albo zostaw plik nietknięty i napisz "potrzebuję X żeby skończyć".

7. ZAKAZ "MAGIC NUMBERS" BEZ ŹRÓDŁA. Jeśli ustawiasz `eps=0.4`, `batch=32`,
   `timeout=30` — w komentarzu lub PR opis ŹRÓDŁA tej liczby (benchmark,
   dokumentacja, decyzja architektoniczna). Inaczej: nie wpisuj.

8. CYTAT > PARAFRAZA. Jeśli kopiujesz wzorzec z dokumentacji oficjalnej —
   podaj URL w komentarzu. Jeśli z StackOverflow — przepisz po swojemu i
   zrozum, nie kopiuj 1:1.
```

### 1.3 Workflow (zawsze ta sama sekwencja)

```
DLA KAŻDEGO TASKA:

1. PRZECZYTAJ kontekst PRZED pisaniem:
   - Otwórz pliki, które będziesz modyfikował (pełna treść, nie tylko fragmenty).
   - Otwórz pliki, które importują modyfikowane moduły.
   - Sprawdź czy są testy dotyczące tego obszaru.

2. PRZEDSTAW PLAN przed kodowaniem (3–7 punktów):
   - Co zmieniam, w których plikach, jaki będzie efekt.
   - Co MOŻE się zepsuć (regresje, kompatybilność wsteczna).
   - Jak to przetestuję LOKALNIE przed pushem.

3. CZEKAJ na "go" od człowieka jeśli plan dotyka:
   - schematu DB,
   - publicznego API (endpoints, payload shape),
   - tauri.conf.json / Cargo.toml / NSIS,
   - usuwania czegokolwiek.

4. KODUJ MAŁYMI KROKAMI:
   - Jeden logiczny komit = jedna zmiana.
   - Po każdej zmianie: pokaż diff w odpowiedzi.

5. TESTUJ ZANIM POWIESZ "DONE":
   - Uruchom pytest dla modyfikowanego modułu.
   - Uruchom npm run build jeśli ruszyłeś frontend.
   - Uruchom backend lokalnie i kliknij /health.
   - Wklej output do PR description.

6. UWAGA NA SCOPE CREEP:
   - Jeśli widzisz inny bug / smell w pliku — NOTUJ go w `docs/TECH_DEBT.md`,
     NIE poprawiaj "przy okazji".
```

### 1.4 Code style hard rules

```
PYTHON:
- Type hints WSZĘDZIE w publicznych funkcjach (mypy --strict friendly).
- Docstring (Google style) dla każdej funkcji w main.py, database.py, ai_core.py.
- f-stringi, nie .format() ani %.
- Logger: logging.getLogger(__name__), NIE print().
- Wyjątki: konkretne (FileNotFoundError, sqlite3.IntegrityError) — NIE goły except.
- Path: pathlib.Path, NIE os.path.join.

TYPESCRIPT:
- strict: true w tsconfig — bez wyjątków.
- any zabronione (poza third-party shims z komentarzem // TODO: type).
- React: function components + hooks. Class components zabronione.
- API calls: jeden helper z typami (Zod schema jeśli backend nie ma OpenAPI client).
- Brak console.log w mergowanym kodzie — useDebugValue lub usuń.

RUST (Tauri):
- clippy --all-targets -- -D warnings musi przechodzić.
- Brak unwrap() w produkcyjnych ścieżkach — anyhow::Result.
- Komendy Tauri: walidacja inputów + structured errors.
```

### 1.5 Mandatory pre-PR checklist (Cursor wypisuje na końcu każdej odpowiedzi)

```
[ ] Przeczytałem WSZYSTKIE pliki, które edytowałem (pełną treść).
[ ] Nie dodałem nowych zależności (lub wyjaśniłem dlaczego).
[ ] Wszystkie funkcje publiczne mają type hints / TS types.
[ ] Pytest / vitest dla zmodyfikowanego obszaru przechodzi lokalnie.
[ ] Brak `print`, `console.log`, `unwrap`, `TODO bez issue`.
[ ] Diff dotyka TYLKO zaplanowanych plików.
[ ] Opis PR zawiera: co/dlaczego/jak testowane/ryzyko regresji.
```

---

## 2. Team Working Agreement

### 2.1 Podział pracy — filozofia

| Obszar | Grzesiek (Cursor) | Luq (manual senior) |
|---|---|---|
| **Backend Python (FastAPI, DB)** | Implementacja, refactor, migracje | Code review, decyzje architektoniczne |
| **AI / ML pipeline** | Integracja FAISS, batch processing | Walidacja jakości klastrów, metryki |
| **Frontend React** | Komponenty CRUD, integracja API | Krytyczne UX, wirtualizacja, accessibility |
| **Tauri / Rust glue** | Konfiguracja, sidecar wiring | Audyt bezpieczeństwa IPC |
| **Testy** | Pytest unit (Cursor pisze) | Vitest UI + E2E + integration design |
| **CI/CD** | GitHub Actions YAML | Branch protection, secrets, signing |
| **Security** | Walidacja path traversal, gating dev API | OWASP audit, dependency scan, code review |
| **Docs** | Docstrings, OpenAPI annotations | USER_GUIDE, ARCHITECTURE, TROUBLESHOOTING |
| **Release** | Build sidecar, MSI artifacts | VM test, signing, GitHub Release |

**Zasada:** Cursor pisze 60% kodu pod nadzorem Grześka, **ale każdy PR Grześka idzie do review Luqa.** Luq pisze 100% testów krytycznej ścieżki (są _ground truth_, nie mogą być wygenerowane przez ten sam model, który pisał kod).

### 2.2 Komunikacja

- **Daily async** (Slack/Discord, 5 min): "wczoraj / dziś / blokery".
- **Weekly sync** (poniedziałek, 30 min): demo, retro fazy, priorytetyzacja.
- **PR review SLA:** Luq odpowiada na PR Grześka w ciągu 24 h roboczych.
- **Branch model:** `main` chroniony, feature branche `feat/<task-id>-<slug>`, squash merge.
- **Konwencja commitów:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).

### 2.3 Definition of Done (każdy task)

```
[ ] Kod scalony do main przez PR.
[ ] PR ma minimum 1 approval od drugiej osoby.
[ ] CI zielone (lint + pytest + npm build).
[ ] Acceptance criteria z taska spełnione (i sprawdzone manualnie).
[ ] Dokumentacja zaktualizowana jeśli zmiana publicznego API.
[ ] CHANGELOG.md ma wpis w sekcji [Unreleased].
```

---

## 3. Definicja Production-Ready

| Kryterium | Wartość docelowa | Sposób weryfikacji |
|---|---|---|
| Dystrybucja | Windows MSI z sidecarem, instalacja na czystej VM bez zewnętrznych deps | Test na świeżej Win11 VM |
| Stabilność | 0 unhandled exceptions w skanie 10k zdjęć | Logi po teście stress |
| Skala | 100k+ twarzy w bazie, query galerii < 100 ms p95 | Benchmark + EXPLAIN QUERY PLAN |
| Bezpieczeństwo | API tylko 127.0.0.1, brak `/api/dev/*` w release, brak `/docs` w release | curl test na zbudowanej apce |
| Jakość | ≥ 80% coverage na `database.py`, `main.py`, `ai_core.py` | pytest --cov w CI |
| CI/CD | Każdy PR uruchamia pytest + npm build + lint; release na tag = artefakt MSI | GitHub Actions logs |
| Logging | Strukturalne logi w `%AppData%\com.photo.organizer\logs\` z rotacją | Manualna inspekcja |
| Recovery | Resume skanu po crashu, anulowanie skanu, integralność DB | Test scenariusza |
| Code signing | MSI podpisany Authenticode | Verify-Signature na MSI |
| Dokumentacja | README, USER_GUIDE, ARCHITECTURE, TROUBLESHOOTING, THIRD_PARTY_NOTICES, LICENSE, CHANGELOG, SECURITY | Pliki istnieją + review |

---

## 4. Mapa faz i timeline

```
Tyg.  1   2   3   4   5   6   7   8   9   10  11  12
      ├───Faza 1──┤   ├───Faza 2──┤   ├───Faza 3──┤   ├───Faza 4──┤
      Stability+Sec   Performance     Testing+Sec     Release+Monit
```

| Faza | Tygodnie | Tasków | Godziny | Cursor% | Główne ryzyko |
|---|---|---|---|---|---|
| 0 (Scope) | -1 do 0 | 5 | 8–12 | 10% | Pominięcie security wcześnie (lekcja z PDF: 3.1.x za późno) |
| 1 (Stability+Sec+Data) | 1–3 | 16 | 70–90 | 55% | Migracje DB w polu — backup before migrate |
| 2 (Performance) | 4–6 | 13 | 55–75 | 50% | FAISS migracja: ryzyko regresji klasterów |
| 3 (Testing+CI+Audit) | 7–9 | 14 | 55–75 | 40% | 80% coverage to dużo — nie zostawiać na koniec |
| 4 (Release) | 10–12 | 10 | 45–60 | 50% | Code signing certyfikat (kup wcześniej!) |
| **Razem** | **12** | **58** | **233–312** | **47%** | — |

**Różnica vs PDF:** +6 tasków (Faza 0 nowa + przeniesione security z Fazy 3 do Fazy 1, np. dev API gating, AppData lokalizacja DB, path traversal). +godziny bo PDF nie liczył Fazy 0.

---

## Faza 0 — Scope Freeze

**Cel:** Zamrozić co JEST w v1.0.0 i co NIE jest. Bez tego każda faza spuchnie.

### Tasks

| ID | Task | Owner | H | Prio |
|---|---|---|---|---|
| 0.1 | `docs/PRODUCT_SCOPE.md` — must-have user stories + out-of-scope | Luq | 3 | P0 |
| 0.2 | Zakup certyfikatu code signing (Sectigo / DigiCert OV ~ $200/rok) | Luq | 1 | P0 |
| 0.3 | Setup `.cursorrules` z sekcji 1 tego dokumentu | Grzesiek | 1 | P0 |
| 0.4 | `docs/TECH_DEBT.md` — backlog "nie naprawiać przy okazji" | Grzesiek | 1 | P0 |
| 0.5 | GitHub Issues template + Labels (P0/P1/P2, bug/feat/chore/security) | Grzesiek | 2 | P0 |

**Acceptance gate:** PRODUCT_SCOPE.md zmergowany do main, certyfikat zamówiony (lead time 3–7 dni).

---

## Faza 1 — Stability + Security + Data (tyg. 1–3)

> **Filozofia:** Najpierw nie crashuje. Potem nie wycieka. Potem nie traci danych. Skala dopiero w Fazie 2.

### Tasks

| ID | Task | Owner | H | Prio | Źródło |
|---|---|---|---|---|---|
| 1.1.1 | Try/except w `ai_core.py` — DeepFace + DBSCAN graceful fail | Grzesiek | 8 | P0 | PDF |
| 1.1.2 | Logging: structured JSON, rotating file handler, AppData | Grzesiek | 6 | P0 | PDF + Cursor |
| 1.1.3 | Standard error responses (kody + payload `{error, code, hint}`) | Grzesiek | 4 | P0 | PDF |
| 1.1.4 | React Error Boundaries (App, Gallery, PeopleGrid) | Luq | 5 | P0 | PDF |
| **1.2.1** | **Gate `/api/dev/*` za `PHOTO_ORGANIZER_DEV=1`** | **Grzesiek** | **3** | **P0** | **Cursor (KRYTYCZNE)** |
| **1.2.2** | **Przenieś DB do `%AppData%\com.photo.organizer\organizer.db`** | **Grzesiek** | **4** | **P0** | **Cursor (KRYTYCZNE)** |
| 1.2.3 | Path traversal validation w endpointach skanu | Luq | 4 | P0 | Cursor |
| 1.2.4 | Wyłącz `/docs` i `/redoc` w buildzie release | Grzesiek | 1 | P0 | Cursor |
| 1.2.5 | Bind tylko 127.0.0.1 + assert w starcie | Grzesiek | 1 | P0 | Cursor |
| 1.3.1 | DB indeksy: `faces(person_id)`, `photos(processed, has_faces)` | Luq | 4 | P0 | PDF + Cursor |
| 1.3.2 | Schema versioning: `schema_version` table + numbered migrations | Grzesiek | 8 | P0 | Cursor (vs Alembic — patrz dyskusja niżej) |
| 1.3.3 | Batch inserts (50–100) dla twarzy w transakcji | Grzesiek | 5 | P0 | PDF |
| 1.3.4 | `PRAGMA foreign_keys=ON` + `integrity_check` przy starcie | Luq | 2 | P0 | Cursor |
| 1.4.1 | `/api/v1/` prefix dla nowych endpointów (legacy `/api/` zostaje) | Grzesiek | 4 | P1 | PDF |
| 1.4.2 | Pydantic v2 validators na wszystkich requestach POST | Grzesiek | 6 | P0 | PDF |
| 1.4.3 | Scan cancellation: `POST /api/scan-cancel` + `ScanProgressState.cancelled` | Luq | 6 | P0 | Cursor |

**Dyskusja decyzji architektonicznych (Luq decyduje przed Fazą 1):**

- **Alembic vs własne migracje (1.3.2):** Przy jednym pliku SQLite, jednym schemacie i 1 deweloperze piszącym DB — Alembic to overhead. Rekomendacja: zostać przy `schema_version + numbered migrations` w `database.py`, dodać do tego **dry-run + backup before migrate**. Alembic przejdziemy w v1.2 jak będzie ≥ 5 migracji w polu.
- **`/api/v1/` (1.4.1):** Nowe endpointy POD `/api/v1/`, stare zostają dla kompatybilności galerii. Decyzja co deprecated: po Fazie 3.

**Acceptance gate Fazy 1:**
- [ ] Release build NIE eksponuje `/api/dev/*` ani `/docs`.
- [ ] DB jest w `%AppData%`, nie w katalogu repo.
- [ ] Skan można przerwać przyciskiem w UI; po przerwaniu DB jest spójna.
- [ ] Po crashu sidecara (kill -9) restart wznawia od pierwszego `processed=0`.
- [ ] Manual stress: skan 1000 zdjęć kończy się bez unhandled exception (sprawdź `backend.log`).

---

## Faza 2 — Performance & Scaling (tyg. 4–6)

> **Filozofia:** Najpierw zmierz, potem przyspieszaj. FAISS dopiero po benchmarku DBSCAN.

### Tasks

| ID | Task | Owner | H | Prio | Źródło |
|---|---|---|---|---|---|
| 2.0.1 | **Benchmark baseline:** skan 1000 / 5000 / 10000 zdjęć — czas, RAM, DB size | Luq | 4 | P0 | Cursor (KRYTYCZNE PRZED 2.2.1) |
| 2.0.2 | `docs/BENCHMARKS.md` + skrypt `scripts/benchmark.py` w repo | Grzesiek | 3 | P0 | Cursor |
| 2.1.1 | Batch detection: 32 obrazów na raz w `ai_core.scan_directory` | Grzesiek | 10 | P0 | PDF |
| 2.1.2 | Async I/O dla load obrazów (PIL) + decouple od inference | Grzesiek | 8 | P1 | PDF |
| 2.1.3 | GPU detection (CUDA) za flagą `PHOTO_ORGANIZER_GPU=1` — graceful fallback | Luq | 6 | P1 | PDF |
| 2.1.4 | `/api/v1/progress` — szczegóły fazy (scan/embed/cluster) + ETA | Grzesiek | 7 | P1 | PDF |
| 2.1.5 | Cancellation tokens propagowane do batch loop (już mamy z 1.4.3, tu wzmocnienie) | Grzesiek | 3 | P1 | PDF |
| 2.2.1 | **FAISS integration:** index `IndexFlatIP` (cosine) + persist do `.faiss` w AppData | Grzesiek | 12 | P0 | PDF |
| 2.2.2 | Incremental clustering: nowe twarze → query FAISS top-k → przypisz do klastra LUB nowy | Luq | 8 | P0 | PDF |
| 2.2.3 | Silhouette / Davies-Bouldin score logowane po każdym pełnym re-clusteringu | Luq | 6 | P1 | PDF |
| 2.2.4 | Auto-tune `eps` (grid search na sample 10% przy re-clustering raz w tygodniu) | Grzesiek | 7 | P2 | PDF |
| 2.3.1 | `react-window` w `Gallery` przy > 200 zdjęć | Luq | 8 | P0 | PDF + Cursor |
| 2.3.2 | `IntersectionObserver` lazy load miniaturek | Luq | 6 | P0 | PDF |
| 2.3.3 | Cache miniaturek z LRU eviction (limit 500 MB w AppData) | Grzesiek | 6 | P1 | Cursor |

**Pominięte z PDF z uzasadnieniem:**
- ~~2.3.3 Service Worker offline caching~~ — Tauri ma już sidecar lokalnie na 127.0.0.1, SW to overhead. **Decyzja: skip.**
- ~~2.3.4 Client-side compression~~ — miniatury robi backend, kompresja klienta nie ma sensu. **Decyzja: skip.**

**Acceptance gate Fazy 2:**
- [ ] Skan 10k zdjęć < 60 minut na CPU (i7-class).
- [ ] Galeria 10k zdjęć renderuje się płynnie (60 fps scroll) dzięki wirtualizacji.
- [ ] Identyfikacja klastra dla nowej twarzy: < 50 ms p95 (FAISS query).
- [ ] Silhouette score ≥ 0.4 dla biblioteki 1000+ twarzy w benchmarku.
- [ ] Skan można anulować w < 2s (cancellation token respektowany w batch loop).

---

## Faza 3 — Testing, Security Audit & CI/CD (tyg. 7–9)

> **Filozofia:** Pisz testy do każdej zmiany w Fazie 1 i 2 (nie zostawiamy na koniec) — ta faza to **dopełnienie do 80%** + audyt + CI.

### Tasks

| ID | Task | Owner | H | Prio | Źródło |
|---|---|---|---|---|---|
| 3.1.1 | OWASP ASVS L1 self-audit (checklist) dla API + sidecara | Luq | 8 | P0 | PDF |
| 3.1.2 | ~~Rate limiting~~ → **Replace:** Audyt `127.0.0.1` bind + DNS rebinding mitigation (`Host` header check) | Luq | 4 | P1 | Cursor (lepsze niż PDF dla loopback-only) |
| 3.1.3 | Security headers (CSP w Tauri webview, X-Content-Type-Options) | Grzesiek | 4 | P1 | PDF |
| 3.1.4 | Path validation hardening + symlink rejection w skanie | Luq | 5 | P0 | PDF + Cursor |
| 3.1.5 | `pip-audit` + `npm audit` w CI jako warning (nie failing) | Grzesiek | 4 | P1 | PDF |
| 3.2.1 | **Unit tests Python: cel 80% coverage** na `database.py`, `main.py`, `ai_core.py` | Luq | 18 | P0 | PDF |
| 3.2.2 | Integration tests: FastAPI TestClient dla każdego endpointu | Luq | 10 | P0 | PDF |
| 3.2.3 | E2E tests: Playwright na Tauri webview (smoke: scan → gallery → name cluster) | Luq | 8 | P1 | PDF |
| 3.2.4 | Benchmarks jako pytest-benchmark, fail jeśli regresja > 20% | Grzesiek | 6 | P1 | PDF |
| 3.2.5 | **GitHub Actions `ci.yml`** (pytest + npm build + lint na każdy PR) | Grzesiek | 7 | P0 | PDF |
| 3.2.6 | Vitest dla `FaceCropImage`, `Gallery filters`, `PeopleGrid` | Luq | 8 | P0 | Cursor |
| 3.3.1 | README sekcja "Production install" + "Building from source" | Luq | 4 | P0 | PDF |
| 3.3.2 | `docs/ARCHITECTURE.md` z diagramem (Mermaid) sidecar ↔ webview | Luq | 5 | P0 | PDF |
| 3.3.3 | `docs/USER_GUIDE.md` z screenshotami | Luq | 6 | P0 | PDF |

**Pominięte/zastąpione vs PDF:**
- ~~3.1.2 Rate limiting per-endpoint~~ → Replace 3.1.2 above (DNS rebinding). Loopback nie potrzebuje rate limit per IP.
- ~~3.1.4 File upload validation~~ → Nie mamy uploadu HTTP, tylko skan local path → już w 3.1.4 (path validation).

**Acceptance gate Fazy 3:**
- [ ] `pytest --cov` raportuje ≥ 80% na `database.py` i `ai_core.py`, ≥ 70% na `main.py`.
- [ ] CI uruchamia się na każdy PR, czas < 5 min.
- [ ] OWASP ASVS L1 checklist 100% przejść (Luq sign-off).
- [ ] E2E smoke test zielony w CI nightly (jeśli infrastrukturalnie możliwe — inaczej manual).
- [ ] Wszystkie 4 dokumenty (README, ARCH, USER_GUIDE, OpenAPI) zmergowane.

---

## Faza 4 — Deployment, Monitoring & Release (tyg. 10–12)

> **Filozofia:** Windows MSI v1.0.0. Mac/Linux to v1.1.x. Sentry to opt-in, nie default.

### Tasks

| ID | Task | Owner | H | Prio | Źródło |
|---|---|---|---|---|---|
| 4.1.1 | Tauri updater plugin + GitHub Releases signed feed | Grzesiek | 8 | P1 | PDF |
| 4.1.2 | **Code signing MSI Authenticode** (cert z 0.2) | Luq | 6 | P0 | PDF |
| 4.1.3 | ~~Multi-platform Win/Mac/Linux~~ → **Only Windows x64 dla v1.0.0** | — | 0 | P2 | Decyzja: v1.1 |
| 4.1.4 | Release workflow GitHub Actions: tag `v*` → build → sign → upload MSI | Grzesiek | 5 | P0 | PDF |
| 4.1.5 | Splash screen "Loading AI models..." w Tauri (cold start UX) | Luq | 4 | P0 | Cursor |
| 4.2.1 | Sentry integration, **DEFAULT OFF**, opt-in w Settings | Grzesiek | 6 | P1 | PDF |
| 4.2.2 | `/api/v1/health` rozszerzony: `db_ok`, `model_loaded`, `schema_version`, `disk_free` | Grzesiek | 3 | P0 | Cursor |
| 4.2.3 | Help → "Copy diagnostics" (wersja, OS, DB size, last error) do schowka | Luq | 4 | P0 | Cursor |
| 4.2.4 | ~~Analytics~~ → **Pominięte:** offline-first USP, brak analytics nawet privacy-first | — | 0 | P2 | Decyzja: skip |
| 4.3.1 | Backup feature: `Help → Export library` → ZIP (DB + cache miniaturek) | Luq | 6 | P0 | PDF |
| 4.3.2 | Data retention: cleanup cache miniaturek > 90 dni nieużywanych | Grzesiek | 4 | P1 | PDF |
| 4.3.3 | Migration system smoke test (1.0 → 1.1 mock schema bump) | Grzesiek | 5 | P0 | PDF |
| 4.4.1 | `CHANGELOG.md` (Keep a Changelog) + `LICENSE` (MIT lub proprietary — decyzja) | Luq | 2 | P0 | Cursor |
| 4.4.2 | `THIRD_PARTY_NOTICES.md` (TensorFlow, DeepFace, OpenCV, ArcFace weights) | Luq | 3 | P0 | Cursor |
| 4.4.3 | `SECURITY.md` (responsible disclosure email + GPG key) | Luq | 1 | P0 | Cursor |
| 4.5.1 | **VM test:** czysta Win11 → install MSI → scan 50 zdjęć → uninstall → ślady? | Luq | 4 | P0 | Cursor (RELEASE GATE) |

**Acceptance gate Fazy 4:** patrz [sekcja 13](#13-release-gate).

---

## 10. Biblioteka promptów Cursora

> Każdy prompt = jeden task. **Kopiuj 1:1 do Cursor Composer / Agent mode.** Każdy zaczyna się od `.cursorrules` z sekcji 1 (lub odwołania), żeby reguły były w kontekście.

### 10.0 Prompt-startupowy (zawsze wklejaj jako pierwszy w nowej sesji)

```
Działasz w repo photo-ai-detector (Tauri 2 + FastAPI + React + SQLite + DeepFace,
Windows-first, offline-only desktop app).

Obowiązuje Cię .cursorrules z roota repo — przeczytaj ten plik PIERWSZY ZANIM
zaczniesz cokolwiek innego i potwierdź: "Przeczytałem .cursorrules, zasady X
najważniejsze w tym tasku to: ...".

NIE zaczynaj kodowania zanim:
1. Otworzysz pliki, które będziesz modyfikował (cała treść).
2. Przedstawisz plan w 3-7 punktach.
3. Dostaniesz "go" ode mnie.

Twój task w tej sesji: [WKLEJ_SKRÓT_TASKA]
```

---

### 10.1.1 — `ai_core.py` graceful error handling (Grzesiek, 8 h, P0)

```
ROLA: Senior Python engineer specjalizujący się w pipeline'ach ML.

TASK: Dodaj graceful error handling w ai_core.py.
Każda interakcja z DeepFace (analyze, represent) i sklearn.cluster.DBSCAN
może rzucić: ValueError (model not loaded), MemoryError, cv2.error, 
np.linalg.LinAlgError, FileNotFoundError dla obrazu.

WYMAGANIA:
1. Otwórz ai_core.py i przeczytaj GO CAŁY zanim zaczniesz.
2. Otwórz main.py i znajdź MIEJSCA wywołań z ai_core — sprawdź
   jak propagujemy błędy teraz (HTTPException? log + skip?).
3. Wprowadź klasy wyjątków w ai_core.py:
   - class AICoreError(Exception)
   - class FaceDetectionError(AICoreError)
   - class EmbeddingError(AICoreError)
   - class ClusteringError(AICoreError)
4. Każdą zewnętrzną wywołanie owiń try/except SPECYFICZNYM wyjątkiem
   (NIE goły except) → log z context (path, batch_idx) → re-raise jako AICoreError.
5. W main.py: catch AICoreError → 500 ze standardowym payloadem
   {"error": "...", "code": "ai_core_failure", "hint": "..."}.
6. Pojedyncza twarz, która padnie, NIE wywala całego batcha — log warning,
   ustaw `photo.processed = 1, has_faces = 0` i lećmy dalej.
7. DBSCAN exception → catch w main, NIE crashujemy procesu, ale flagujemy
   `ScanProgressState.last_error`.

NIE WOLNO:
- Dodawać nowych zależności.
- Refaktorować "obok" (struktura modułów zostaje).
- Tłumić błędów cicho — zawsze log z level ERROR + traceback.

TESTUJ:
- pytest tests/test_ai_core_errors.py (napisz 4 testy: każdy typ
  wyjątku → odpowiedni AICoreError z context).
- Manual: skan z 1 zepsutym JPG (0 bajtów) — czy batch leci dalej?

DELIVERABLE:
- Diff ai_core.py, main.py, tests/test_ai_core_errors.py
- Output pytest
- Pre-PR checklist z .cursorrules wypełniony
```

---

### 10.1.2 — Structured logging w `%AppData%` (Grzesiek, 6 h, P0)

```
ROLA: Senior Python engineer (logging, observability).

TASK: Zastąp wszystkie print() w backend/ strukturalnym logging z rotacją plików
w katalogu danych aplikacji (Windows: %AppData%\com.photo.organizer\logs\).

WYMAGANIA:
1. Stwórz logging_config.py:
   - get_log_dir() → pathlib.Path; na Windows %AppData%\com.photo.organizer\logs,
     fallback ~/.photo-organizer/logs (Linux dev).
   - setup_logging(level: str) → konfiguruje root logger:
     * RotatingFileHandler: 5 plików × 10 MB, backend.log.
     * StreamHandler na stderr w dev (env PHOTO_ORGANIZER_DEV=1).
     * Format JSON: {"ts": ISO8601, "level", "logger", "msg", "ctx": {...}}.
     * Użyj python-json-logger TYLKO jeśli już jest w requirements; inaczej
       własny logging.Formatter (preferuj brak nowej zależności).
2. W main.py: setup_logging() jako pierwsza linia funkcji 
   `lifespan` / startup event.
3. Grep `print(` w backend/ — KAŻDY zamień na logger = logging.getLogger(__name__);
   logger.info/warning/error z context dict.
4. `requirements.txt`: jeśli python-json-logger już jest, używamy; jeśli nie —
   piszemy własny Formatter (zero nowych deps).

NIE WOLNO:
- Logować ścieżek użytkownika w plain text na poziomie INFO (privacy) — tylko
  hash/anonim. PII = ERROR z pełną ścieżką dozwolone (debug skanu).
- Logować embeddingów twarzy / nazw osób (PII).

ACCEPTANCE:
- Po `pytest` katalog logs/ powstaje w tmpdir z 1 plikiem JSON.
- `grep -r "print(" ` = 0 wyników.
- Manual: po skanie 100 zdjęć — backend.log ma wpisy {scan.start, batch.done × N, scan.complete}.

DELIVERABLE: diff + opis testów + przykładowe 5 linii logu w PR description.
```

---

### 10.1.4 — React Error Boundaries (Luq, 5 h, P0)

> **Luq — to robisz manualnie, ale możesz użyć Cursora jako pair-programmera. Prompt poniżej jeśli używasz.**

```
ROLA: Senior React engineer.

TASK: Dodaj Error Boundary do src/.
Aktualnie nieobsłużony rzucony exception w komponencie wywala cały tree
do białego ekranu. Chcemy fallback UI + log do backendu (POST /api/v1/log-error).

WYMAGANIA:
1. Otwórz src/App.tsx i src/main.tsx — zobacz strukturę.
2. Stwórz src/components/ErrorBoundary.tsx:
   - Class component (Error Boundaries wymagają class — wiem, że to jedyny wyjątek
     od reguły "tylko function components").
   - Props: { fallback?: ReactNode, scope: string }
   - State: { error: Error | null, errorInfo: ErrorInfo | null }
   - componentDidCatch → POST /api/v1/log-error z { scope, message, stack }.
   - Fallback domyślny: "Something broke in {scope}. Try again or restart the app."
3. Owiń w App.tsx:
   - <ErrorBoundary scope="app"> wrapper najwyższy.
   - <ErrorBoundary scope="gallery"> wokół <Gallery>.
   - <ErrorBoundary scope="people"> wokół <PeopleGrid>.
4. Endpoint backend: POST /api/v1/log-error → loguje na poziomie ERROR
   z prefixem "frontend:" (Grzesiek doda w 1.1.2, na razie stub OK).

ACCEPTANCE:
- Manual test: throw Error("boom") w useEffect <Gallery> → widzisz fallback,
  nie biały ekran, w backend.log jest wpis.
- Vitest test: ErrorBoundary renderuje fallback przy throw w child.

DELIVERABLE: diff + screenshot fallback UI.
```

---

### 10.2.1 — Gating `/api/dev/*` za `PHOTO_ORGANIZER_DEV` (Grzesiek, 3 h, P0 KRYTYCZNE)

```
ROLA: Senior Python/FastAPI engineer (security-conscious).

TASK: Endpointy /api/dev/* (w tym /api/dev/reset-library, /api/dev/seed) MUSZĄ
być NIEDOSTĘPNE w buildzie release. Aktualnie są zawsze włączone.

WYMAGANIA:
1. Otwórz main.py — znajdź gdzie rejestrowane są routy /api/dev/*.
2. Przed rejestracją routera dev sprawdź env:
   ```python
   import os
   DEV_MODE = os.getenv("PHOTO_ORGANIZER_DEV", "0") == "1"
   if DEV_MODE:
       app.include_router(dev_router, prefix="/api/dev")
       logger.warning("DEV endpoints ENABLED — do not use in production")
   ```
3. W src-tauri/src/lib.rs (sidecar spawn): NIE PRZEKAZUJ
   PHOTO_ORGANIZER_DEV=1 do sidecara w buildzie release. Sprawdź czy mamy
   `cfg!(debug_assertions)` i tylko wtedy ustawiamy env.
4. Test E2E (manual na razie): zbuduj release sidecar (PyInstaller),
   uruchom, curl http://127.0.0.1:8000/api/dev/reset-library → 404.

NIE WOLNO:
- Zostawić defaultu DEV_MODE=True. Default to ZAWSZE False.
- Zaufać X-Dev-Mode headerowi czy podobnemu hackowi. ENV ONLY.

ACCEPTANCE:
- pytest: test_dev_endpoints_disabled_by_default (monkeypatch env, build app,
  check 404 na /api/dev/reset-library).
- pytest: test_dev_endpoints_enabled_with_env (set env=1, check 200/expected).
- Manual: release build → curl /api/dev/reset-library → 404.

DELIVERABLE: diff main.py + lib.rs + 2 testy + output curl.
```

---

### 10.2.2 — DB w `%AppData%` (Grzesiek, 4 h, P0 KRYTYCZNE)

```
ROLA: Senior Python + Tauri engineer.

TASK: organizer.db musi w produkcji żyć w
%AppData%\com.photo.organizer\organizer.db (Windows), NIE w katalogu repo
ani CWD sidecara. W dev OK katalog repo dla wygody.

WYMAGANIA:
1. Otwórz database.py — znajdź gdzie definiowana jest ścieżka DB.
2. Wprowadź funkcję get_db_path():
   ```python
   def get_db_path() -> Path:
       override = os.getenv("PHOTO_ORGANIZER_DB_PATH")
       if override:
           return Path(override)
       if sys.platform == "win32":
           base = Path(os.environ["APPDATA"]) / "com.photo.organizer"
       elif sys.platform == "darwin":
           base = Path.home() / "Library" / "Application Support" / "com.photo.organizer"
       else:
           base = Path.home() / ".local" / "share" / "com.photo.organizer"
       base.mkdir(parents=True, exist_ok=True)
       return base / "organizer.db"
   ```
3. W src-tauri/src/lib.rs: przy spawnowaniu sidecara w release ustaw
   PHOTO_ORGANIZER_DB_PATH = tauri::api::path::app_data_dir(...) / "organizer.db".
4. MIGRACJA istniejących userów (jeśli plik organizer.db jest w CWD, a w AppData
   pusto) → opcjonalnie kopia z notyfikacją "Library moved to AppData". Decyzja
   Luqa: czy v1.0.0 ma tę migrację, czy zaczynamy świeżo.
5. .gitignore: dodaj %APPDATA%-niezależnie, ale CWD organizer.db dla pewności.

ACCEPTANCE:
- pytest: test_db_path_windows_uses_appdata (mock platform, env).
- Manual: uruchom Tauri release build, sprawdź że %AppData%\com.photo.organizer\
  ma organizer.db.

DELIVERABLE: diff + decyzja Luqa co do migracji starych userów (komentarz w PR).
```

---

### 10.2.3 — Path traversal validation (Luq, 4 h, P0)

```
ROLA: Senior security engineer.

TASK: Endpointy skanu przyjmują path od użytkownika. Walidacja jest minimalna.
Trzeba zatrzymać: ..\, symlinki out-of-tree, ścieżki nieistniejące, ścieżki
do plików (musi być katalog), ścieżki do roota dysku (np. C:\) z ostrzeżeniem.

WYMAGANIA:
1. Otwórz main.py — znajdź endpointy POST /api/scan-start lub podobne.
2. Stwórz path_validation.py:
   - validate_scan_path(raw: str) -> Path
   - Reject: ".." w segmentach, symlinki rozwiązywane do parenta out-of-disk,
     nieistniejące, nie-katalogi.
   - Warning (nie reject): root dysku (C:\, D:\) — wymagaj nagłówka
     X-Confirm-Whole-Disk: 1 (przekazany z UI po confirmie).
3. Tauri side: dialog file picker MUSI być źródłem path (frontend nie powinien
   wysyłać arbitralnego stringa). Zaktualizuj komponent UI żeby używał
   tauri-plugin-dialog.
4. Test: pytest na każdy reject case + jeden positive.

NIE WOLNO:
- Logować surowej ścieżki w response (tylko hash w logu erroru).

ACCEPTANCE:
- 5 testów (.., symlink, nieistnieje, plik nie katalog, root + missing header).
- Manual: spróbuj POST /api/scan-start z body {"path": "..\\..\\Windows"} → 400.

DELIVERABLE: diff + tabela case'ów testowych w PR.
```

---

### 10.3.1 — DB indeksy (Luq, 4 h, P0)

```
ROLA: Senior DBA / SQLite engineer.

TASK: Dodać indeksy żeby query galerii i identyfikacji klastra były < 100 ms p95
przy 100k twarzy / 50k zdjęć.

WYMAGANIA:
1. Otwórz database.py i znajdź funkcje SELECT.
2. Uruchom (lokalnie, lub w teście) na bazie z fixturą 10k twarzy:
   EXPLAIN QUERY PLAN dla każdego SELECT — zidentyfikuj SCAN TABLE.
3. Dodaj migrację numbered: migrations/003_indexes.sql:
   ```sql
   CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces(person_id);
   CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces(photo_id);
   CREATE INDEX IF NOT EXISTS idx_photos_processed ON photos(processed, has_faces);
   CREATE INDEX IF NOT EXISTS idx_photos_path ON photos(path);
   ```
   (Dokładny zestaw zależy od kolumn — zweryfikuj schemat PRZED pisaniem.)
4. Re-run EXPLAIN — wszystkie SELECTy galerii MUSZĄ używać USE INDEX,
   nie SCAN TABLE.

NIE WOLNO:
- Indeksować wszystkiego "na zapas" — każdy indeks = wolniejsze INSERTy.
  Tylko indeksy uzasadnione EXPLAIN.

ACCEPTANCE:
- pytest tests/perf/test_query_plans.py: assert "SCAN" not in plan
  dla 5 krytycznych queries.
- Benchmark: get_all_photos(faceless_only=True) na 50k zdjęć < 100 ms.

DELIVERABLE: migracja + tabela query → plan przed/po w PR description.
```

---

### 10.3.2 — Schema versioning + numbered migrations (Grzesiek, 8 h, P0)

```
ROLA: Senior Python/SQLite engineer.

TASK: Wprowadź wersjonowanie schematu DB i system numbered migrations.
NIE używamy Alembic (decyzja Luqa — zbyt ciężki dla jednego pliku SQLite).

WYMAGANIA:
1. Otwórz database.py — przeczytaj _apply_migrations() i CREATE TABLE.
2. Stwórz strukturę:
   migrations/
     001_initial.sql      (CREATE TABLE zaczerpnięte z obecnego schema)
     002_add_has_faces.sql
     003_indexes.sql      (z 10.3.1)
3. Tabela schema_migrations(version INTEGER PRIMARY KEY, applied_at TIMESTAMP).
4. Funkcja apply_pending_migrations(conn):
   - SELECT MAX(version) FROM schema_migrations.
   - Lista plików migrations/*.sql posortowana.
   - Dla każdej > current: BEGIN; exec sql; INSERT version; COMMIT.
   - Failure → ROLLBACK + raise.
5. Dry-run flag: PHOTO_ORGANIZER_MIGRATE_DRY_RUN=1 → print planu, nie wykonuj.
6. **Backup before migrate:** PRZED apply_pending_migrations kopia
   organizer.db → organizer.db.bak.{version}. Jeśli migracja failuje — restore.

NIE WOLNO:
- ALTER TABLE w trybie autocommit (musi być w transakcji).
- Migracja, która usuwa kolumnę bez backup.

ACCEPTANCE:
- pytest: test_migration_001_creates_schema (fresh DB → apply → 4 tables).
- pytest: test_migration_dry_run (DB unchanged, plan w stdout).
- pytest: test_migration_failure_rollback (zła sql w 003 → revert do 002 + bak istnieje).

DELIVERABLE: cały folder migrations/ + database.py diff + 3 testy.
```

---

### 10.4.3 — Scan cancellation (Luq, 6 h, P0)

```
ROLA: Senior Python (asyncio) + React engineer.

TASK: Aktualnie skan jest non-cancellable. Dodaj POST /api/v1/scan-cancel
+ przycisk "Stop" w UI.

WYMAGANIA:
1. Backend main.py:
   - W ScanProgressState (już istnieje) dodaj pole cancelled: bool = False.
   - POST /api/v1/scan-cancel ustawia cancelled = True.
   - Pętla skanu (w ai_core.scan_directory lub main.scan_task) sprawdza flagę
     między batchami: if state.cancelled: break → finalize partial.
   - Po cancel: photos już sprocesowane zostają, reszta processed=0.
2. Frontend:
   - W <Gallery> lub <ScanProgress> przycisk "Stop scan" widoczny tylko gdy
     scanning=True.
   - onClick → confirm dialog → POST /api/v1/scan-cancel → polling /api/v1/progress
     czeka na state="cancelled".
3. Cancellation w batch loop (jeśli DeepFace nie wspiera cancel mid-call):
   - Nie chcemy zabijać wątku w środku batch. Cancellation tylko między batchami.
   - To znaczy: max latency cancellation = czas 1 batcha (32 obrazy ~ 5–10 s).

ACCEPTANCE:
- pytest: test_scan_cancel_mid_scan (start scan task, cancel po 2 batchach,
  assert progress.cancelled, assert processed count > 0 i < total).
- Vitest: test_stop_button_calls_api.
- Manual: skan 1000 zdjęć, cancel po 5 s, restart aplikacji → resume działa.

DELIVERABLE: diff main.py + ai_core.py + frontend ScanProgress + 2 testy.
```

---

### 10.2.1 (Faza 2) — Batch detection 32 obrazów (Grzesiek, 10 h, P0)

```
ROLA: Senior ML engineer (DeepFace, TensorFlow).

TASK: Aktualnie DeepFace.analyze() jest wołane per-obraz. To wąskie gardło.
Refactor na batch processing 32 obrazów na raz.

WYMAGANIA:
1. Otwórz ai_core.py — funkcja, która obecnie skanuje pojedynczy obraz.
2. ZWERYFIKUJ czy DeepFace.represent / analyze wspiera batch input. Sprawdź
   dokumentację (deepface/DeepFace.py na GitHub). Jeśli NIE — czy backend
   (TensorFlow / OpenCV) potrafi batch przez bezpośredni model.predict()?
   - Jeśli ZWERYFIKUJESZ że to nie jest możliwe natywnie — udokumentuj w komentarzu
     i zrób concurrent.futures.ThreadPoolExecutor(max_workers=4) jako fallback
     (BO TF nie lubi processpoola w PyInstaller).
3. Nowa funkcja: detect_faces_batch(image_paths: list[Path]) → list[FaceResult].
4. Pamięć: 32 obrazy × ~1080p × 3 channels = ~200 MB peak. ZWERYFIKUJ
   przed deployem na CPU z 4 GB RAM.
5. Jeśli OOM: implementuj retry z batch_size //= 2.

NIE WOLNO:
- Wymyślać API DeepFace, którego nie ma (jeśli batch_size param nie istnieje
  w wersji z requirements.txt — NIE UDAWAJ że istnieje, zrób fallback).

ACCEPTANCE:
- Benchmark: scan 1000 zdjęć przed i po (zapisz do docs/BENCHMARKS.md).
  Cel: ≥ 2× speedup.
- pytest: test_batch_detection_returns_correct_count (32 obrazów → 32 wyników).
- Manual: monitoruj RAM przez task manager — peak < 1.5 GB.

DELIVERABLE: diff ai_core.py + benchmark before/after + decyzja batch lub
thread pool z uzasadnieniem.
```

---

### 10.2.2 (Faza 2) — FAISS integration (Grzesiek, 12 h, P0)

```
ROLA: Senior ML engineer (FAISS, vector search).

TASK: Zastąp obecne wyszukiwanie podobnych twarzy (linear cosine w SQLite)
przez FAISS IndexFlatIP. Indeks persisted w AppData.

WYMAGANIA:
1. Dodaj faiss-cpu>=1.7.4 do requirements.txt (PyInstaller-kompatybilne;
   GPU version NIE — większy paczek + CUDA hassle).
2. Stwórz vector_store.py:
   - class VectorStore:
     - __init__(dim: int = 512): index = faiss.IndexFlatIP(dim).
     - add(face_id: int, embedding: np.ndarray): normalizuj (L2),
       index.add(emb.reshape(1, -1)), store mapping (faiss_idx → face_id).
     - search(emb: np.ndarray, k: int = 10): index.search → list[(face_id, similarity)].
     - save(path: Path): faiss.write_index + pickle id_map.
     - load(path: Path): faiss.read_index + load id_map.
3. W ai_core.py: po każdym embed → vector_store.add(). Po skanie → vector_store.save().
4. W main.py startup: vector_store.load() jeśli plik istnieje.
5. NIE USUWAMY jeszcze DBSCAN — FAISS to dla SZYBKIEJ identyfikacji nowej twarzy
   ("do której osoby należy ta nowa?"). DBSCAN dalej dla full re-clustering.
6. Integracja: gdy user nazwie klaster, nowa twarz przychodzi → 
   vector_store.search(emb, k=5) → jeśli top1 similarity > 0.7 i należy do
   nazwanego klastra → przypisz auto.

NIE WOLNO:
- Wymyślać API FAISS, którego nie ma — sprawdź faiss docs (github.com/facebookresearch/faiss/wiki).
- Trzymać index w RAM bez persistence — restart aplikacji = re-build z DB
  (długie).

ACCEPTANCE:
- pytest: test_vector_store_add_search (10 embeddings, search → top1 to ten sam).
- pytest: test_vector_store_persistence (save + load + search → te same wyniki).
- Benchmark: query top-10 z bazy 100k embeddings < 50 ms.
- Manual: skan + nazwij osobę → dodaj nowe zdjęcia tej osoby → sprawdź auto-assign.

DELIVERABLE: vector_store.py + diff ai_core.py + 3 testy + benchmark.
```

---

### 10.3.5 — GitHub Actions ci.yml (Grzesiek, 7 h, P0)

```
ROLA: Senior DevOps / CI engineer.

TASK: Stwórz .github/workflows/ci.yml uruchamiany na push i pull_request do main.

WYMAGANIA:
1. Job "backend":
   - runs-on: ubuntu-latest (szybsze niż windows runner; testy backend są platform-independent).
   - python 3.12 (lock — TensorFlow ma problem z 3.14, było w README).
   - actions/cache pip cache.
   - pip install -r requirements.txt --no-deps najpierw (faster), potem -r dla brakujących.
   - pip install -r requirements-dev.txt (pytest, pytest-cov, ruff).
   - ruff check main.py ai_core.py database.py (jeśli pyproject.toml z konfigiem ruff jest — inaczej skip).
   - pytest --cov=ai_core --cov=main --cov=database --cov-report=xml --cov-fail-under=70 (na razie 70, w Fazie 3 podbijemy do 80).
   - actions/upload-artifact coverage.xml.
2. Job "frontend":
   - runs-on: ubuntu-latest.
   - node 20 + actions/cache npm.
   - npm ci.
   - npm run lint (jeśli skrypt istnieje).
   - npm run build.
   - npm test (vitest) jeśli skrypt istnieje.
3. Job "rust":
   - runs-on: windows-latest (Tauri build wymaga Windows toolchain dla MSI).
   - actions-rs/toolchain stable.
   - cargo clippy --manifest-path src-tauri/Cargo.toml -- -D warnings.
   - NIE pełny tauri build w CI per-PR (zbyt długie ~15 min) — tylko clippy.
4. Job "release" (osobny plik release.yml uruchamiany na tag v*):
   - npm run sidecar:package.
   - npm run tauri:build.
   - actions/upload-artifact MSI.
   - (signing dopiero w 4.1.4 z certyfikatem).

NIE WOLNO:
- Hardcode'ować secrets w yaml.
- Zapomnieć o concurrency: group: ci-${{github.ref}}, cancel-in-progress: true.

ACCEPTANCE:
- Otwórz testowy PR → CI uruchamia się → wszystkie joby zielone.
- Czas total < 5 min.
- Branch protection rule na main: wymagany CI green (Luq konfiguruje w GitHub
  settings).

DELIVERABLE: .github/workflows/ci.yml + .github/workflows/release.yml + dokument
"jak Luq ustawi branch protection" w docs/CI_SETUP.md.
```

---

### 10.4.1 — Tauri updater + signing feed (Grzesiek, 8 h, P1)

```
ROLA: Senior Tauri + DevOps engineer.

TASK: Wdrożyć Tauri updater plugin — apka sprawdza GitHub Releases co X dni
i proponuje update. Wymaga signed update feed.

WYMAGANIA:
1. Przeczytaj https://tauri.app/v2/guides/distribution/updater
2. tauri.conf.json: enable updater, endpoint = "https://github.com/bialasqq/photo-ai-detector/releases/latest/download/latest.json".
3. Generuj klucz updatera (tauri signer generate). PUBKEY w tauri.conf.json,
   PRIVKEY w GitHub Secrets (TAURI_PRIVATE_KEY).
4. release.yml: po build MSI → tauri signer sign + generate latest.json z URL
   do MSI i podpisem.
5. UI: Settings → "Check for updates" + auto-check w tle co 7 dni (opt-out
   w settings).
6. NIE auto-download/install bez user consent — tylko notyfikacja.

NIE WOLNO:
- Commitować PRIVKEY do repo (nawet zaszyfrowanego).
- Auto-update bez podpisu (Tauri to wymusza, ale podwójnie sprawdź).

ACCEPTANCE:
- v0.9.0 instalujesz → v1.0.0 publikujesz tag → apka w settings pokazuje
  "Update available".
- Manual: zatwierdzasz → MSI pobrany, instalator uruchomiony.

DELIVERABLE: diff tauri.conf.json + release.yml + dokumentacja w docs/RELEASE.md
jak Luq generuje klucz.
```

---

### 10.4.5.1 — Release VM Test (Luq, 4 h, P0 RELEASE GATE)

```
ROLA: Senior QA engineer (manual, ale można Cursor jako pomocnika do checklisty).

TASK: Finalny release gate. Czysta Win11 VM. Pełen flow od instalacji do uninstall.

CHECKLIST (zapisz wyniki w docs/RELEASE_QA_v1.0.0.md):

PRE-INSTALL:
[ ] Czysta Win11 22H2+ VM (Hyper-V / VirtualBox), nigdy nie miała apki.
[ ] WebView2 NIE preinstalowane (test bootstrap).
[ ] Defender włączony.
[ ] Bez admin rights (user account).

INSTALL:
[ ] Pobierz MSI z GitHub Release v1.0.0.
[ ] SHA256 zgadza się z release notes.
[ ] Authenticode signature OK (sigcheck.exe lub Properties → Digital Signatures).
[ ] MSI install bez UAC prompt unormal (jeden uprawnienie OK, ale nie 5).
[ ] Defender NIE flaguje jako PUA / Trojan.
[ ] Start Menu shortcut powstał.
[ ] Apka startuje w < 10 s (cold start, modele ładują się — splash).

SCAN:
[ ] Folder z 50 testowymi zdjęciami (mix: portraits, groups, landscapes, broken JPG).
[ ] Scan completes, progress bar nie zawiesza się.
[ ] Stop scan działa w trakcie.
[ ] Po stop: galeria pokazuje partial results.
[ ] Resume scan kończy resztę.

GALLERY:
[ ] Miniatury renderują się.
[ ] Filtr "faceless" działa.
[ ] Nazwij osobę → re-cluster → osoba persist po restart apki.

CRASH / RECOVERY:
[ ] Kill sidecara w trakcie skanu (Task Manager) → restart apki → wznawia.
[ ] %AppData%\com.photo.organizer\logs\backend.log ma sensowne wpisy.
[ ] DB integrity OK po restart (SELECT count(*) FROM photos zgadza się).

SECURITY:
[ ] curl http://127.0.0.1:8000/api/dev/reset-library → 404.
[ ] curl http://127.0.0.1:8000/docs → 404.
[ ] curl http://192.168.x.x:8000/health (z innej maszyny w LAN) → connection refused.
[ ] Skan path "..\\..\\Windows\\System32" → 400 Bad Request.

UNINSTALL:
[ ] Programy i funkcje → Photo AI Detector → Uninstall.
[ ] Apka zniknęła ze Start Menu.
[ ] Decyzja v1: czy %AppData%\com.photo.organizer\ jest czyszczone czy zostaje
    (PRODUCT_SCOPE.md decyzja: ZOSTAJE z notyfikacją w uninstaller).

DELIVERABLE: docs/RELEASE_QA_v1.0.0.md wypełnione, screenshot każdego ✓.
Jakikolwiek FAIL → blokada release, ticket P0.
```

---

> **Pozostałe taski mają prompty w tym samym formacie. Cursor sam wygeneruje je z TASK_ID przy pierwszym zwołaniu — pattern jest spójny:**
> 1. Rola (Senior X engineer)
> 2. Task (zwięzły opis)
> 3. Wymagania numerowane
> 4. NIE WOLNO (anty-halucynacja)
> 5. Acceptance (pytest + manual)
> 6. Deliverable

---

## 11. Ryzyka i mitigacje

| Ryzyko | Wpływ | Prawdo­podob. | Mitigacja | Owner |
|---|---|---|---|---|
| Certyfikat code signing zamówiony za późno | Release v1.0.0 opóźniony 1–4 tyg. | High | **Zamów w tygodniu 0** (task 0.2) | Luq |
| FAISS migracja psuje istniejące klastry | Regresja jakości, frustracja userów | Med | Benchmark 2.0.1 PRZED 2.2.1 + rollback plan (feature flag) | Grzesiek |
| TensorFlow 2.x x Python 3.14 build się sypie | Builds blocked | Med | Lock 3.12 w CI (.python-version + matrix) | Grzesiek |
| AV false positive na MSI (PyInstaller) | Userzy nie instalują | High | 1) code signing 2) submit to MS Defender + VirusTotal whitelist 3) avoid UPX | Luq |
| Cursor halucynuje przy refactorze DB | Korupcja danych w polu | Med | `.cursorrules` + backup-before-migrate (10.3.2) + Luq review | Obaj |
| Rozmiar MSI > 1 GB (TF + DeepFace) | Słaba adopcja | High | NSIS LZMA + dokumentuj "2 GB free disk" w wymaganiach | Grzesiek |
| Luq overloaded testami w Fazie 3 | Slip 80% coverage | Med | Testy piszemy NA BIEŻĄCO w Fazach 1–2 (każdy PR = test), Faza 3 to dopełnienie | Obaj |
| Path traversal jednak przecieka | Privilege escalation | Low | Tauri dialog jako jedyne źródło path + walidacja backendowa (defense-in-depth) | Luq |
| DBSCAN nie skaluje > 50k twarzy | Klasteryzacja zamarza | High | FAISS-first identification (Faza 2), DBSCAN tylko incremental | Grzesiek |
| Zapomnienie o LICENSE / NOTICES | Compliance fail | Med | Faza 4 task 4.4.x w release gate | Luq |

---

## 12. Cadence

### Daily (5 min, async w kanale)
```
Wczoraj: [task IDs done / WIP]
Dziś: [task IDs planned]
Blokery: [None | opisz]
PR do review: [linki]
```

### Weekly sync (poniedziałek 9:00, 30 min)
```
1. Demo postępu (15 min) — pokaż apkę po zmianach
2. Retro tygodnia (5 min) — co poszło dobrze/źle
3. Plan tygodnia (5 min) — które task IDs z roadmapy
4. Risk review (5 min) — zmienia się prawdopodobieństwo czegoś?
```

### End-of-phase gate (po fazie 1, 2, 3, 4)
```
1. Wszystkie task ID z fazy: status (done / dropped + why)
2. Acceptance gate fazy: pass / fail
3. Coverage report (od Fazy 1)
4. Benchmark vs baseline (od Fazy 2)
5. Decyzja: lock fazę i lecimy dalej / dodatkowy tydzień bufora
```

### Release cadence (Faza 4)
```
v1.0.0-rc1 → VM test → fix → v1.0.0-rc2 → VM test → … → v1.0.0
Max 3 RC. Po 3 RC bez green: post-mortem, decyzja czy scope-cut.
```

---

## 13. Release Gate

**v1.0.0 może wyjść TYLKO jeśli wszystkie poniższe są ✓:**

### Kod i CI
- [ ] CI green na main (ostatni commit).
- [ ] Coverage: `database.py` ≥ 80%, `ai_core.py` ≥ 80%, `main.py` ≥ 70%.
- [ ] 0 `unwrap()` / `TODO bez issue` / `print(` w main.py, ai_core.py, database.py i `src-tauri/src/`.
- [ ] Pytest, vitest, clippy: wszystkie zielone.
- [ ] OWASP ASVS L1 checklist (3.1.1): 100% pass.

### Bezpieczeństwo i dane
- [ ] Release build: curl `/api/dev/*` → 404. Sprawdzone na MSI install.
- [ ] Release build: curl `/docs` → 404.
- [ ] Bind: `netstat -an | findstr :8000` pokazuje TYLKO 127.0.0.1.
- [ ] DB w `%AppData%\com.photo.organizer\`. Nie w katalogu instalacji.
- [ ] Path traversal: 5 test cases pass.

### Build i instalacja
- [ ] MSI podpisany Authenticode (verify-signature OK).
- [ ] VM test pass (docs/RELEASE_QA_v1.0.0.md w pełni ✓).
- [ ] Splash screen przy cold start.
- [ ] Uninstall działa, decyzja o AppData zgodna z PRODUCT_SCOPE.md.

### Dokumentacja
- [ ] README z sekcją Production Install.
- [ ] CHANGELOG.md z wpisem v1.0.0 (kopia z release notes).
- [ ] LICENSE w roocie.
- [ ] THIRD_PARTY_NOTICES.md kompletne (TF, DeepFace, OpenCV, ArcFace, FAISS).
- [ ] SECURITY.md z disclosure email.
- [ ] docs/USER_GUIDE.md z screenshotami.
- [ ] docs/ARCHITECTURE.md z Mermaid diagram.

### Wydajność (benchmark z 2.0.1 / 3.2.4)
- [ ] Scan 10k zdjęć < 60 min na CPU i7 8th gen.
- [ ] Galeria 10k zdjęć: scroll 60 fps p95.
- [ ] FAISS top-10 query: < 50 ms p95 na 100k embeddings.
- [ ] Memory peak skan < 1.5 GB.

### Release ops
- [ ] GitHub Release v1.0.0 utworzony z MSI + SHA256 + release notes.
- [ ] Latest.json updater feed wgenerowany i podpisany.
- [ ] Smoke test updatera: v0.9.x → v1.0.0 prompt pokazuje się.

---

## Podsumowanie planu (TL;DR)

1. **Tydzień 0:** Scope freeze, certyfikat code signing zamówiony, `.cursorrules` ustawione.
2. **Tygodnie 1–3 (Faza 1):** Najpierw nie crashuje + nie wycieka + dane w AppData. Security WCZEŚNIE, nie w tygodniu 7 jak w PDF.
3. **Tygodnie 4–6 (Faza 2):** Benchmark → batch detection → FAISS → react-window. Skala dopiero TERAZ.
4. **Tygodnie 7–9 (Faza 3):** Dopełnienie testów do 80% (piszemy je na bieżąco od Fazy 1!) + OWASP audit + CI/CD obowiązkowe.
5. **Tygodnie 10–12 (Faza 4):** MSI signed, Tauri updater, Sentry opt-in, VM test, release v1.0.0.
6. **v1.1+:** macOS, Linux, GPU, analytics privacy-first, FAISS replace DBSCAN całkowicie.

**Twoje pierwsze 5 kroków „od jutra":**
1. Wklej sekcję 1 (`.cursorrules`) do roota repo.
2. Zamów certyfikat code signing (Luq, lead time!).
3. Otwórz GitHub Issues, stwórz 58 ticketów z tabel powyżej (task ID = issue tytuł).
4. Branch protection na main (Luq, GitHub settings).
5. PR #1: task 1.2.1 (gating `/api/dev/*`) — najmniejszy, najważniejszy. Trening procesu.

---

*Wygenerowano: 2026-05-27. Wersja roadmapy: 1.0. Następna rewizja: po Fazie 1 (tydzień 3).*
