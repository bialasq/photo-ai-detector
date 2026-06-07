# Architecture — Photo AI Detector

> **Status:** target architecture po Fazie 2 (z FAISS). Sekcje oznaczone `[Faza 2]` opisują stan po wdrożeniu vector store; przed tym embeddingi są przeszukiwane liniowo w SQLite.
> **Stack:** Tauri 2 (Rust shell) + React/TypeScript (webview) + FastAPI sidecar (Python) + SQLite + FAISS + DeepFace/TensorFlow.
> **Model wdrożenia:** offline-first desktop app, Windows 10/11 x64. Brak chmury, brak telemetrii (poza opt-in Sentry).

---

## 1. Component diagram (wysokopoziomowy)

```mermaid
graph TB
    subgraph Desktop["🖥️ Windows Desktop (jeden proces użytkownika)"]
        subgraph Tauri["Tauri 2 Shell (Rust)"]
            RustCore["Rust Core<br/>lib.rs<br/>• sidecar lifecycle<br/>• env injection (DB_PATH, DEV)<br/>• file dialogs (path picker)<br/>• window mgmt"]
            WebView["WebView2<br/>(React UI)"]
        end

        subgraph Sidecar["Python Sidecar (FastAPI, PyInstaller bundle)"]
            API["FastAPI<br/>127.0.0.1:8000<br/>• LoopbackHostMiddleware<br/>• SecurityHeadersMiddleware<br/>• /api/* + /api/v1/*<br/>• /api/dev/* (DEV only)<br/>• Pydantic validation"]
            AICore["ai_core.py<br/>• batch detection<br/>• embeddings<br/>• clustering"]
            ThumbnailEngine["main.py::ThumbnailEngine<br/>• JPEG cache<br/>• LRU 500 MB w AppData"]
            VectorStore["vector_store.py<br/>FAISS IndexFlatIP<br/>[Faza 2]"]
            DBLayer["database.py<br/>• migrations<br/>• CRUD<br/>• connection mgmt"]
            LogPrivacy["log_privacy.py<br/>hash_path_for_log()"]
        end

        subgraph Models["AI Models (bundled)"]
            DeepFace["DeepFace<br/>+ TensorFlow"]
            ArcFace["ArcFace weights<br/>(embeddings 512-dim)"]
        end

        subgraph Storage["%AppData%\\com.photo.organizer\\"]
            SQLite[("organizer.db<br/>SQLite (WAL)")]
            FAISSFile["faiss.index<br/>+ .ids.pkl<br/>[Faza 2]"]
            Thumbs["thumbnails/<br/>(LRU cache 500MB)"]
            Logs["logs/<br/>backend.log<br/>(rotating 5×10MB)"]
        end

        UserPhotos[("📁 User photo folder<br/>(read-only scan)")]
    end

    WebView -->|"HTTP fetch<br/>127.0.0.1:8000/api/v1"| API
    RustCore -->|"spawn + env"| Sidecar
    RustCore -.->|"selected path"| WebView
    WebView -.->|"open dialog request"| RustCore

    API --> AICore
    API --> DBLayer
    API --> ThumbnailEngine
    ThumbnailEngine --> Thumbs
    AICore --> DeepFace
    DeepFace --> ArcFace
    AICore --> VectorStore
    AICore -->|"read images"| UserPhotos
    AICore --> DBLayer

    DBLayer --> SQLite
    VectorStore --> FAISSFile
    Sidecar --> Logs
    API -.-> LogPrivacy

    classDef rust fill:#dea584,stroke:#8b4513,color:#000
    classDef python fill:#3776ab,stroke:#1d4e6f,color:#fff
    classDef storage fill:#f9d71c,stroke:#b8860b,color:#000
    classDef model fill:#a370ff,stroke:#5319e7,color:#fff
    class RustCore,WebView rust
    class API,AICore,VectorStore,DBLayer,ThumbnailEngine,LogPrivacy python
    class SQLite,FAISSFile,Thumbs,Logs storage
    class DeepFace,ArcFace model
```

**Kluczowa zasada bezpieczeństwa:** sidecar nasłuchuje wyłącznie na `127.0.0.1` (loopback). WebView łączy się przez HTTP do `127.0.0.1:8000`. Żaden ruch nie opuszcza maszyny. `/api/dev/*` rejestrowane tylko gdy `PHOTO_ORGANIZER_DEV=1` (nigdy w release).

---

## 2. Scan flow (sequence diagram)

