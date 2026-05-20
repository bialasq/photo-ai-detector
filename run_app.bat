@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ===========================================================================
REM  Photo AI Organizer — one-click startup (project root)
REM  Backend:  venv + uvicorn on http://127.0.0.1:8000  (separate console)
REM  Frontend: npm run tauri:dev  (Tauri 2 + Vite, same root)
REM ===========================================================================

set "PROJECT_ROOT=%~dp0"
if "!PROJECT_ROOT:~-1!"=="\" set "PROJECT_ROOT=!PROJECT_ROOT:~0,-1!"

cd /d "!PROJECT_ROOT!"

REM --- 1. Validate Python virtual environment --------------------------------
if not exist ".\venv\Scripts\activate.bat" (
    echo.
    echo [ERROR] Python virtual environment not found:
    echo   !PROJECT_ROOT!\venv\Scripts\activate.bat
    echo.
    echo Setup from the project root (Python 3.12 required):
    echo   py -3.12 -m venv venv
    echo   .\venv\Scripts\activate
    echo   set PYTHONNOUSERSITE=1
    echo   python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM --- 2. Launch FastAPI backend in a dedicated console ------------------------
REM     PYTHONNOUSERSITE=1 blocks global user-site packages (e.g. AppData).
echo.
echo [1/4] Starting backend in "Photo Organizer - Backend" ...
start "Photo Organizer - Backend" cmd /k "cd /d ""!PROJECT_ROOT!"" && call .\venv\Scripts\activate.bat && set PYTHONNOUSERSITE=1 && python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info"

REM --- 3. Allow models and Uvicorn to initialize -------------------------------
echo [2/4] Waiting 5 seconds for backend initialization...
timeout /t 5 /nobreak >nul

REM --- 4. Prevent Tauri sidecar from binding the same port ---------------------
set "PHOTO_ORGANIZER_EXTERNAL_BACKEND=1"

REM --- 5. Start Tauri + Vite dev shell -------------------------------------------
echo [3/4] Starting Tauri desktop app (npm run tauri:dev)...
call npm run tauri:dev
set "TAURI_EXIT=!ERRORLEVEL!"

REM --- 6. Cleanup backend on exit ------------------------------------------------
echo.
echo [4/4] Cleaning up backend processes...
call :Cleanup

set "EXIT_CODE=!TAURI_EXIT!"
endlocal
exit /b %EXIT_CODE%

:Cleanup
REM Close the titled backend console window.
taskkill /FI "WINDOWTITLE eq Photo Organizer - Backend*" /F >nul 2>&1

REM Safety net: terminate any process still listening on port 8000.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    if not "%%P"=="0" (
        taskkill /PID %%P /F >nul 2>&1
    )
)

exit /b 0
