@echo off
rem One click: update + rebuild the WSL copy, create a memory card if there is none,
rem then start the emulator with the web panel and open it in the browser.
rem   1. tools\rebuild_wsl.sh  (quick when nothing changed)
rem   2. ~/a7c-emulator/out/sdcard.img, empty 4 GB, only if it does not exist yet
rem   3. python3 tools/a7c_panel.py  ->  http://localhost:8642
rem Extra settings can follow, e.g.:  start_a7c_panel.bat "--env A7C_MODE_DIAL=A"
rem Close this window (or press Ctrl+C) to stop the panel and the emulator.

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
echo === 3/3 Starting the emulator and the panel: http://localhost:8642 ===
echo The camera screen appears in the browser after 7-8 minutes.
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8642"
wsl.exe -e bash -lc "cd ~/a7c-emulator && pkill -x qemu-system-arm; sleep 1; python3 tools/a7c_panel.py %~1"

endlocal
pause
