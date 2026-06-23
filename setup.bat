@echo off
:: OpenVoice — Self-hosted voice synthesis and cloning for churches and nonprofits
:: Copyright (c) 2026 TheRevDrJ — part of the HonedEdge Foundation
:: Licensed under AGPL-3.0-or-later — see LICENSE
setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"

:: ============================================================================
:: OpenVoice Setup Script
:: Installs all dependencies and configures Windows for OpenVoice.
:: Must be run as Administrator (for firewall rule and long paths).
::
:: OpenVoice runs TWO engines, each in its own uv-managed venv (their deps
:: conflict): the Chatterbox backend (server/) and the VoxCPM design + clone worker
:: (voxcpm/). uv reads each pyproject.toml + uv.lock and reproduces the exact pinned
:: build, CUDA torch and all.
:: ============================================================================

echo.
echo   ============================================
echo     OpenVoice Setup
echo   ============================================
echo.

:: ----------------------------------------------------------------------------
:: Check for admin privileges
:: ----------------------------------------------------------------------------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo   ERROR: This script must be run as Administrator.
    echo   Right-click and select "Run as administrator".
    echo.
    pause
    exit /b 1
)
echo   [OK] Running as Administrator

:: ----------------------------------------------------------------------------
:: Check NVIDIA drivers — nvidia-smi alone is NOT enough. Windows ships a basic
:: display driver that provides nvidia-smi but NOT the CUDA runtime DLLs we use.
:: We re-verify cublas64_12.dll actually loads after the backend venv is built.
:: ----------------------------------------------------------------------------
nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   WARNING: NVIDIA drivers not detected!
    echo.
    echo   OpenVoice needs an NVIDIA GPU with the FULL driver package from
    echo   nvidia.com (not the basic Windows Update display driver). Without
    echo   the CUDA runtime, synthesis will fail.
    echo.
    echo   Download from: https://www.nvidia.com/drivers
    echo   Install, restart, then run this setup again.
    echo.
    set /p CONTINUE="   Continue anyway? (y/N): "
    if /i not "!CONTINUE!"=="y" (
        echo   Setup cancelled. Install NVIDIA drivers first.
        pause
        exit /b 1
    )
    echo   [WARN] Continuing without a detected NVIDIA GPU
    goto gpu_done
)
for /f "tokens=2 delims=:" %%g in ('nvidia-smi -L 2^>nul ^| findstr /i "GPU"') do set GPUNAME=%%g
echo   [OK] NVIDIA drivers found -!GPUNAME!
:gpu_done

:: ----------------------------------------------------------------------------
:: Ensure uv (the Python package/venv manager) is installed
:: ----------------------------------------------------------------------------
echo.
where uv >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] uv already installed
    goto uv_done
)
echo   Installing uv...
winget install --id astral-sh.uv -e --accept-package-agreements --accept-source-agreements >nul 2>&1
:: winget's shim dir and uv's default install dir — add both so this session sees uv.
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo   [FAIL] uv install failed. Install it manually from https://docs.astral.sh/uv/
    echo          then re-run this setup.
    pause
    exit /b 1
)
echo   [OK] uv installed
:uv_done

:: Make sure uv has a Python 3.11 interpreter to build the venvs against.
echo   Ensuring Python 3.11 is available to uv...
uv python install 3.11 >nul 2>&1
echo   [OK] Python 3.11 ready

:: ----------------------------------------------------------------------------
:: Check Node.js (needed to build the web UI)
:: ----------------------------------------------------------------------------
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   WARNING: Node.js not found — the web UI cannot be built without it.
    echo   Install the LTS build from https://nodejs.org then re-run setup.
    echo.
    set /p CONTINUE="   Continue without building the UI? (y/N): "
    if /i not "!CONTINUE!"=="y" ( pause & exit /b 1 )
    set "SKIP_UI=1"
) else (
    for /f "tokens=*" %%v in ('node --version 2^>^&1') do set NODEVER=%%v
    echo   [OK] Node.js !NODEVER! found
)

:: ----------------------------------------------------------------------------
:: Enable Windows + Git long paths (PyTorch/CUDA packages have deep paths)
:: ----------------------------------------------------------------------------
echo.
echo   Enabling Windows long path support...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f >nul 2>&1
if %errorlevel% equ 0 ( echo   [OK] Long paths enabled ) else ( echo   [WARN] Could not enable long paths )
git --version >nul 2>&1
if %errorlevel% equ 0 ( git config --global core.longpaths true >nul 2>&1 & echo   [OK] Git long paths enabled )

:: ----------------------------------------------------------------------------
:: Firewall — allow the OpenVoice port (single-port: backend serves UI + API)
:: ----------------------------------------------------------------------------
echo.
echo   Configuring Windows Firewall...
netsh advfirewall firewall show rule name="OpenVoice" >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Firewall rule already exists
) else (
    netsh advfirewall firewall add rule name="OpenVoice" dir=in action=allow protocol=TCP localport=5601 >nul 2>&1
    if %errorlevel% equ 0 ( echo   [OK] Firewall rule added ^(port 5601 inbound^) ) else ( echo   [WARN] Could not add firewall rule )
)

