# Builds photo-ai-engine sidecar via PyInstaller and renames it for Tauri externalBin.
# Requires: Python venv with requirements.txt + pyinstaller installed.
#
# Usage (from repo root):
#   npm run sidecar:package
#
# Output:
#   src-tauri/binaries/photo-ai-engine-<target-triple>.exe  (Windows)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Get-TargetTriple {
    $rustc = Get-Command rustc -ErrorAction SilentlyContinue
    if (-not $rustc) {
        throw "rustc not found. Install Rust (https://rustup.rs) so Tauri can resolve the host target triple."
    }
    return (& rustc --print host-tuple).Trim()
}

$triple = Get-TargetTriple
$distDir = Join-Path $PWD "dist-sidecar"
$buildDir = Join-Path $PWD "build-sidecar"
$specName = "photo-ai-engine.spec"

Write-Host "Building PyInstaller sidecar for triple: $triple"

$python = $env:PHOTO_AI_PYTHON
if (-not $python) {
    $python = "python"
}

& $python -m pip show pyinstaller 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..."
    & $python -m pip install pyinstaller
}

if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir }
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name photo-ai-engine `
    --distpath $distDir `
    --workpath $buildDir `
    --specpath $PWD `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols `
    --hidden-import uvicorn.protocols.http `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan `
    --hidden-import uvicorn.lifespan.on `
  main.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$builtExe = Join-Path $distDir "photo-ai-engine.exe"
if (-not (Test-Path $builtExe)) {
    throw "Expected executable not found: $builtExe"
}

$binariesDir = Join-Path $PWD "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $binariesDir | Out-Null

$extension = if ($IsWindows -or $env:OS -match "Windows") { ".exe" } else { "" }
$destName = "photo-ai-engine-$triple$extension"
$destPath = Join-Path $binariesDir $destName

Copy-Item -Force $builtExe $destPath
Write-Host "Sidecar ready: $destPath"
