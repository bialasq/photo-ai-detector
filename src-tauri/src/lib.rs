//! Tauri 2 desktop shell for the offline photo organizer.
//!
//! # Backend lifecycle
//!
//! | Mode | `PHOTO_ORGANIZER_EXTERNAL_BACKEND` | Backend process |
//! |------|-----------------------------------|-----------------|
//! | **Production** (`tauri build`) | unset | Bundled PyInstaller sidecar `photo-ai-backend` from `bundle.externalBin` |
//! | **`run_app.bat` / manual uvicorn** | `1` / `true` / `yes` | No spawn — UI talks to an already-running server on port 8000 |
//! | **Dev** (`tauri dev`) | unset | PyInstaller sidecar if `binaries/photo-ai-backend-{triple}` is present (≥10 MB); else `venv/Scripts/python.exe main.py` |
//!
//! On startup (`setup`), when external backend is **not** requested, Rust spawns the native
//! sidecar and stores the child handle. On main-window close or app exit, `shutdown_backend`
//! terminates that child so port 8000 is released.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use tauri::async_runtime::Receiver;
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Basename registered in `tauri.conf.json` → `bundle.externalBin`: `binaries/photo-ai-backend`.
/// Tauri resolves the host triple at runtime (`photo-ai-backend-x86_64-pc-windows-msvc.exe`, etc.).
const SIDECAR_NAME: &str = "photo-ai-backend";

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: &str = "8000";

/// Compile-time `src-tauri` directory; used only for dev `python main.py` fallback paths.
const TAURI_CRATE_DIR: &str = env!("CARGO_MANIFEST_DIR");

/// PyInstaller `photo-ai-backend` is hundreds of MB; the Rust dev launcher stub is ~250 KB.
const MIN_PYINSTALLER_SIDECAR_BYTES: u64 = 10 * 1024 * 1024;

/// Child process spawned by this app (sidecar or dev python). `None` when using an external server.
struct BackendProcessState(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcessState(Mutex::new(None)))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            spawn_backend(app.handle())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(on_run_event);
}

fn on_run_event(app_handle: &AppHandle, event: RunEvent) {
    match event {
        RunEvent::Exit | RunEvent::ExitRequested { .. } => {
            shutdown_backend(app_handle);
        }
        RunEvent::WindowEvent { label, event, .. } if label == "main" => match event {
            WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed => {
                shutdown_backend(app_handle);
            }
            _ => {}
        },
        _ => {}
    }
}

/// True when `run_app.bat` (or the user) already started uvicorn — Tauri must not spawn or kill it.
fn external_backend_requested() -> bool {
    std::env::var("PHOTO_ORGANIZER_EXTERNAL_BACKEND")
        .map(|value| {
            let normalized = value.trim();
            normalized == "1"
                || normalized.eq_ignore_ascii_case("true")
                || normalized.eq_ignore_ascii_case("yes")
        })
        .unwrap_or(false)
}

fn spawn_backend(app: &AppHandle) -> tauri::Result<()> {
    if external_backend_requested() {
        log::info!(
            "PHOTO_ORGANIZER_EXTERNAL_BACKEND is set — using existing server at http://{BACKEND_HOST}:{BACKEND_PORT} (no sidecar spawn)"
        );
        return Ok(());
    }

    #[cfg(not(debug_assertions))]
    log::info!(
        "Release build: starting bundled sidecar '{SIDECAR_NAME}' (see bundle.externalBin in tauri.conf.json)"
    );

    #[cfg(debug_assertions)]
    log::info!(
        "Dev build: starting '{SIDECAR_NAME}' sidecar when present, else python main.py fallback"
    );

    {
        let state = app.state::<BackendProcessState>();
        let guard = state.0.lock().expect("backend process mutex poisoned");
        if guard.is_some() {
            log::warn!("{SIDECAR_NAME} already running; skipping duplicate spawn");
            return Ok(());
        }
    }

    let (mut rx, child) = spawn_backend_process(app).map_err(|message| {
        std::io::Error::new(std::io::ErrorKind::Other, message)
    })?;

    let pid = child.pid();
    app.state::<BackendProcessState>()
        .0
        .lock()
        .expect("backend process mutex poisoned")
        .replace(child);

    log::info!(
        "Started {SIDECAR_NAME} at http://{BACKEND_HOST}:{BACKEND_PORT} (pid {pid})"
    );

    tauri::async_runtime::spawn(async move {
        monitor_backend_output(&mut rx).await;
    });

    Ok(())
}