:: ----------------------------------------------------------------------------
:: Visual C++ Runtime (required by CUDA/PyTorch native libs)
:: ----------------------------------------------------------------------------
echo.
echo   Checking Visual C++ Runtime...
python -c "import ctypes; ctypes.CDLL('msvcp140.dll')" >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Visual C++ Runtime found
    goto vcpp_done
)
echo   Downloading Visual C++ Runtime...
curl -L -o "%TEMP%\vc_redist.x64.exe" "https://aka.ms/vs/17/release/vc_redist.x64.exe" >nul 2>&1
if exist "%TEMP%\vc_redist.x64.exe" (
    "%TEMP%\vc_redist.x64.exe" /install /quiet /norestart
    echo   [OK] Visual C++ Runtime installed
) else (
    echo   [WARN] Could not download VC++ Runtime. Install manually from:
    echo          https://aka.ms/vs/17/release/vc_redist.x64.exe
)
:vcpp_done

:: ----------------------------------------------------------------------------
:: Build the engine venvs from their pinned uv.lock files (exact reproducible
:: installs, CUDA 12.4 torch and all). This is the long step — several minutes.
:: ----------------------------------------------------------------------------
echo.
echo   Building the Chatterbox backend venv (server/) — this may take several minutes...
pushd "%SCRIPT_DIR%server"
uv sync --frozen
if %errorlevel% neq 0 ( echo   [FAIL] backend venv build failed & popd & pause & exit /b 1 )
popd
echo   [OK] Backend venv ready

echo.
echo   Building the VoxCPM design-worker venv (voxcpm/)...
pushd "%SCRIPT_DIR%voxcpm"
uv sync --frozen
if %errorlevel% neq 0 ( echo   [FAIL] voxcpm venv build failed & popd & pause & exit /b 1 )
popd
echo   [OK] VoxCPM venv ready

:: Verify the CUDA runtime DLLs the backend torch actually needs are loadable.
nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    "%SCRIPT_DIR%server\.venv\Scripts\python.exe" -c "import nvidia.cublas, os, ctypes; p=os.path.join(os.path.dirname(nvidia.cublas.__path__[0]),'cublas','bin'); ctypes.CDLL(os.path.join(p,'cublas64_12.dll')); print('OK')" >nul 2>&1
    if !errorlevel! neq 0 (
        echo.
        echo   WARNING: CUDA runtime DLLs ^(cublas64_12.dll^) could not be loaded.
        echo   nvidia-smi sees your GPU, but the full CUDA runtime is missing —
        echo   synthesis will fail. Install the FULL driver from nvidia.com,
        echo   restart, and re-run setup.
        echo.
    ) else (
        echo   [OK] CUDA runtime verified
    )
)

:: ----------------------------------------------------------------------------
:: Build the web UI (served single-port by the backend from web/dist)
:: ----------------------------------------------------------------------------
if defined SKIP_UI goto ui_done
echo.
echo   Building the web UI...
pushd "%SCRIPT_DIR%web"
call npm ci >nul 2>&1
if %errorlevel% neq 0 ( call npm install >nul 2>&1 )
call npm run build
if %errorlevel% neq 0 ( echo   [WARN] UI build failed — check Node version & popd & goto ui_done )
popd
echo   [OK] Web UI built ^(web\dist^)
:ui_done

:: ----------------------------------------------------------------------------
:: Pre-download the core AI models (so first start is instant)
:: ----------------------------------------------------------------------------
echo.
echo   Downloading core AI models (Chatterbox + VoxCPM2, several GB, one-time)...
"%SCRIPT_DIR%server\.venv\Scripts\python.exe" "%SCRIPT_DIR%download_models.py"
if %errorlevel% equ 0 ( echo   [OK] Models downloaded ) else ( echo   [WARN] Model download failed — they will download on first start )

:: ----------------------------------------------------------------------------
:: Remote management tools (optional, --remote flag): Git, Tailscale, SSH, RDP
:: ----------------------------------------------------------------------------
if /i not "%1"=="--remote" if /i not "%2"=="--remote" goto remote_done
echo.
echo   ============================================
echo     Remote Management Tools
echo   ============================================
git --version >nul 2>&1
if %errorlevel% equ 0 ( echo   [OK] Git already installed ) else ( winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements >nul 2>&1 & echo   [OK] Git installed )
tailscale version >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Tailscale already installed
) else (
    curl -L -o "%TEMP%\tailscale.msi" "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi" >nul 2>&1
    msiexec /i "%TEMP%\tailscale.msi" /quiet /norestart >nul 2>&1
    echo   [OK] Tailscale installed — open it from the Start menu and sign in to activate.
)
sc query sshd >nul 2>&1
if %errorlevel% neq 0 ( powershell -Command "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0" >nul 2>&1 )
powershell -Command "Start-Service sshd; Set-Service -Name sshd -StartupType Automatic" >nul 2>&1
echo   [OK] OpenSSH Server enabled and set to auto-start
netsh advfirewall firewall show rule name="OpenSSH-Server" >nul 2>&1
if %errorlevel% neq 0 ( netsh advfirewall firewall add rule name="OpenSSH-Server" dir=in action=allow protocol=TCP localport=22 >nul 2>&1 )
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f >nul 2>&1
netsh advfirewall firewall show rule name="Remote Desktop" >nul 2>&1
if %errorlevel% neq 0 ( netsh advfirewall firewall add rule name="Remote Desktop" dir=in action=allow protocol=TCP localport=3389 >nul 2>&1 )
echo   [OK] Remote Desktop enabled
:remote_done

:: ----------------------------------------------------------------------------
:: Done
:: ----------------------------------------------------------------------------
echo.
echo   ============================================
echo     OpenVoice setup complete!
echo   ============================================
echo.
echo   To start OpenVoice:
echo     openvoice.bat start
echo.
echo   To start with visible logs:
echo     openvoice.bat verbose
echo.
echo   Then open:  http://localhost:5601
echo.
echo   NOTE: On a fresh Windows install you may need to restart once for
echo   long-path support to take effect.
echo.
pause
