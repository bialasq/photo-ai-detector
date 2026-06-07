# Offline smoke test for the PyInstaller sidecar (faiss + bundled DeepFace weights).
# Does NOT build MSI — validates dist-sidecar/photo-ai-backend.exe only.
#
# Usage (repo root):
#   powershell -ExecutionPolicy Bypass -File ./scripts/verify-sidecar-bundle.ps1
#   powershell -ExecutionPolicy Bypass -File ./scripts/verify-sidecar-bundle.ps1 -SidecarExe path\to\photo-ai-backend.exe

param(
    [string]$SidecarExe = "",
    [int]$StartupTimeoutSec = 300,
    [int]$HealthPollIntervalSec = 2
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

function Resolve-SidecarExe {
    if ($SidecarExe -and (Test-Path -LiteralPath $SidecarExe)) {
        return (Resolve-Path -LiteralPath $SidecarExe).Path
    }

    $candidates = @(
        (Join-Path $ProjectRoot "dist-sidecar\photo-ai-backend.exe"),
        (Join-Path $ProjectRoot "src-tauri\binaries\photo-ai-backend-x86_64-pc-windows-msvc.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw @"
Sidecar executable not found.

Build first:
  npm run sidecar:package

Or pass -SidecarExe path\to\photo-ai-backend.exe
"@
}

function Stop-SidecarProcess {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process -or $Process.HasExited) {
        return
    }

    try {
        Stop-Process -Id $Process.Id -Force -ErrorAction Stop
        [void]$Process.WaitForExit(15000)
    } catch {
        Write-Warning "Failed to stop sidecar pid $($Process.Id): $_"
    }
}

$exePath = Resolve-SidecarExe
$exeSizeMb = [math]::Round((Get-Item -LiteralPath $exePath).Length / 1MB, 1)
Write-Host "=== Verify sidecar bundle (offline) ==="
Write-Host "Executable: $exePath (${exeSizeMb} MB)"

$logDir = Join-Path $env:TEMP "photo-organizer-sidecar-verify"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir "stdout.log"
$stderrLog = Join-Path $logDir "stderr.log"
$dbPath = Join-Path $logDir "verify-sidecar.db"
$port = "8010"

foreach ($path in @($stdoutLog, $stderrLog)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}
if (Test-Path -LiteralPath $dbPath) {
    Remove-Item -LiteralPath $dbPath -Force
}

$previousProxy = $env:HTTP_PROXY
$previousHttpsProxy = $env:HTTPS_PROXY
$previousDeepfaceHome = $env:DEEPFACE_HOME
$previousHost = $env:PHOTO_ORGANIZER_HOST
$previousPort = $env:PHOTO_ORGANIZER_PORT
$previousDbPath = $env:PHOTO_ORGANIZER_DB_PATH
$previousAppData = $env:PHOTO_ORGANIZER_APP_DATA

$process = $null

try {
    # Block outbound HTTP(S) so missing bundled weights cannot be downloaded silently.
    $env:HTTP_PROXY = "http://127.0.0.1:9"
    $env:HTTPS_PROXY = "http://127.0.0.1:9"
    Remove-Item Env:DEEPFACE_HOME -ErrorAction SilentlyContinue

    $env:PHOTO_ORGANIZER_HOST = "127.0.0.1"
    $env:PHOTO_ORGANIZER_PORT = $port
    $env:PHOTO_ORGANIZER_DB_PATH = $dbPath
    $env:PHOTO_ORGANIZER_APP_DATA = $logDir

    Write-Host "Starting sidecar (offline proxies enabled, DEEPFACE_HOME unset)..."

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exePath
    $psi.WorkingDirectory = $logDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi

    $stdoutBuilder = New-Object System.Text.StringBuilder
    $stderrBuilder = New-Object System.Text.StringBuilder

    $outHandler = {
        if ($EventArgs.Data) {
            [void]$Event.MessageData.AppendLine($EventArgs.Data)
        }
    }

    Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -Action $outHandler -MessageData $stdoutBuilder | Out-Null
    Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -Action $outHandler -MessageData $stderrBuilder | Out-Null

    if (-not $process.Start()) {
        throw "Failed to start sidecar process."
    }

    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()

    $healthUrl = "http://127.0.0.1:${port}/api/v1/health"
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
    $healthOk = $false

    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) {
            break
        }

        try {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                $healthOk = $true
                break
            }
        } catch {
            Start-Sleep -Seconds $HealthPollIntervalSec
        }
    }

    Start-Sleep -Seconds 2
    $stdoutBuilder.ToString() | Set-Content -LiteralPath $stdoutLog -Encoding UTF8
    $stderrBuilder.ToString() | Set-Content -LiteralPath $stderrLog -Encoding UTF8

    $backendLog = Join-Path $logDir "logs\backend.log"
    $backendLogText = ""
    if (Test-Path -LiteralPath $backendLog) {
        $backendLogText = Get-Content -LiteralPath $backendLog -Raw
    }

    $combinedLog = (Get-Content -LiteralPath $stdoutLog -Raw) + "`n" +
        (Get-Content -LiteralPath $stderrLog -Raw) + "`n" +
        $backendLogText

    if (-not $healthOk) {
        Write-Error @"
Sidecar did not become healthy within ${StartupTimeoutSec}s.

stdout: $stdoutLog
stderr: $stderrLog
exit code: $($process.ExitCode)
"@
    }

    Write-Host "OK health: $healthUrl"

    $detectorReady = $combinedLog -match "Detector backend ready|detector\.ready"
    if (-not $detectorReady) {
        Write-Error "Sidecar log missing detector ready marker. See $backendLog, $stdoutLog and $stderrLog"
    }
    Write-Host "OK detector probe completed (offline bundled weights)"

    $faissLoaded = $combinedLog -match "Successfully loaded faiss"
    if (-not $faissLoaded) {
        Write-Error "Sidecar log missing faiss load success. See $backendLog"
    }
    Write-Host "OK faiss loaded in bundled sidecar"

    $downloadPatterns = @(
        "drive\.google",
        "gdown",
        "Failed to download",
        "URL fetch failed",
        "downloading.*\.h5",
        "download.*weights"
    )

    foreach ($pattern in $downloadPatterns) {
        if ($combinedLog -match $pattern) {
            Write-Error "Sidecar log suggests network weight download (pattern '$pattern'). See $stdoutLog and $stderrLog"
        }
    }
    Write-Host "OK no weight download attempts detected in logs"

    Write-Host "VERIFY PASSED (offline sidecar bundle)"
    exit 0
}
finally {
    if ($null -ne $process) {
        Stop-SidecarProcess -Process $process
    }

    $env:HTTP_PROXY = $previousProxy
    $env:HTTPS_PROXY = $previousHttpsProxy

    if ($null -ne $previousDeepfaceHome) {
        $env:DEEPFACE_HOME = $previousDeepfaceHome
    } else {
        Remove-Item Env:DEEPFACE_HOME -ErrorAction SilentlyContinue
    }

    $env:PHOTO_ORGANIZER_HOST = $previousHost
    $env:PHOTO_ORGANIZER_PORT = $previousPort
    $env:PHOTO_ORGANIZER_DB_PATH = $previousDbPath
    $env:PHOTO_ORGANIZER_APP_DATA = $previousAppData
}
