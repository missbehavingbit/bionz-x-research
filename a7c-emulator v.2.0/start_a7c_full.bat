@echo off
rem One click: update + rebuild the WSL copy, create a memory card if there is none,
rem then start the emulator in the full mode (camera app, buttons, memory card).
rem   1. tools\rebuild_wsl.sh  (same as rebuild_qemu_wsl.bat; quick when nothing changed)
rem   2. ~/a7c-emulator/out/sdcard.img, empty 4 GB, only if it does not exist yet
rem      (an existing card is kept; create_sdcard_wsl.bat replaces it)
rem   3. A7C_FULL=1 A7C_SD_IMAGE=out/sdcard.img ./run.sh
rem Extra settings can follow, e.g.:  start_a7c_full.bat "A7C_MODE_DIAL=A"
rem The camera screen appears after a few minutes. Keys: see README.md.

setlocal
echo === 1/3 Updating and building QEMU in WSL ===
wsl.exe -e bash -lc "cd \"$(wslpath '%~dp0.')\" && bash tools/rebuild_wsl.sh"
if errorlevel 1 (
    echo.
    echo Rebuild failed; see the messages above. The emulator was not started.
    pause
    exit /b 1
)

echo.
echo === 2/3 Memory card ===
wsl.exe -e bash -lc "cd ~/a7c-emulator && mkdir -p out && if [ -e out/sdcard.img ]; then echo 'Using the existing card out/sdcard.img'; else truncate -s 4G out/sdcard.img && echo 'Created an empty 4 GB card out/sdcard.img (the camera asks to format it)'; fi"
if errorlevel 1 (
    echo Could not create the memory card.
    pause
    exit /b 1
)

echo.
echo === 3/3 Starting the emulator (camera screen after a few minutes) ===
wsl.exe -e bash -lc "cd ~/a7c-emulator && env A7C_FULL=1 A7C_SD_IMAGE=out/sdcard.img %~1 ./run.sh"

endlocal
pause
