@echo off
:: OpenVoice — Self-hosted voice synthesis and cloning for churches and nonprofits
:: Copyright (c) 2026 TheRevDrJ — part of the HonedEdge Foundation
:: Licensed under AGPL-3.0-or-later — see LICENSE
setlocal enabledelayedexpansion

:: ============================================================================
:: OpenVoice Server Manager
:: Usage: openvoice.bat [command]
::
:: OpenVoice runs two processes: the backend (5601, serves the UI + API and proxies
:: to the worker) and the VoxCPM design + clone worker (5602). 'start' brings up the
:: worker, then the backend. The backend on 5601 is the only one you connect to.
:: ============================================================================

set "SCRIPT_DIR=%~dp0"
set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "BACKEND_PY=%SCRIPT_DIR%server\.venv\Scripts\pythonw.exe"
set "VOXCPM_PY=%SCRIPT_DIR%voxcpm\.venv\Scripts\pythonw.exe"

if "%~1"=="" goto help
if /i "%~1"=="start" goto start
if /i "%~1"=="stop" goto stop
if /i "%~1"=="restart" goto restart
if /i "%~1"=="status" goto status
if /i "%~1"=="verbose" goto verbose
if /i "%~1"=="log" goto log
if /i "%~1"=="version" goto version
if /i "%~1"=="help" goto help
if /i "%~1"=="--help" goto help
if /i "%~1"=="-h" goto help
goto help

:: ============================================================================
:start
:: ============================================================================
call :port_pid 5601
if defined PORT_PID (
    echo OpenVoice is already running ^(backend PID: !PORT_PID!^).
    echo Use 'openvoice restart' to restart it.
    exit /b 0
)

echo Starting OpenVoice...

:: VoxCPM design worker (5602)
echo   Starting VoxCPM worker (5602)...
start "" /b "%VOXCPM_PY%" -m uvicorn server.workers.voxcpm_worker:app --host 127.0.0.1 --port 5602 > "%LOG_DIR%\voxcpm.log" 2>&1

:: Backend (5601) — serves UI + API, proxies to the worker
echo   Starting backend (5601)...
start "" /b "%BACKEND_PY%" -m uvicorn server.app.main:app --host 0.0.0.0 --port 5601 > "%LOG_DIR%\backend.log" 2>&1

:: Poll port 5601 — every 3s, up to 180s (first run warms / downloads models)
set /a ELAPSED=0
echo   Loading models...
:start_wait
ping 127.0.0.1 -n 4 > nul
set /a ELAPSED+=3
call :port_pid 5601
if defined PORT_PID goto start_success
if !ELAPSED! geq 180 goto start_timeout
if !ELAPSED! equ 60 echo   Still loading... ^(!ELAPSED!s^) — may be downloading models on first run
if !ELAPSED! neq 60 echo   Still loading... ^(!ELAPSED!s^)
goto start_wait

:start_success
echo.
echo   OpenVoice is running ^(backend PID: !PORT_PID!^) — started in !ELAPSED!s
echo.
echo   Open:  http://localhost:5601
echo   Log:   %LOG_DIR%\backend.log
echo.
echo   Use 'openvoice stop' to shut down, 'openvoice log' for live logs.
echo.
exit /b 0

:start_timeout
echo.
echo   Backend did not come up after 3 minutes.
echo   Try 'openvoice verbose' to see errors, or check %LOG_DIR%\backend.log
echo.
exit /b 1

:: ============================================================================
:stop
:: ============================================================================
set "FOUND=0"
for %%P in (5601 5602) do (
    call :port_pid %%P
    if defined PORT_PID (
        echo Stopping process on port %%P ^(PID: !PORT_PID!^)...
        taskkill /F /PID !PORT_PID! > nul 2>&1
        set "FOUND=1"
    )
)
if "!FOUND!"=="0" ( echo OpenVoice is not running. ) else ( echo OpenVoice stopped. )
exit /b 0

:: ============================================================================
:restart
:: ============================================================================
call :stop
echo.
ping 127.0.0.1 -n 3 > nul
call :start
exit /b 0

:: ============================================================================
:status
:: ============================================================================
echo.
echo   OpenVoice status:
for %%S in ("Backend (UI+API):5601" "VoxCPM design:5602") do (
    for /f "tokens=1,2 delims=:" %%a in (%%S) do (
        call :port_pid %%b
        if defined PORT_PID ( echo     [RUNNING] %%a  ^(port %%b, PID !PORT_PID!^) ) else ( echo     [stopped] %%a  ^(port %%b^) )
    )
)
echo.
echo   URL: http://localhost:5601
echo.
exit /b 0

:: ============================================================================
:verbose
::   Workers in the background, backend in the foreground with live logs.
:: ============================================================================
call :port_pid 5601
if defined PORT_PID ( echo Already running headless ^(PID: !PORT_PID!^). Stop it first with 'openvoice stop'. & exit /b 1 )
echo   Starting the VoxCPM worker in the background...
start "" /b "%VOXCPM_PY%" -m uvicorn server.workers.voxcpm_worker:app --host 127.0.0.1 --port 5602 > "%LOG_DIR%\voxcpm.log" 2>&1
echo   Starting backend in the foreground. Ctrl+C to stop.
echo   ================================================
"%SCRIPT_DIR%server\.venv\Scripts\python.exe" -m uvicorn server.app.main:app --host 0.0.0.0 --port 5601
exit /b 0

:: ============================================================================
:log
:: ============================================================================
if not exist "%LOG_DIR%\backend.log" ( echo No log yet. Start the server first with 'openvoice start'. & exit /b 1 )
echo   Showing %LOG_DIR%\backend.log — Ctrl+C to stop.
echo   ================================================
powershell -Command "Get-Content '%LOG_DIR%\backend.log' -Tail 40 -Wait"
exit /b 0

:: ============================================================================
:version
:: ============================================================================
for /f "tokens=2 delims==" %%v in ('findstr /b /c:"VERSION = " "%SCRIPT_DIR%server\app\main.py"') do set "OVVER=%%v"
set "OVVER=!OVVER: =!"
set "OVVER=!OVVER:\"=!"
echo OpenVoice v!OVVER!
exit /b 0

:: ============================================================================
:port_pid
::   Sets PORT_PID to whatever is LISTENING on the port in %1 (clears it if none).
:: ============================================================================
set "PORT_PID="
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr "LISTENING" ^| findstr ":%1 "') do set "PORT_PID=%%a"
exit /b 0

:: ============================================================================
:help
:: ============================================================================
echo.
echo   OpenVoice Server Manager
echo   ========================
echo.
echo   Usage: openvoice [command]
echo.
echo   Commands:
echo     start      Start the workers + backend in the background ^(headless^)
echo     stop       Stop all OpenVoice processes
echo     restart    Stop and restart
echo     status     Show which engines are running
echo     verbose    Backend in the foreground with live logs ^(Ctrl+C to stop^)
echo     log        Follow the backend log in real time
echo     version    Show the OpenVoice version
echo     help       Show this help
echo.
echo   Once running, open:  http://localhost:5601
echo.
exit /b 0
