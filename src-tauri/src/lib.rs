//! Tauri 2 desktop shell for the offline photo organizer.
//!
//! Lifecycle:
//! 1. `setup` spawns the `photo-ai-engine` sidecar (PyInstaller binary) or, in debug builds,
//!    falls back to `python main.py` when the packaged binary is missing.
//! 2. The React frontend polls `http://127.0.0.1:8000/health`, then calls `getCurrentWindow().show()`.
//! 3. On window close or app exit, `shutdown_engine` calls `CommandChild::kill()` so Uvicorn does not
//!    keep listening on port 8000.

use std::path::PathBuf;
use std::sync::Mutex;

use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::process::{Command, CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Must match `bundle.externalBin` basename in `tauri.conf.json` (`binaries/photo-ai-engine`).
const SIDECAR_NAME: &str = "photo-ai-engine";

/// `src-tauri` directory at compile time; project root is its parent.
const TAURI_CRATE_DIR: &str = env!("CARGO_MANIFEST_DIR");

/// Holds the spawned backend child so we can kill it on exit.
struct EngineProcessState(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(EngineProcessState(Mutex::new(None)))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            spawn_engine(app.handle())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(on_run_event);
}

fn on_run_event(app_handle: &AppHandle, event: RunEvent) {
    match event {
        RunEvent::Exit | RunEvent::ExitRequested { .. } => {
            shutdown_engine(app_handle);
        }
        RunEvent::WindowEvent { label, event, .. } if label == "main" => match event {
            WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed => {
                shutdown_engine(app_handle);
            }
            _ => {}
        },
        _ => {}
    }
}

fn spawn_engine(app: &AppHandle) -> tauri::Result<()> {
    {
        let guard = app
            .state::<EngineProcessState>()
            .0
            .lock()
            .expect("engine process mutex poisoned");
        if guard.is_some() {
            log::warn!("photo-ai-engine already running; skipping duplicate spawn");
            return Ok(());
        }
    }

    let (mut rx, child) = spawn_backend_process(app).map_err(|message| {
        std::io::Error::new(std::io::ErrorKind::Other, message)
    })?;

    let pid = child.pid();
    app.state::<EngineProcessState>()
        .0
        .lock()
        .expect("engine process mutex poisoned")
        .replace(child);

    log::info!("Started photo-ai-engine backend (pid {pid})");

    tauri::async_runtime::spawn(async move {
        monitor_engine_output(&mut rx).await;
    });

    Ok(())
}

fn spawn_backend_process(
    app: &AppHandle,
) -> Result<(tauri_plugin_shell::process::CommandReceiver, CommandChild), String> {
    match spawn_packaged_sidecar(app) {
        Ok(pair) => Ok(pair),
        Err(sidecar_err) => {
            #[cfg(debug_assertions)]
            {
                log::warn!("Packaged sidecar unavailable ({sidecar_err}); using python main.py");
                spawn_dev_python_backend()
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

fn spawn_packaged_sidecar(
    app: &AppHandle,
) -> Result<(tauri_plugin_shell::process::CommandReceiver, CommandChild), String> {
    let command = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|err| format!("sidecar '{SIDECAR_NAME}' not found: {err}"))?;

    command
        .spawn()
        .map_err(|err| format!("sidecar '{SIDECAR_NAME}' spawn failed: {err}"))
}

#[cfg(debug_assertions)]
fn spawn_dev_python_backend(
) -> Result<(tauri_plugin_shell::process::CommandReceiver, CommandChild), String> {
    let project_root = PathBuf::from(TAURI_CRATE_DIR)
        .parent()
        .map(PathBuf::from)
        .ok_or_else(|| "could not resolve project root from CARGO_MANIFEST_DIR".to_string())?;

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
        "Using dev backend: {python} {} (cwd: {})",
        main_py.display(),
        project_root.display()
    );

    Command::new(python)
        .args(["main.py"])
        .current_dir(project_root)
        .spawn()
        .map_err(|err| format!("dev python spawn failed: {err}"))
}

async fn monitor_engine_output(rx: &mut tauri_plugin_shell::process::CommandReceiver) {
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                log::info!("[photo-ai-engine] {}", String::from_utf8_lossy(&line).trim_end());
            }
            CommandEvent::Stderr(line) => {
                log::warn!("[photo-ai-engine] {}", String::from_utf8_lossy(&line).trim_end());
            }
            CommandEvent::Error(message) => {
                log::error!("[photo-ai-engine] {message}");
            }
            CommandEvent::Terminated(payload) => {
                log::warn!(
                    "[photo-ai-engine] process ended (code={:?}, signal={:?})",
                    payload.code,
                    payload.signal
                );
                break;
            }
            _ => {}
        }
    }
}

fn shutdown_engine(app: &AppHandle) {
    let child = app
        .state::<EngineProcessState>()
        .0
        .lock()
        .expect("engine process mutex poisoned")
        .take();

    let Some(child) = child else {
        return;
    };

    let pid = child.pid();

    match child.kill() {
        Ok(()) => log::info!("Terminated photo-ai-engine (pid {pid})"),
        Err(err) => log::error!("Failed to terminate photo-ai-engine (pid {pid}): {err}"),
    }
}
