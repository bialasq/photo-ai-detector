use std::path::PathBuf;
use std::process::Command;

fn main() {
    ensure_sidecar_binary();
    tauri_build::build();
}

/// Tauri requires `binaries/photo-ai-backend-{TARGET}{ext}` at compile time.
/// If missing (typical in dev), build the Rust launcher stub that runs `python main.py`.
fn ensure_sidecar_binary() {
    let target =
        std::env::var("TARGET").expect("TARGET must be set when build.rs runs");
    let manifest_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let ext = if target.contains("windows") { ".exe" } else { "" };
    let dest_name = format!("photo-ai-backend-{target}{ext}");
    let binaries_dir = manifest_dir.join("binaries");
    let dest = binaries_dir.join(&dest_name);

    println!("cargo:rerun-if-changed=backend-launcher/src/main.rs");
    println!("cargo:rerun-if-changed=backend-launcher/Cargo.toml");

    if dest.is_file() {
        println!("cargo:rerun-if-changed=binaries/{dest_name}");
        return;
    }

    eprintln!(
        "cargo:warning=Sidecar binary missing at {}; building dev launcher...",
        dest.display()
    );

    std::fs::create_dir_all(&binaries_dir).expect("create binaries directory");

    let launcher_manifest = manifest_dir.join("backend-launcher/Cargo.toml");
    let status = Command::new("cargo")
        .args([
            "build",
            "--release",
            "--manifest-path",
            launcher_manifest.to_str().expect("utf-8 manifest path"),
        ])
        .status()
        .expect("failed to invoke cargo for backend-launcher");

    if !status.success() {
        panic!(
            "backend-launcher build failed. Install Rust, or run: npm run sidecar:package"
        );
    }

    let built = manifest_dir
        .join("backend-launcher/target/release")
        .join(format!("photo-ai-backend{ext}"));

    if !built.is_file() {
        panic!(
            "expected launcher at {} after build",
            built.display()
        );
    }

    std::fs::copy(&built, &dest).unwrap_or_else(|err| {
        panic!(
            "failed to copy {} -> {}: {err}",
            built.display(),
            dest.display()
        );
    });

    eprintln!("cargo:warning=Created dev sidecar at {}", dest.display());
}