```mermaid
sequenceDiagram
    actor User
    participant UI as React UI
    participant Rust as Tauri Rust Core
    participant API as FastAPI
    participant AI as ai_core
    participant DF as DeepFace/TF
    participant VS as VectorStore (FAISS)
    participant DB as SQLite

    User->>UI: Click "Scan folder"
    UI->>Rust: open directory dialog
    Rust-->>UI: selected path
    UI->>API: POST /api/scan-folder {folder_path}
    API->>API: validate_scan_path() (traversal guard)
    API->>AI: _execute_folder_scan_async() [async task]
    API-->>UI: 200 {status: started, total_files}

    Note over AI,DB: Faza 1: scanning
    loop Każdy batch (32 obrazów)
        AI->>AI: check state.cancelled
        AI->>DF: detect_faces_batch(images)
        DF-->>AI: faces + bounding boxes
        AI->>DF: represent(faces) → embeddings
        DF-->>AI: 512-dim vectors
        AI->>VS: add(face_id, embedding)
        AI->>DB: insert_faces_batch(50-100, txn)
        AI->>DB: mark_photo_processed
        AI-->>API: update ScanProgressState (phase, ETA)
    end

    Note over AI,VS: Faza 2: clustering
    AI->>AI: incremental_assign() lub full DBSCAN
    AI->>DB: update cluster_id per face
    AI->>VS: save(faiss.index)
    AI->>DB: persist cluster_health (silhouette)
    AI-->>API: state.phase = 'idle'

    loop UI polling co 500ms
        UI->>API: GET /api/scan-status
        API-->>UI: {phase, processed, total, is_active, eta_seconds, ...}
    end

    User->>UI: (opcjonalnie) Click "Stop"
    UI->>API: POST /api/v1/scan-cancel
    API->>AI: state.cancelled = True
    Note over AI: przerwanie między batchami (≤2s)
    AI->>DB: finalize partial (processed zostają)
```

**Recovery:** Jeśli sidecar padnie (crash / kill) w trakcie skanu, zdjęcia z `processed=1` zostają w DB. Po restarcie skan wznawia od pierwszego `processed=0` — żadna praca nie jest tracona, brak duplikatów.

---

## 3. Face identification flow [Faza 2 — FAISS]

```mermaid
flowchart TD
    Start([Nowa twarz wykryta]) --> Embed[Oblicz embedding<br/>ArcFace 512-dim]
    Embed --> Norm[L2 normalize]
    Norm --> Search["FAISS search top-k=5<br/>IndexFlatIP (cosine)"]
    Search --> Filter{"Similarity<br/>> 0.7?"}

    Filter -->|Nie| Pending["Oznacz jako pending<br/>→ następny full DBSCAN"]
    Filter -->|Tak| SameCluster{"Wszystkie top-k<br/>w tym samym<br/>klastrze?"}

    SameCluster -->|Tak| Assign["Auto-assign<br/>do istniejącego klastra"]
    SameCluster -->|"Nie — mieszane"| Pending

    Assign --> Named{"Klaster ma<br/>nazwę osoby?"}
    Named -->|Tak| AutoName["Dziedzicz nazwę osoby<br/>(propagacja)"]
    Named -->|Nie| Done([Zapisz face.cluster_id])
    AutoName --> Done
    Pending --> Done

    classDef decision fill:#fef2c0,stroke:#b8860b,color:#000
    classDef action fill:#c2e0c6,stroke:#0e8a16,color:#000
    class Filter,SameCluster,Named decision
    class Embed,Norm,Search,Assign,AutoName,Pending action
```

**Dlaczego FAISS + DBSCAN razem (a nie zamiast):** FAISS daje O(1)-ish identyfikację pojedynczej nowej twarzy względem znanych (sub-50 ms na 100k embeddingów). DBSCAN robi globalne re-grupowanie (kosztowne, ale rzadkie — przy dużych przyrostach albo na żądanie). Mieszane wyniki top-k odkładamy do następnego pełnego re-clusteringu zamiast ryzykować błędne sklejenie różnych osób.

---

## 4. Data model (ERD)

```mermaid
erDiagram
    photos ||--o{ faces : "ma 0..N"
    clusters ||--o{ faces : "grupuje 0..N"
    persons ||--o| clusters : "nazywa 0..1"
    schema_migrations {
        int version PK
        timestamp applied_at
    }

    photos {
        int id PK
        text path UK
        int processed "0|1"
        int has_faces "0|1"
        timestamp scanned_at
    }

    faces {
        int id PK
        int photo_id FK
        int cluster_id FK "nullable"
        blob embedding "512-dim float32"
        text bounding_box "JSON x,y,w,h"
        int pending "0|1 — wait full DBSCAN"
    }

    clusters {
        int id PK
        int person_id FK "nullable"
        int representative_face_id
        timestamp created_at
    }

    persons {
        int id PK
        text name
        timestamp created_at
    }

    cluster_health {
        int id PK
        timestamp run_at
        real silhouette
        real db_score
        int n_clusters
        real eps
    }

    thumbnail_cache {
        text key PK
        text path
        int size_bytes
        timestamp last_access
    }
```

