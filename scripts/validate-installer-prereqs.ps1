# Pre-flight checks before npm run tauri:build (large PyInstaller sidecar + WiX/NSIS).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$MinSidecarBytes = 10MB
$MinFreeBytes = 2GB

function Get-FreeBytes([string]$Root) {
    $drive = [System.IO.Path]::GetPathRoot($Root)
    if (-not $drive) { return $null }
    $disk = New-Object System.IO.DriveInfo($drive)
    if (-not $disk.IsReady) { return $null }
    return $disk.AvailableFreeSpace
}

Write-Host "=== Photo Organizer — installer preflight ==="

$triple = (rustc --print host-tuple).Trim()
$sidecar = Join-Path $PWD "src-tauri\binaries\photo-ai-backend-$triple.exe"

if (-not (Test-Path -LiteralPath $sidecar)) {
    Write-Error @"
Missing PyInstaller sidecar:
  $sidecar

Run: npm run sidecar:package
"@
}

$sidecarSize = (Get-Item -LiteralPath $sidecar).Length
if ($sidecarSize -lt $MinSidecarBytes) {
    Write-Error @"
Sidecar at $sidecar is only $([math]::Round($sidecarSize/1MB,2)) MB (launcher stub?).
Run: npm run sidecar:package
"@
}

$sidecarMb = [math]::Round($sidecarSize / 1MB, 1)
Write-Host "OK sidecar: $sidecar (${sidecarMb} MB)"

foreach ($root in @($PWD, $env:TEMP, $env:LOCALAPPDATA, ${env:ProgramFiles})) {
    if (-not $root) { continue }
    $free = Get-FreeBytes $root
    if ($null -eq $free) { continue }
    $freeGb = [math]::Round($free / 1GB, 2)
    if ($free -ge $MinFreeBytes) {
        Write-Host "OK free space on ${root}: ${freeGb} GB"
    } else {
        Write-Host "LOW free space on ${root}: ${freeGb} GB"
    }
    if ($free -lt $MinFreeBytes) {
        Write-Warning "Installer may fail (MSI error 112 = not enough disk space). Free at least 2 GB on C: (TEMP + LocalAppData + target folder)."
    }
}

if (-not (Test-Path ".\dist\index.html")) {
    Write-Warning "Frontend dist/ not found. tauri build will run npm run build first."
}

Write-Host "Preflight complete. Build with: npm run tauri:build"
