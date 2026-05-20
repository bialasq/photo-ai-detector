//! Tauri 2 desktop shell for the offline photo organizer.
//!
//! Lifecycle:
//! 1. `setup` spawns the `photo-ai-backend` sidecar (PyInstaller binary) or, in debug
//!    builds, falls back to `python main.py` when the packaged binary is missing.
//! 2. The React frontend polls `http://127.0.0.1:8000/health`, then calls
//!    `getCurrentWindow().show()`.
//! 3. On window close or app exit, `shutdown_backend` kills the child so port 8000 is freed.

use std::path::PathBuf;
use std::sync::Mutex;

use tauri::async_runtime::Receiver;
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Must match `bundle.externalBin` basename in `tauri.conf.json` (`binaries/photo-ai-backend`).
const SIDECAR_NAME: &str = "photo-ai-backend";

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: &str = "8000";

/// `src-tauri` directory at compile time; project root is its parent.
const TAURI_CRATE_DIR: &str = env!("CARGO_MANIFEST_DIR");

/// Holds the spawned backend child so we can kill it on exit.
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
            "PHOTO_ORGANIZER_EXTERNAL_BACKEND set — using existing server at http://{BACKEND_HOST}:{BACKEND_PORT}"
        );
        return Ok(());
    }

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

fn spawn_backend_process(
    app: &AppHandle,
) -> Result<(Receiver<CommandEvent>, CommandChild), String> {
    match spawn_packaged_sidecar(app) {
        Ok(pair) => Ok(pair),
        Err(sidecar_err) => {
            #[cfg(debug_assertions)]
            {
                log::warn!(
                    "Packaged sidecar unavailable ({sidecar_err}); using python main.py"
                );
                spawn_dev_python_backend(app)
            }
            #[cfg(not(debug_assertions))]
            {
                Err(format!(
                    "Failed to spawn '{SIDECAR_NAME}': {sidecar_err}. \
                     Run: npm run sidecar:package"
                ))
            }
        }
    }
}

fn project_root() -> Result<PathBuf, String> {
    PathBuf::from(TAURI_CRATE_DIR)
        .parent()
        .map(PathBuf::from)
        .ok_or_else(|| "could not resolve project root from CARGO_MANIFEST_DIR".to_string())
}

fn spawn_packaged_sidecar(
    app: &AppHandle,
) -> Result<(Receiver<CommandEvent>, CommandChild), String> {
    let project_root = project_root()?;

    let command = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|err| format!("sidecar '{SIDECAR_NAME}' not found: {err}"))?;

    command
        .env("PHOTO_ORGANIZER_HOST", BACKEND_HOST)
        .env("PHOTO_ORGANIZER_PORT", BACKEND_PORT)
        .env("PHOTO_AI_PROJECT_ROOT", project_root.display().to_string())
        .spawn()
        .map_err(|err| format!("sidecar '{SIDECAR_NAME}' spawn failed: {err}"))
}

#[cfg(debug_assertions)]
fn spawn_dev_python_backend(
    app: &AppHandle,
) -> Result<(Receiver<CommandEvent>, CommandChild), String> {
    let project_root = project_root()?;

    let main_py = project_root.join("main.py");
    if !main_py.is_file() {
        return Err(format!(
            "dev fallback requires main.py at {}",
            main_py.display()
        ));
    }

    let python = std::env::var("PHOTO_AI_PYTHON").unwrap_or_else(|_| {
        if cfg!(target_os = "windows") {
            "python".into()
        } else {
            "python3".into()
        }
    });

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
