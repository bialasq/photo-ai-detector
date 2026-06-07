# Stage ArcFace + RetinaFace weights for PyInstaller (--add-data).
# Reuses ~/.deepface/weights when present; downloads only as a last resort.
#
# Usage (repo root):
#   powershell -ExecutionPolicy Bypass -File ./scripts/prefetch-deepface-weights.ps1

$ErrorActionPreference = "Stop"
$env:PYTHONNOUSERSITE = "1"

$RequiredWeights = @("arcface_weights.h5", "retinaface.h5")
$MinWeightBytes = 1024

$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

$VendorRoot = Join-Path $ProjectRoot "vendor\deepface-weights"
$VendorWeights = Join-Path $VendorRoot ".deepface\weights"
$HomeWeights = Join-Path $env:USERPROFILE ".deepface\weights"

function Test-RequiredWeightsPresent {
    param([string]$Directory)

    if (-not (Test-Path -LiteralPath $Directory)) {
        return $false
    }

    foreach ($fileName in $RequiredWeights) {
        $path = Join-Path $Directory $fileName
        if (-not (Test-Path -LiteralPath $path)) {
            return $false
        }
        $size = (Get-Item -LiteralPath $path).Length
        if ($size -lt $MinWeightBytes) {
            throw "Weight file exists but is too small (${fileName}: ${size} bytes): $path"
        }
    }

    return $true
}

function Copy-RequiredWeights {
    param(
        [string]$SourceDir,
        [string]$DestDir
    )

    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

    foreach ($fileName in $RequiredWeights) {
        $source = Join-Path $SourceDir $fileName
        $dest = Join-Path $DestDir $fileName
        Copy-Item -LiteralPath $source -Destination $dest -Force
        $sizeMb = [math]::Round((Get-Item -LiteralPath $dest).Length / 1MB, 1)
        Write-Host "  copied $fileName (${sizeMb} MB)"
    }
}

function Show-VendorSummary {
    Write-Host "DeepFace weights ready under $VendorWeights"
    foreach ($fileName in $RequiredWeights) {
        $path = Join-Path $VendorWeights $fileName
        $sizeMb = [math]::Round((Get-Item -LiteralPath $path).Length / 1MB, 1)
        Write-Host "  OK $fileName (${sizeMb} MB)"
    }
}

Write-Host "=== Prefetch DeepFace weights (ArcFace + RetinaFace) ==="

if (Test-RequiredWeightsPresent -Directory $VendorWeights) {
    Write-Host "Vendor cache already complete - reusing existing files."
    Show-VendorSummary
    exit 0
}

if (Test-RequiredWeightsPresent -Directory $HomeWeights) {
    Write-Host "Copying weights from $HomeWeights -> $VendorWeights"
    Copy-RequiredWeights -SourceDir $HomeWeights -DestDir $VendorWeights
    Show-VendorSummary
    exit 0
}

Write-Host "Local weights not found - downloading via DeepFace probe (network required once)."

$python = $env:PHOTO_AI_PYTHON
if (-not $python -or $python.Trim() -eq "") {
    $python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $python)) {
    throw @"
Python interpreter not found: $python

Create venv and install requirements, or copy weights manually to:
  $VendorWeights

Required files: $($RequiredWeights -join ', ')
"@
}

& $python (Join-Path $PSScriptRoot "prefetch_deepface_weights_probe.py") $VendorRoot
if ($LASTEXITCODE -ne 0) {
    throw "DeepFace weight prefetch probe failed with exit code $LASTEXITCODE"
}

if (-not (Test-RequiredWeightsPresent -Directory $VendorWeights)) {
    throw "Prefetch probe succeeded but required weights are still missing under $VendorWeights"
}

Show-VendorSummary
