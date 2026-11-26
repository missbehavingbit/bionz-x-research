@echo off
setlocal
cd /d "%~dp0.."

set "REPO_ROOT=%CD%"
set "GUI_PY=%REPO_ROOT%\.venv-gui\Scripts\python.exe"
set "BASE_PY="

if exist "%REPO_ROOT%\.venv\Scripts\python.exe" set "BASE_PY=%REPO_ROOT%\.venv\Scripts\python.exe"
if not defined BASE_PY if exist "%REPO_ROOT%\venv\Scripts\python.exe" set "BASE_PY=%REPO_ROOT%\venv\Scripts\python.exe"
if not defined BASE_PY (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python 3 was not found on PATH. Install Python 3.11+ and re-run.
        pause
        exit /b 1
    )
    set "BASE_PY=python"
)

set "PY=%BASE_PY%"
if exist "%GUI_PY%" set "PY=%GUI_PY%"

"%PY%" -c "import webview, usb, libusb_package, Cryptodome, elftools, win32file, comtypes, yaml, asn1crypto, axmlparserpy, certifi, tlslite" >nul 2>nul
if not errorlevel 1 goto :launch

:provision
if not exist "%GUI_PY%" (
    echo Creating isolated .venv-gui environment...
    "%BASE_PY%" -m venv "%REPO_ROOT%\.venv-gui"
    if errorlevel 1 (
        echo Failed to create .venv-gui environment.
        pause
        exit /b 1
    )
)

echo Installing Windows GUI dependencies into .venv-gui...
"%GUI_PY%" -m pip install --upgrade pip >nul 2>nul
"%GUI_PY%" -m pip install "pywebview>=5.3,<7" pyusb==1.3.1 "libusb-package>=1.0.26" pycryptodomex==3.24.2 pyelftools==0.33 "pywin32>=308" "comtypes>=1.4.8" PyYAML==6.0.3 "asn1crypto>=1.5.1" axmlparserpy==0.0.4 "certifi>=2024.8.30" "tlslite-ng>=0.7.6"
if errorlevel 1 (
    echo Failed to install GUI dependencies.
    pause
    exit /b 1
)
set "PY=%GUI_PY%"

:launch
"%PY%" -m tools.mod_manager_gui %*
if errorlevel 1 pause