**Indeksy krytyczne (task 1.3.1):** `idx_faces_person_id`, `idx_faces_photo_id`, `idx_photos_processed (processed, has_faces)`, `idx_thumbnail_last_access`. Cel: query galerii < 100 ms p95 przy 50k zdjęć / 100k twarzy.

---

## 5. AppData layout

```
%AppData%\com.photo.organizer\
├── organizer.db              # SQLite (WAL mode)
├── organizer.db-wal          # Write-Ahead Log
├── organizer.db-shm          # Shared memory
├── organizer.db.bak.{N}      # Backup przed migracją N (task 1.3.2)
├── faiss.index               # [Faza 2] FAISS persisted index
├── faiss.index.ids.pkl       # [Faza 2] face_id mapping
├── thumbnails\               # LRU cache (limit 500 MB, task 2.3.3)
│   ├── {hash1}.jpg
│   └── {hash2}.jpg
├── logs\                     # Rotating (5 × 10 MB, task 1.1.2)
│   ├── backend.log
│   ├── backend.log.1
│   └── ...
└── settings.json             # User prefs (telemetry opt-in, GPU flag)
```

**Ścieżka rozwiązywana przez `get_db_path()`** (task 1.2.2): Windows `%APPDATA%`, macOS `~/Library/Application Support`, Linux `~/.local/share`. Override przez `PHOTO_ORGANIZER_DB_PATH` (dev + Tauri injection).

---

## 6. Process lifecycle & boundaries

```mermaid
stateDiagram-v2
    [*] --> AppLaunch: User otwiera apkę
    AppLaunch --> SpawnSidecar: Rust spawnuje PyInstaller sidecar
    SpawnSidecar --> LoadingModels: Sidecar startuje FastAPI

    state LoadingModels {
        [*] --> RunMigrations: backup + apply_pending_migrations
        RunMigrations --> IntegrityCheck: PRAGMA integrity_check
        IntegrityCheck --> LoadFAISS: load faiss.index lub rebuild z DB
        LoadFAISS --> LoadDeepFace: DeepFace lazy-load (cold start ~5-10s)
        LoadDeepFace --> [*]
    }

    LoadingModels --> Ready: /health → model_loaded=true
    note right of LoadingModels: UI pokazuje Splash (task 4.1.5)

    Ready --> Scanning: POST /api/scan-folder
    Scanning --> Ready: scan complete
    Scanning --> Cancelled: POST /api/v1/scan-cancel
    Cancelled --> Ready: finalize partial
    Scanning --> Error: AICoreError
    Error --> Ready: state.last_error set, no crash

    Ready --> Shutdown: User zamyka okno
    Shutdown --> KillSidecar: Rust kończy sidecar
    KillSidecar --> [*]

    Scanning --> CrashRecovery: sidecar killed mid-scan
    CrashRecovery --> Ready: restart → resume od processed=0
```

---

## 7. Trust boundaries & security

```mermaid
graph LR
    subgraph Untrusted["⚠️ Untrusted input"]
        Photos["Zdjęcia użytkownika<br/>(mogą być malformed)"]
        Paths["Ścieżki skanu<br/>(traversal risk)"]
    end

    subgraph Boundary1["🔒 Path validation (task 1.2.3, 3.1.4)"]
        Validate["validate_scan_path()<br/>• reject ..<br/>• reject symlinks out-of-tree<br/>• reject device files<br/>• reject reserved names"]
    end

    subgraph Boundary2["🔒 Loopback + HTTP hardening (task 1.2.5, 3.1.2, 3.1.3)"]
        Loopback["assert_loopback_bind_host()<br/>bind 127.0.0.1 only"]
        HostGuard["LoopbackHostMiddleware<br/>Host header allowlist<br/>(DNS rebinding mitigation)"]
        SecHeaders["SecurityHeadersMiddleware<br/>X-Content-Type-Options,<br/>X-Frame-Options, Referrer-Policy"]
    end

    subgraph Boundary3["🔒 Log privacy (GAP-003 remediated)"]
        LogHash["log_privacy.py<br/>hash_path_for_log()<br/>basename in scan status"]
    end

    subgraph Trusted["✅ Trusted zone (sidecar)"]
        Processing["Face detection<br/>+ embedding<br/>+ DB write"]
    end

    Photos --> Boundary1
    Paths --> Boundary1
    Boundary1 --> Boundary2
    Boundary2 --> Boundary3
    Boundary3 --> Trusted

    classDef untrusted fill:#f8d7da,stroke:#b60205,color:#000
    classDef boundary fill:#fff3cd,stroke:#b8860b,color:#000
    classDef trusted fill:#d4edda,stroke:#0e8a16,color:#000
    class Photos,Paths untrusted
    class Validate,Loopback,HostGuard,SecHeaders,LogHash boundary
    class Processing trusted
```

