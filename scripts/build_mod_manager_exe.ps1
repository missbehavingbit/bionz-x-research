<#
.SYNOPSIS
    1-click Windows standalone packager for Sony BIONZ X Mod Manager.
.DESCRIPTION
    Provisions an isolated .venv-packager environment, caches Sony-PMCA-RE,
    bundles WebView2 + libusb-1.0 + shipped .mod binaries (plus optional MLv2
    and debugger binaries), and builds dist\BionzX-Mod-Manager\ via PyInstaller.
#>
param(
    [switch]$OneFile,
    [string]$Mlv2Dir = "",
    [switch]$IncludeDebuggerBinaries,
    [switch]$SkipPmcaDownload,
    [switch]$SkipReleaseDownload
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Resolve-BasePython {
    foreach ($candidate in @(
        (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
        (Join-Path $RepoRoot "venv\Scripts\python.exe")
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "Python 3.11+ was not found. Install Python and retry."
}

$PackagerPy = Join-Path $RepoRoot ".venv-packager\Scripts\python.exe"
if (-not (Test-Path $PackagerPy)) {
    $BasePy = Resolve-BasePython
    Write-Host "[1/4] Creating isolated .venv-packager environment..."
    & $BasePy -m venv (Join-Path $RepoRoot ".venv-packager")
}

Write-Host "[2/4] Installing GUI & PyInstaller dependencies..."
& $PackagerPy -m pip install --upgrade pip --quiet
& $PackagerPy -m pip install -r (Join-Path $RepoRoot "requirements-gui.txt") --quiet

Write-Host "[3/4] Preparing bundled Sony-PMCA-RE, official MLv2, and Arrivederci v5..."
$PmcaPath = ""
if ($SkipPmcaDownload) {
    $PmcaPath = & $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import find_pmca_dir; p = find_pmca_dir(); print(p or '')"
} else {
    $PmcaPath = & $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import ensure_pmca; print(ensure_pmca())"
}

if ($Mlv2Dir) {
    & $PackagerPy -c "import sys; from pathlib import Path; from src.a6400.mod_manager.tools_bundle import validate_mlv2_build; print(validate_mlv2_build(Path(sys.argv[1]))['path'])" $Mlv2Dir
} elseif (-not $SkipReleaseDownload) {
    $Mlv2Dir = (& $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import ensure_mlv2_release; print(ensure_mlv2_release()['path'])").Trim()
}

$ArrivederciPath = ""
if (-not $SkipReleaseDownload) {
    $ArrivederciPath = (& $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import ensure_arrivederci_v5; print(ensure_arrivederci_v5())").Trim()
} else {
    $ArrivederciPath = (& $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import find_arrivederci_v5; p = find_arrivederci_v5(); print(p or '')").Trim()
}

$env:BIONZX_REPO_ROOT = $RepoRoot
$env:BIONZX_PMCA_DIR = $PmcaPath.Trim()
$env:BIONZX_MLV2_DIR = $Mlv2Dir
$env:BIONZX_ARRIVEDERCI_PATH = $ArrivederciPath
$env:BIONZX_INCLUDE_DEBUGGER_BIN = $(if ($IncludeDebuggerBinaries) { "1" } else { "0" })
$env:BIONZX_ONEFILE = $(if ($OneFile) { "1" } else { "0" })

Write-Host "[4/4] Building standalone Windows executable via PyInstaller..."
& $PackagerPy -m PyInstaller --noconfirm --clean `
    --workpath (Join-Path $RepoRoot "build\mod-manager") `
    --distpath (Join-Path $RepoRoot "dist") `
    (Join-Path $RepoRoot "tools\mod_manager.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE (close any running BionzX-Mod-Manager.exe and retry)."
}

if ($OneFile) {
    Write-Host "`nDone! Standalone EXE: $RepoRoot\dist\BionzX-Mod-Manager.exe"
} else {
    Write-Host "`nDone! Standalone bundle: $RepoRoot\dist\BionzX-Mod-Manager\BionzX-Mod-Manager.exe"
}