/// Spawn order:
/// - **Release:** always `app.shell().sidecar()` (bundled next to the installed `.exe`).
/// - **Dev:** PyInstaller binary in `src-tauri/binaries/` if ≥10 MB, else venv `python main.py`.
fn spawn_backend_process(
    app: &AppHandle,
) -> Result<(Receiver<CommandEvent>, CommandChild), String> {
    #[cfg(not(debug_assertions))]
    {
        log::info!("Release: spawning bundled sidecar via Tauri resource resolver");
        return spawn_packaged_sidecar(app);
    }

    #[cfg(debug_assertions)]
    {
        if let Some(sidecar_path) = packaged_sidecar_binary_path() {
            log::info!(
                "Dev: using compiled PyInstaller sidecar at {}",
                sidecar_path.display()
            );
            return spawn_packaged_sidecar(app);
        }

        log::warn!(
            "Dev: no PyInstaller sidecar in src-tauri/binaries/ (missing or only the launcher stub); \
             falling back to venv Python"
        );
        spawn_dev_python_backend(app)
    }
}

fn dev_project_root() -> Result<PathBuf, String> {
    PathBuf::from(TAURI_CRATE_DIR)
        .parent()
        .map(PathBuf::from)
        .ok_or_else(|| "could not resolve project root from CARGO_MANIFEST_DIR".to_string())
}

/// `src-tauri/binaries/photo-ai-backend-{triple}[.exe]` from `npm run sidecar:package`.
///
/// Ignores the small Rust dev launcher stub (`build.rs`) so we do not spawn system `python`.
fn packaged_sidecar_binary_path() -> Option<PathBuf> {
    let binaries_dir = PathBuf::from(TAURI_CRATE_DIR).join("binaries");
    let entries = std::fs::read_dir(&binaries_dir).ok()?;

    let mut best: Option<(u64, PathBuf)> = None;

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }

        let name = entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with("photo-ai-backend-") {
            continue;
        }

        let size = entry.metadata().ok()?.len();
        if size < MIN_PYINSTALLER_SIDECAR_BYTES {
            log::debug!(
                "Skipping sidecar candidate {} ({} bytes — likely dev launcher stub)",
                path.display(),
                size
            );
            continue;
        }

        if best.as_ref().is_none_or(|(best_size, _)| size > *best_size) {
            best = Some((size, path));
        }
    }

    best.map(|(_, path)| path)
}

/// Repository root for `PHOTO_AI_PROJECT_ROOT` when running under `tauri dev`.
///
/// `npm run tauri:dev` is started from the repo root, so `current_dir` is preferred.
/// Falls back to the parent of `src-tauri` (compile-time `CARGO_MANIFEST_DIR`) if cwd differs.
#[cfg(debug_assertions)]
fn dev_repository_root() -> Result<PathBuf, String> {
    let cwd = std::env::current_dir()
        .map_err(|err| format!("failed to read process current_dir: {err}"))?;

    if cwd.join("main.py").is_file() && cwd.join("src-tauri").is_dir() {
        return Ok(cwd);
    }

    let from_manifest = dev_project_root()?;
    if from_manifest.join("main.py").is_file() {
        return Ok(from_manifest);
    }

    Err(format!(
        "could not locate repo root (cwd={}, expected main.py + src-tauri/)",
        cwd.display()
    ))
}

/// Value for `PHOTO_AI_PROJECT_ROOT`: repo root in dev, app data dir in release.
fn sidecar_project_root(_app: &AppHandle, work_dir: &Path) -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    {
        let repo_root = dev_repository_root()?;
        log::info!(
            "Dev sidecar: PHOTO_AI_PROJECT_ROOT={} (database cwd={})",
            repo_root.display(),
            work_dir.display()
        );
        Ok(repo_root)
    }

    #[cfg(not(debug_assertions))]
    {
        Ok(work_dir.to_path_buf())
    }
}

/// Writable directory for `organizer.db` when running the packaged sidecar (release-safe).
fn sidecar_working_directory(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|err| format!("app_data_dir unavailable: {err}"))?;

    std::fs::create_dir_all(&dir).map_err(|err| {
        format!(
            "failed to create app data directory {}: {err}",
            dir.display()
        )
    })?;

    Ok(dir)
}