**Defense-in-depth:** (1) Tauri file dialog jako jedyne źródło ścieżki (frontend nie wysyła arbitralnych stringów), (2) backend `validate_scan_path()` mimo to waliduje, (3) bind loopback-only, (4) `LoopbackHostMiddleware` przeciw DNS rebinding (task **3.1.2**), (5) `SecurityHeadersMiddleware` na odpowiedziach HTTP sidecara + CSP w `tauri.conf.json` (task **3.1.3**), (6) `log_privacy.py` — hashe ścieżek w logach, basename w `GET /api/scan-status`, (7) `/api/dev/*` i `/docs` wyłączone w release.

---

## 8. CI/CD pipeline (task 3.2.5, 4.1.4)

```mermaid
graph LR
    PR["Pull Request<br/>→ main"] --> CI{ci.yml}
    CI --> Backend["backend job<br/>ubuntu, py3.12<br/>ruff + pytest --cov≥70"]
    CI --> Frontend["frontend job<br/>ubuntu, node20<br/>lint + build + vitest"]
    CI --> Rust["rust job<br/>windows<br/>clippy -D warnings"]
    Backend --> Gate{Wszystkie<br/>zielone?}
    Frontend --> Gate
    Rust --> Gate
    Gate -->|Tak| Merge["Branch protection<br/>pozwala merge"]
    Gate -->|Nie| Block["Block merge"]

    Tag["git tag v*.*.*"] --> Release{release.yml}
    Release --> BuildSidecar["PyInstaller sidecar"]
    BuildSidecar --> TauriBuild["tauri build → MSI"]
    TauriBuild --> Sign["signtool Authenticode<br/>(task 4.1.2)"]
    Sign --> TauriSign["tauri signer<br/>(updater feed)"]
    TauriSign --> Latest["generate latest.json"]
    Latest --> GHRelease["GitHub Release<br/>+ MSI + SHA256"]

    classDef ci fill:#c5def5,stroke:#0052cc,color:#000
    classDef rel fill:#fef2c0,stroke:#b8860b,color:#000
    class CI,Backend,Frontend,Rust,Gate ci
    class Release,BuildSidecar,TauriBuild,Sign,TauriSign,Latest,GHRelease rel
```

---

## 9. Decyzje architektoniczne (ADR skrót)

| # | Decyzja | Uzasadnienie | Status |
|---|---|---|---|
| ADR-1 | SQLite (nie Postgres) | Offline-first desktop, jeden user, zero-config | Stałe |
| ADR-2 | Własne numbered migrations (nie Alembic) | Jeden plik SQLite, overhead Alembic nieuzasadniony do v1.1 | Stałe v1.0 |
| ADR-3 | FAISS dla identyfikacji + DBSCAN dla re-cluster | FAISS szybkie single-query, DBSCAN globalne grupowanie | [Faza 2] |
| ADR-4 | faiss-cpu (nie faiss-gpu) | PyInstaller compat, brak CUDA hassle u userów | [Faza 2] |
| ADR-5 | ThreadPool (nie ProcessPool) dla batch | TensorFlow + PyInstaller + ProcessPool = crash | [Faza 2] |
| ADR-6 | DB w %AppData% (nie katalog instalacji) | Clean uninstall, multi-instance safety, Windows conventions | Faza 1 |
| ADR-7 | Loopback-only forever | Offline-first USP, brak attack surface LAN | Stałe |
| ADR-8 | Sentry default OFF, opt-in | Offline-first USP, privacy | Faza 4 |
| ADR-9 | Windows-first, Mac/Linux v1.1 | Skupienie zasobów, NSIS/MSI już działa | v1.0 |

---

*Diagramy w Mermaid renderują się natywnie na GitHub. Do edycji: [mermaid.live](https://mermaid.live).*
*Ostatnia aktualizacja: 2026-05-27 (task 3.3.x — middleware, scan API, ThumbnailEngine, log_privacy). Powiązane zadania: 1.2.x, 1.3.x, 2.2.x, 3.1.x, 3.2.5, 4.1.x.*
