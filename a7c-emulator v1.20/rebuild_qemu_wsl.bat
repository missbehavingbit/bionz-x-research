@echo off
rem Update the WSL copy of the A7C emulator from this folder and rebuild QEMU (runs tools\rebuild_wsl.sh in WSL).
rem Optional argument: the WSL path of the copy to update, e.g. ~/a7c-emulator
wsl.exe -e bash -lc "cd \"$(wslpath '%~dp0.')\" && bash tools/rebuild_wsl.sh %*"
if errorlevel 1 echo. & echo Rebuild failed; see the messages above.
pause
