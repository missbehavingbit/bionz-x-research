<#
.SYNOPSIS
    Build the portable BIONZ Look Lab release (bionz-look-lab.exe + README.txt + zip).
.DESCRIPTION
    Runs the Rust workspace tests, builds `bionz-app` in release mode with the statically linked
    MSVC C runtime (isp-studio/.cargo/config.toml), copies the executable into
    dist\BIONZ-Look-Lab-portable\, writes the SD-card instructions, zips the folder, and checks that
    the executable does not import the Visual C++ runtime DLLs.

    The executable is self-contained: the twelve shipped presets and the ARM SMOD v2 template are
    compiled in, so it works from an empty folder with no Python, GCC, WSL or preset files.
.EXAMPLE
    .\scripts\build_look_lab_release.ps1
.EXAMPLE
    .\scripts\build_look_lab_release.ps1 -SkipTests
#>
[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Studio = Join-Path $RepoRoot "isp-studio"
$Dist = Join-Path $RepoRoot "dist"
$Bundle = Join-Path $Dist "BIONZ-Look-Lab-portable"
$Exe = Join-Path $Bundle "bionz-look-lab.exe"
$Zip = Join-Path $Dist "BIONZ-Look-Lab-win64-portable.zip"

function Get-PeImports([string]$Path) {
    $objdump = Get-Command llvm-objdump -ErrorAction SilentlyContinue
    if ($objdump) {
        $lines = & $objdump.Source -p $Path 2>$null
        $names = @($lines | ForEach-Object { if ($_ -match 'DLL Name:\s*(\S+)') { $Matches[1] } })
        if ($names.Count -gt 0) { return $names }
    }
    # Fallback without llvm-objdump: the PE import directory names are plain ASCII in the image.
    $text = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($Path))
    return @([regex]::Matches($text, '(?i)[A-Za-z0-9_\-\.]+\.dll') | ForEach-Object { $_.Value } | Sort-Object -Unique)
}

