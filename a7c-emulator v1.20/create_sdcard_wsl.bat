@echo off
rem Creates an empty memory card image for the emulator: out\sdcard.img in the WSL copy
rem (~/a7c-emulator/out/sdcard.img). The camera formats it itself ("Format?" -> Enter).
rem Usage:  create_sdcard_wsl.bat          (4 GB)
rem         create_sdcard_wsl.bat 8G       (other size: 1G .. 32G)
rem Start the emulator with the card:
rem         run_emulator_wsl.bat "A7C_FULL=1 A7C_SD_IMAGE=out/sdcard.img"

setlocal
set "SIZE=%~1"
if "%SIZE%"=="" set "SIZE=4G"

wsl.exe -e bash -lc "cd ~/a7c-emulator 2>/dev/null || { echo ~/a7c-emulator not found: run rebuild_qemu_wsl.bat first.; exit 1; }; case '%SIZE%' in [1-9]G|[1-3][0-9]G) ;; *) echo 'Size must be 1G..32G, e.g. 4G'; exit 1;; esac; mkdir -p out; f=out/sdcard.img; if [ -e $f ]; then read -r -p \"$f already exists (the card's contents). Replace it with an empty card? [y/N] \" a; case $a in y|Y) ;; *) echo 'Kept the existing card.'; exit 0;; esac; fi; rm -f $f && truncate -s %SIZE% $f && echo \"Created empty card $f (%SIZE%) in ~/a7c-emulator.\" && echo 'Start with: run_emulator_wsl.bat \"A7C_FULL=1 A7C_SD_IMAGE=out/sdcard.img\"'"

endlocal
pause
