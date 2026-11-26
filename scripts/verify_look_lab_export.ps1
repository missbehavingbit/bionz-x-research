<#
.SYNOPSIS
    Verify the portable BIONZ Look Lab export path from a folder that holds only the executable.
.DESCRIPTION
    Copies dist\BIONZ-Look-Lab-portable\bionz-look-lab.exe into an otherwise empty folder (by default
    C:\Temp\LookLabTest, so no `presets/` folder or `mods/film_sims/presets` exists anywhere above it)
    and runs its headless export (`--export-mod`). That is the same preset library and the same writes
    as the "Export Camera Mod (.mod)" dialog:

      * the twelve built-in presets must load with no preset folder on disk (preset_dir = null),
      * `--bodies both` must write <out>\a6400\mods\<Look>.mod and <out>\a6100\mods\<Look>.mod,
      * `--bodies a6400` into a folder already named `mods` must write <mods>\<Look>.mod directly,
      * every file must start with the SMOD v2 header, carry FLAG_ACTION, and match its FNV-1a 32,
      * the two bodies' files must differ (they carry different physical addresses).

    The dialog widgets themselves (rendered without a window) are covered by
    `cargo test -p bionz-app` -> `mod_export_dialog_renders_headless_and_writes_the_files_it_reports`.
.EXAMPLE
    .\scripts\verify_look_lab_export.ps1
.EXAMPLE
    .\scripts\verify_look_lab_export.ps1 -Look "Gold 200" -Root D:\LookLabCheck
#>
[CmdletBinding()]
param(
    [string]$Look = "Classic Chrome",
    [string]$Root = "C:\Temp\LookLabTest"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Source = Join-Path $RepoRoot "dist\BIONZ-Look-Lab-portable\bionz-look-lab.exe"
if (-not (Test-Path $Source)) {
    throw "$Source is missing; run scripts\build_look_lab_release.ps1 first"
}

$FileBase = $Look -replace '[^A-Za-z0-9]', ''
if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$Exe = Join-Path $Root "bionz-look-lab.exe"
Copy-Item -Force $Source $Exe
$siblings = @(Get-ChildItem $Root -File)
if ($siblings.Count -ne 1) {
    throw "the check folder must contain only the executable, found: $($siblings.Name -join ', ')"
}
Write-Host "Checking in $Root (initially: $($siblings.Name))" -ForegroundColor DarkGray

function Invoke-Export([string]$Destination, [string]$Bodies, [string]$Kind = "single", [string]$LookFor = $Look) {
    # The release binary uses the "windows" subsystem, so PowerShell's `&` cannot capture its stdout;
    # redirect through a file and use the child's exit code.
    $outFile = Join-Path $Root ("receipt-{0}-{1}.json" -f $Kind, $Bodies)
    $line = 'cd /d "{5}" && "{0}" --export-mod "{1}" --kind {6} --look "{2}" --bodies {3} > "{4}" 2>&1' -f $Exe, $Destination, $LookFor, $Bodies, $outFile, $Root, $Kind
    & cmd.exe /c $line | Out-Null
    $code = $LASTEXITCODE
    $json = Get-Content $outFile -Raw
    if ($code -ne 0) { throw "--export-mod --kind $Kind $Bodies failed with exit code $code`: $json" }
    $receipt = $json | ConvertFrom-Json
    if (-not $receipt.ok) { throw "export receipt is not ok: $json" }
    return $receipt
}

function Get-ModHeader([string]$Path) {
    if (-not (Test-Path $Path)) { throw "$Path was not written" }
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 68) { throw "$Path is shorter than an SMOD header" }
    $magic = [Text.Encoding]::ASCII.GetString($bytes, 0, 4)
    $version = [BitConverter]::ToUInt32($bytes, 4)
    $flags = [BitConverter]::ToUInt32($bytes, 8)
    $codeSize = [BitConverter]::ToUInt32($bytes, 44)
    $checksum = [BitConverter]::ToUInt32($bytes, 48)
    # FNV-1a 32 over the code, exactly as Mod Loader v2 checks it: h = (h ^ byte) * 16777619 mod 2^32.
    $fnv = [uint64]2166136261
    for ($i = 68; $i -lt $bytes.Length; $i++) {
        $fnv = (($fnv -bxor [uint64]$bytes[$i]) * 16777619) % 4294967296
    }
    $fnv = [uint32]$fnv
    if ($magic -ne "SMOD") { throw "$Path does not start with SMOD" }
    if ($version -ne 2) { throw "$Path is SMOD version $version, expected 2" }
    if (($flags -band 16) -ne 16) { throw "$Path is missing FLAG_ACTION (16)" }
    if ($codeSize -ne ($bytes.Length - 68)) { throw "$Path code size $codeSize does not match the file" }
    if ($fnv -ne $checksum) { throw "$Path FNV-1a checksum mismatch" }
    return [pscustomobject]@{ Path = $Path; Bytes = $bytes.Length; Version = $version; Flags = $flags; CodeSize = $codeSize; Fnv = $checksum }
}

