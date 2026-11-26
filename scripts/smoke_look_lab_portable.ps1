<#
.SYNOPSIS
    Smoke-test the portable BIONZ Look Lab executable from a folder that contains only itself.
.DESCRIPTION
    Copies dist\BIONZ-Look-Lab-portable\bionz-look-lab.exe into an empty folder (C:\Temp\LookLabTest by
    default), starts it there through `cmd /c start` (a plain process launch; no console window is
    attached because the release binary uses the "windows" subsystem) and checks that the process is
    still alive after a few seconds. A missing window, a missing file or an immediate crash shows up as
    a process that is gone before the timeout.

    The preset library and the Export Camera Mod writes are verified headlessly by
    scripts\verify_look_lab_export.ps1 and by `cargo test -p bionz-app`; this script only answers "does
    the GUI start from a bare folder and stay up".
.EXAMPLE
    .\scripts\smoke_look_lab_portable.ps1
#>
[CmdletBinding()]
param(
    [int]$Seconds = 8,
    [string]$Root = "C:\Temp\LookLabTest"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Source = Join-Path $RepoRoot "dist\BIONZ-Look-Lab-portable\bionz-look-lab.exe"
if (-not (Test-Path $Source)) {
    throw "$Source is missing; run scripts\build_look_lab_release.ps1 first"
}
if (Get-Process bionz-look-lab -ErrorAction SilentlyContinue) {
    throw "a bionz-look-lab process is already running; close it first"
}

if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
Copy-Item -Force $Source (Join-Path $Root "bionz-look-lab.exe")
$files = @(Get-ChildItem $Root -File)
if ($files.Count -ne 1) {
    throw "the smoke folder must contain only the executable, found: $($files.Name -join ', ')"
}

# `start` gives the app the caller's handles; without redirection to NUL the PowerShell pipeline would
# wait on the GUI process for as long as it runs. Redirect the launcher and never pipe its output.
$launch = 'cd /d "{0}" && start "" "bionz-look-lab.exe" > nul 2>&1' -f $Root
& cmd.exe /c $launch
Start-Sleep -Seconds $Seconds
$process = @(Get-Process bionz-look-lab -ErrorAction SilentlyContinue)
$alive = $process.Count -gt 0
$withWindow = if ($alive) { @($process | Where-Object { $_.MainWindowHandle -ne 0 }).Count } else { 0 }
$titles = if ($alive) { ($process | Where-Object { $_.MainWindowTitle } | ForEach-Object { $_.MainWindowTitle }) -join '; ' } else { '' }
if ($alive) {
    $process | Stop-Process -Force
    Start-Sleep -Milliseconds 800
}
# The executable stays locked until the process is gone; cleanup must not decide pass or fail.
Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue

if (-not $alive) {
    throw "bionz-look-lab.exe exited before $Seconds s; the portable build failed to start from an empty folder"
}
Write-Host ("PASS: bionz-look-lab.exe ran for {0}s from a folder containing only the executable ({1} window(s) with a main window, title '{2}')." -f `
    $Seconds, $withWindow, $titles) -ForegroundColor Green
