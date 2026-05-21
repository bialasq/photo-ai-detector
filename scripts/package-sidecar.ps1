# Builds photo-ai-backend sidecar via PyInstaller and renames it for Tauri externalBin.
# Requires: Python 3.12 venv with requirements.txt at repo root.
#
# Usage (from repo root):
#   npm run sidecar:package
#
# Optional override:
#   $env:PHOTO_AI_PYTHON = "C:\path\to\python.exe"
#
# Output:
#   src-tauri/binaries/photo-ai-backend-<target-triple>.exe  (Windows)

$ErrorActionPreference = "Stop"
$env:PYTHONNOUSERSITE = "1"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

function Get-TargetTriple {
    $rustc = Get-Command rustc -ErrorAction SilentlyContinue
    if (-not $rustc) {
        throw "rustc not found. Install Rust (https://rustup.rs) so Tauri can resolve the host target triple."
    }
    return (& rustc --print host-tuple).Trim()
}

# --- Resolve Python interpreter (venv by default) --------------------------------
$python = $env:PHOTO_AI_PYTHON
if (-not $python -or $python.Trim() -eq "") {
    $python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $python)) {
    throw @"
Python interpreter not found: $python

Create the virtual environment from the project root:
  py -3.12 -m venv venv
  .\venv\Scripts\Activate.ps1
  python -m pip install -r requirements.txt
"@
}

Write-Host "Using Python: $python"
& $python --version
if ($LASTEXITCODE -ne 0) {
    throw "Failed to run Python at: $python"
}

# --- Ensure PyInstaller is installed in the venv ---------------------------------
& $python -m pip show pyinstaller 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller into venv..."
    & $python -m pip install --no-cache-dir --no-user pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "pip install pyinstaller failed with exit code $LASTEXITCODE"
    }
}

$triple = Get-TargetTriple
$distDir = Join-Path $ProjectRoot "dist-sidecar"
$buildDir = Join-Path $ProjectRoot "build-sidecar"

Write-Host "Building PyInstaller sidecar for triple: $triple"
Write-Host "PYTHONNOUSERSITE=$env:PYTHONNOUSERSITE"

if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir }
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }

# --- PyInstaller: one-file bundle with full AI / API dependency graph ------------
$sitePackages = Join-Path $ProjectRoot "venv\Lib\site-packages"
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "venv site-packages not found: $sitePackages"
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name photo-ai-backend `
    --distpath $distDir `
    --workpath $buildDir `
    --specpath $ProjectRoot `
    --paths $sitePackages `
    --collect-all tensorflow `
    --collect-all keras `
    --collect-all ml_dtypes `
    --collect-submodules deepface `
    --collect-submodules retina_face `
    --collect-all cv2 `
    --collect-submodules sklearn `
    --collect-submodules pydantic `
    --collect-submodules pydantic_core `
    --collect-submodules fastapi `
    --collect-submodules starlette `
    --collect-submodules uvicorn `
    --copy-metadata tensorflow `
    --copy-metadata keras `
    --copy-metadata deepface `
    --copy-metadata opencv-python `
    --copy-metadata scikit-learn `
    --copy-metadata pydantic `
    --copy-metadata pydantic_core `
    --copy-metadata pillow `
    --hidden-import tensorflow `
    --hidden-import tensorflow.python `
    --hidden-import keras `
    --hidden-import deepface `
    --hidden-import retina_face `
    --hidden-import cv2 `
    --hidden-import sklearn `
    --hidden-import sklearn.cluster `
    --hidden-import sklearn.utils._cython_blas `
    --hidden-import sklearn.neighbors._typedefs `
    --hidden-import sklearn.neighbors._quad_tree `
    --hidden-import sklearn.tree._utils `
    --hidden-import pydantic_core._pydantic_core `
    --hidden-import PIL._imaging `
    --hidden-import PIL.Image `
    --hidden-import numpy `
    --hidden-import mtcnn `
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
    --hidden-import ai_core `
    --hidden-import database `
    main.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$builtExe = Join-Path $distDir "photo-ai-backend.exe"
if (-not (Test-Path -LiteralPath $builtExe)) {
    throw "Expected executable not found: $builtExe"
}

# --- Copy to Tauri externalBin naming convention ---------------------------------
$binariesDir = Join-Path $ProjectRoot "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $binariesDir | Out-Null

$extension = if ($IsWindows -or $env:OS -match "Windows") { ".exe" } else { "" }
$destName = "photo-ai-backend-$triple$extension"
$destPath = Join-Path $binariesDir $destName

Copy-Item -Force $builtExe $destPath
Write-Host "Sidecar ready: $destPath"