fn spawn_packaged_sidecar(
    app: &AppHandle,
) -> Result<(Receiver<CommandEvent>, CommandChild), String> {
    let work_dir = sidecar_working_directory(app)?;
    let project_root = sidecar_project_root(app, &work_dir)?;

    log::info!(
        "Spawning packaged sidecar '{SIDECAR_NAME}' (cwd: {}, PHOTO_AI_PROJECT_ROOT: {}, http://{BACKEND_HOST}:{BACKEND_PORT})",
        work_dir.display(),
        project_root.display()
    );

    let command = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|err| {
            format!(
                "sidecar '{SIDECAR_NAME}' not found (expected under src-tauri/binaries/ \
                 from bundle.externalBin): {err}"
            )
        })?;

    command
        .env("PHOTO_ORGANIZER_HOST", BACKEND_HOST)
        .env("PHOTO_ORGANIZER_PORT", BACKEND_PORT)
        .env("PHOTO_AI_PROJECT_ROOT", project_root.display().to_string())
        .env("PYTHONNOUSERSITE", "1")
        .current_dir(&work_dir)
        .spawn()
        .map_err(|err| format!("sidecar '{SIDECAR_NAME}' spawn failed: {err}"))
}

#[cfg(debug_assertions)]
fn resolve_dev_python(project_root: &Path) -> Result<String, String> {
    if let Ok(from_env) = std::env::var("PHOTO_AI_PYTHON") {
        let trimmed = from_env.trim();
        if !trimmed.is_empty() {
            return Ok(trimmed.to_string());
        }
    }

    #[cfg(target_os = "windows")]
    let venv_python = project_root.join("venv").join("Scripts").join("python.exe");

    #[cfg(not(target_os = "windows"))]
    let venv_python = project_root.join("venv").join("bin").join("python3");

    if venv_python.is_file() {
        return Ok(venv_python.display().to_string());
    }

    Err(format!(
        "venv Python not found at {}. From the repo root run: \
         py -3.12 -m venv venv && .\\venv\\Scripts\\python.exe -m pip install -r requirements.txt",
        venv_python.display()
    ))
}

#[cfg(debug_assertions)]
fn spawn_dev_python_backend(
    app: &AppHandle,
) -> Result<(Receiver<CommandEvent>, CommandChild), String> {
    let project_root = dev_repository_root()?;

    let main_py = project_root.join("main.py");
    if !main_py.is_file() {
        return Err(format!(
            "dev fallback requires main.py at {}",
            main_py.display()
        ));
    }

    let python = resolve_dev_python(&project_root)?;

    log::info!(
        "Dev backend: {python} {} (cwd: {}, http://{BACKEND_HOST}:{BACKEND_PORT})",
        main_py.display(),
        project_root.display()
    );

    app.shell()
        .command(python)
        .args(["main.py"])
        .current_dir(&project_root)
        .env("PHOTO_ORGANIZER_HOST", BACKEND_HOST)
        .env("PHOTO_ORGANIZER_PORT", BACKEND_PORT)
        .env("PHOTO_AI_PROJECT_ROOT", project_root.display().to_string())
        .env("PYTHONNOUSERSITE", "1")
        .spawn()
        .map_err(|err| format!("dev python spawn failed: {err}"))
}

async fn monitor_backend_output(rx: &mut Receiver<CommandEvent>) {
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                log::info!("[{SIDECAR_NAME}] {}", String::from_utf8_lossy(&line).trim_end());
            }
            CommandEvent::Stderr(line) => {
                log::warn!("[{SIDECAR_NAME}] {}", String::from_utf8_lossy(&line).trim_end());
            }
            CommandEvent::Error(message) => {
                log::error!("[{SIDECAR_NAME}] {message}");
            }
            CommandEvent::Terminated(payload) => {
                log::warn!(
                    "[{SIDECAR_NAME}] process ended (code={:?}, signal={:?})",
                    payload.code,
                    payload.signal
                );
                break;
            }
            _ => {}
        }
    }
}

/// Terminates only a backend process spawned by this app (not an external uvicorn from `run_app.bat`).
fn shutdown_backend(app: &AppHandle) {
    let child = app
        .state::<BackendProcessState>()
        .0
        .lock()
        .expect("backend process mutex poisoned")
        .take();

    let Some(child) = child else {
        return;
    };

    let pid = child.pid();

    match child.kill() {
        Ok(()) => log::info!("Terminated {SIDECAR_NAME} (pid {pid})"),
        Err(err) => log::error!("Failed to terminate {SIDECAR_NAME} (pid {pid}): {err}"),
    }
}
