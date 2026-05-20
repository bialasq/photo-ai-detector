//! Dev / CI sidecar stub when the PyInstaller binary is not present yet.
//! Tauri spawns this executable as `photo-ai-backend`; it runs `python main.py` and
//! forwards child stdout/stderr so `ai_core` / DBSCAN logs reach the Tauri console.

use std::io::{self, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;

fn default_python() -> &'static str {
    if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    }
}

fn resolve_project_root() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?.to_path_buf();
    for _ in 0..8 {
        if dir.join("main.py").is_file() && dir.join("src-tauri").is_dir() {
            return Some(dir);
        }
        if !dir.pop() {
            break;
        }
    }
    None
}

fn copy_stream<R: io::Read + Send + 'static>(mut reader: R, mut writer: impl Write + Send + 'static) {
    thread::spawn(move || {
        let _ = io::copy(&mut reader, &mut writer);
    });
}

fn run_backend() -> i32 {
    let project_root = std::env::var("PHOTO_AI_PROJECT_ROOT")
        .ok()
        .map(PathBuf::from)
        .or_else(resolve_project_root)
        .filter(|p| p.join("main.py").is_file());

    let Some(project_root) = project_root else {
        eprintln!(
            "photo-ai-backend: set PHOTO_AI_PROJECT_ROOT to the repo root (main.py + src-tauri)"
        );
        return 1;
    };

    let python = std::env::var("PHOTO_AI_PYTHON").unwrap_or_else(|_| default_python().to_string());
    let host = std::env::var("PHOTO_ORGANIZER_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port = std::env::var("PHOTO_ORGANIZER_PORT").unwrap_or_else(|_| "8000".to_string());

    let main_py = project_root.join("main.py");
    eprintln!(
        "photo-ai-backend launcher: {python} {} (cwd: {})",
        main_py.display(),
        project_root.display()
    );

    let mut child = match Command::new(&python)
        .arg("main.py")
        .current_dir(&project_root)
        .env("PHOTO_ORGANIZER_HOST", &host)
        .env("PHOTO_ORGANIZER_PORT", &port)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            eprintln!("photo-ai-backend launcher: failed to start {python}: {err}");
            return 1;
        }
    };

    if let Some(stdout) = child.stdout.take() {
        copy_stream(stdout, io::stdout());
    }
    if let Some(stderr) = child.stderr.take() {
        copy_stream(stderr, io::stderr());
    }

    match child.wait() {
        Ok(status) => status.code().unwrap_or(1),
        Err(err) => {
            eprintln!("photo-ai-backend launcher: wait failed: {err}");
            1
        }
    }
}

fn main() {
    let code = run_backend();
    std::process::exit(code);
}