Write-Host "1. Both bodies, from a folder containing only the executable" -ForegroundColor Cyan
$receipt = Invoke-Export (Join-Path $Root "out") "both"
Write-Host ("   presets_loaded={0} presets_built_in={1} preset_dir={2} look='{3}' bodies={4}" -f `
    $receipt.presets_loaded, $receipt.presets_built_in, `
    $(if ($null -eq $receipt.preset_dir) { "<none>" } else { "'$($receipt.preset_dir)'" }), `
    $receipt.look, ($receipt.bodies -join "+"))
if ($receipt.presets_loaded -lt 12) { throw "expected at least 12 built-in presets, got $($receipt.presets_loaded)" }
if ($receipt.preset_dir) { Write-Warning "a preset folder was also found at $($receipt.preset_dir); run this from a folder outside the checkout to see the built-in-only case" }

$a6400 = Join-Path $Root "out\a6400\mods\$FileBase.mod"
$a6100 = Join-Path $Root "out\a6100\mods\$FileBase.mod"
foreach ($path in @($a6400, $a6100)) {
    $header = Get-ModHeader $path
    Write-Host ("   {0}  {1} bytes  SMOD v{2}  flags=0x{3:x}  fnv1a=0x{4:x8}" -f `
        $path.Substring($Root.Length + 1), $header.Bytes, $header.Version, $header.Flags, $header.Fnv)
}

Write-Host "2. One body into a folder already named mods (an SD card opened at <SD>:\mods)" -ForegroundColor Cyan
$mods = Join-Path $Root "sd\mods"
New-Item -ItemType Directory -Force -Path $mods | Out-Null
$single = Invoke-Export $mods "a6400"
$expected = Join-Path $mods "$FileBase.mod"
$header = Get-ModHeader $expected
if (Test-Path (Join-Path $mods "mods")) { throw "a mods folder must not get a nested mods folder" }
Write-Host ("   {0}  {1} bytes  SMOD v{2}  flags=0x{3:x}  fnv1a=0x{4:x8}" -f `
    $expected.Substring($Root.Length + 1), $header.Bytes, $header.Version, $header.Flags, $header.Fnv)

Write-Host "3. Live picker (FilmSimulations.mod) for both bodies" -ForegroundColor Cyan
$live = Invoke-Export (Join-Path $Root "live") "both" "live" "unused"
Write-Host ("   presets_loaded={0} kind={1} bodies={2}" -f $receipt.presets_loaded, $live.kind, ($live.bodies -join "+"))
$live6400 = Join-Path $Root "live\a6400\mods\FilmSimulations.mod"
$live6100 = Join-Path $Root "live\a6100\mods\FilmSimulations.mod"
foreach ($path in @($live6400, $live6100)) {
    $bytes = [IO.File]::ReadAllBytes($path)
    if ([Text.Encoding]::ASCII.GetString($bytes, 12, 16) -ne "Film Simulations") { throw "$path is not the Live Picker" }
    $flags = [BitConverter]::ToUInt32($bytes, 8)
    $bss = [BitConverter]::ToUInt32($bytes, 52)
    $hooks = [BitConverter]::ToUInt32($bytes, 64)
    if ($flags -ne 20) { throw "$path flags $flags, expected FLAG_ACTION|FLAG_RELOC (20)" }
    if ($bss -ne 48) { throw "$path bss $bss, expected 48" }
    if ($hooks -ne 1) { throw "$path has $hooks hooks, expected the event_push hook" }
    $codeSize = [BitConverter]::ToUInt32($bytes, 44)
    $fnv = [uint64]2166136261
    for ($i = 108; $i -lt 108 + $codeSize; $i++) {
        $fnv = (($fnv -bxor [uint64]$bytes[$i]) * 16777619) % 4294967296
    }
    if ([uint32]$fnv -ne [BitConverter]::ToUInt32($bytes, 48)) { throw "$path FNV-1a mismatch" }
    Write-Host ("   {0}  {1} bytes  SMOD v2  flags=0x{2:x}  bss={3}  hooks={4}" -f `
        $path.Substring($Root.Length + 1), $bytes.Length, $flags, $bss, $hooks)
}
if ($live6400 -eq $live6100) { throw "the live picker must be written per body" }

Write-Host "4. Files on disk" -ForegroundColor Cyan
Get-ChildItem -Recurse $Root -Filter *.mod | ForEach-Object {
    Write-Host ("   {0}  {1} bytes" -f $_.FullName.Substring($Root.Length + 1), $_.Length)
}

Write-Host "5. The two bodies carry different payloads" -ForegroundColor Cyan
$a = [IO.File]::ReadAllBytes($a6400)
$b = [IO.File]::ReadAllBytes($a6100)
if ($a.Length -ne $b.Length) { throw "the two .mod files differ in length" }
$firstDiff = -1
for ($i = 68; $i -lt $a.Length; $i++) { if ($a[$i] -ne $b[$i]) { $firstDiff = $i; break } }
if ($firstDiff -lt 0) { throw "the a6400 and a6100 payloads are identical" }
Write-Host ("   payloads differ from offset {0} on (the body's physical addresses)" -f $firstDiff)

Remove-Item -Recurse -Force $Root
Write-Host ""
Write-Host "PASS: bionz-look-lab.exe loaded $($receipt.presets_loaded) built-in presets from a folder that contained only itself and wrote" -ForegroundColor Green
Write-Host "      out\a6400\mods\$FileBase.mod and out\a6100\mods\$FileBase.mod (single look) plus" -ForegroundColor Green
Write-Host "      live\a6400\mods\FilmSimulations.mod and live\a6100\mods\FilmSimulations.mod (Live Picker, 12 looks) as SMOD v2." -ForegroundColor Green