Write-Host "[1/5] Rust workspace tests" -ForegroundColor Cyan
Push-Location $Studio
try {
    if ($SkipTests) {
        Write-Host "      skipped (-SkipTests)"
    } else {
        & cargo test --workspace --exclude bionz-py
        if ($LASTEXITCODE -ne 0) { throw "cargo test --workspace --exclude bionz-py failed with exit code $LASTEXITCODE" }
    }

    Write-Host "[2/5] cargo build --release -p bionz-app" -ForegroundColor Cyan
    & cargo build --release -p bionz-app
    if ($LASTEXITCODE -ne 0) { throw "cargo build --release failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$TargetRoot = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $Studio "target" }
$Built = Join-Path $TargetRoot "release\bionz-look-lab.exe"
if (-not (Test-Path $Built)) { throw "expected $Built; did the binary target get renamed?" }

Write-Host "[3/5] Bundle into dist\BIONZ-Look-Lab-portable" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Bundle | Out-Null
Copy-Item -Force $Built $Exe

Write-Host "[4/5] README.txt" -ForegroundColor Cyan
$Readme = @'
BIONZ Look Lab - portable Windows build
=======================================

bionz-look-lab.exe is self-contained. Double-click it; there is no installer, no console window,
and nothing to install. The twelve shipped film-simulation looks and the ARM mod template are
compiled into the executable, so it needs no preset folder, Python, GCC or WSL next to it.

Using the looks in the app
--------------------------
Open a photo or a Sony ARW (Ctrl+O, or drag it onto the window), pick a look on the left, and move
the sliders. Compare side B is the stock camera rendering, or a real camera JPEG you add with
"Add camera JPEG".

Putting a look on the camera
----------------------------
1. Click "Export Camera Mod (.mod)" in the toolbar.
2. Pick the mod kind:
     Single look      -> one .mod per look, switched on/off in MENU -> Mods
     Live picker      -> one FilmSimulations.mod with every look; it opens in Live View and
                         Left/Right (or Up/Down) switches the look live, ENTER locks it
3. Pick the camera body:
     a6400 + a6100  -> writes <folder>\a6400\mods\... and <folder>\a6100\mods\...
     one body only  -> writes <folder>\mods\..., or <folder>\... if the folder you chose is
                       already named "mods" (for example <SD>:\mods)
4. Choose the destination folder, or the SD card root (for example E:\), and click "Export .mod".
5. If you exported to a folder other than the card, copy the .mod file to <SD>:\mods\ (create the
   folder if it does not exist). One .mod file per look; keep the file name as generated.
6. Put the card back into the camera, start Mod Loader v2, open MENU -> Mods and switch the look on.

Important
---------
* Turning the look off writes the stock tables back to RAM. Do not delete or rename the .mod on the
  card while the look is enabled.
* The .mod targets ILCE-6400 Ver.2.00 (a6400) and ILCE-6100 Ver.1.00 (a6100). Exporting for the
  wrong body writes the right tables to the wrong physical addresses.
* A look built with a Color Mode base matrix (Movie/Arrivederci presets) is refused: those looks
  need mods/arri_isp/arrivederci, not the Film Sims payload.
* Nothing here talks to the camera. Files are written to the folder you choose, nowhere else.

Exporting without the window (optional)
---------------------------------------
The same export runs from a command prompt:

    bionz-look-lab.exe --export-mod <folder> [--kind single|live] [--look "Gold 200"] [--bodies both|a6400|a6100]

It prints a JSON receipt with the files it wrote. Without a recognised flag the app starts normally.

Regenerating the ARM templates (developers)
-------------------------------------------
The SMOD v2 payloads are built once and committed under isp-studio/crates/bionz-preset/assets/:
* film_sim_template.blob (+ .json):
    wsl.exe -d Ubuntu -- bash mods/film_sims/template/build_template_wsl.sh
* film_sim_live_template.mod (+ .json):
    wsl.exe -d Ubuntu -- bash mods/film_sims/live_picker/build_live_template_wsl.sh

Rebuild them only when the C template sources change.
'@
Set-Content -Path (Join-Path $Bundle "README.txt") -Value $Readme -Encoding ASCII

Write-Host "[5/5] Zip and portability checks" -ForegroundColor Cyan
if (Test-Path $Zip) { Remove-Item -Force $Zip }
# A freshly linked executable can still be held for a moment by the antivirus/indexer; retry instead
# of failing the release on a transient sharing violation.
$zipped = $false
for ($attempt = 1; $attempt -le 6 -and -not $zipped; $attempt++) {
    try {
        Compress-Archive -Path (Join-Path $Bundle '*') -DestinationPath $Zip -ErrorAction Stop
        $zipped = $true
    } catch {
        if ($attempt -eq 6) { throw }
        Write-Host "      zip attempt $attempt failed ($($_.Exception.Message)); retrying"
        Start-Sleep -Seconds 2
    }
}

$imports = Get-PeImports $Exe
$forbidden = @($imports | Where-Object { $_ -match '(?i)^(vcruntime|msvcp|msvcr|concrt|vccorlib)' })
if ($forbidden.Count -gt 0) {
    throw "bionz-look-lab.exe still imports $($forbidden -join ', '); the CRT is not statically linked"
}
$sizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
$sha = (Get-FileHash -Algorithm SHA256 $Exe).Hash.ToLower()
$zipSizeMb = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
$zipSha = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()
$ShaFile = Join-Path $Dist "SHA256SUMS.txt"
Set-Content -Path $ShaFile -Value @(
    "$sha  bionz-look-lab.exe"
    "$zipSha  BIONZ-Look-Lab-win64-portable.zip"
) -Encoding ASCII

Write-Host ""
Write-Host "DLL imports: $($imports -join ', ')" -ForegroundColor DarkGray
Write-Host "No Visual C++ runtime DLL is imported." -ForegroundColor Green
Write-Host ""
Write-Host "Exe:  $Exe ($sizeMb MB)" -ForegroundColor Green
Write-Host "Zip:  $Zip ($zipSizeMb MB)" -ForegroundColor Green
Write-Host "SHA256 (exe): $sha"
Write-Host "SHA256 (zip): $zipSha"
Write-Host "Checksums:    $ShaFile"
