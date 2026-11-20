@echo off
rem Start the A7C emulator in WSL (the copy that rebuild_qemu_wsl.bat keeps in ~/a7c-emulator).
rem Extra settings go in front, e.g.:  run_emulator_wsl.bat "A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on"
wsl.exe -e bash -lc "cd ~/a7c-emulator 2>/dev/null || { echo ~/a7c-emulator not found: run rebuild_qemu_wsl.bat first.; exit 1; }; [ -x qemu/build/qemu-system-arm ] && [ -x qemu/build/qemu-img ] || { echo QEMU is not built yet: run rebuild_qemu_wsl.bat first.; exit 1; }; env %~1 ./run.sh"
pause
