@echo off
title BIONZ X Film Sim Studio (ILCE-6100 / ILCE-6400)
rem This launcher lives in scripts/; tools/ stays at the repository root (%~dp0..).
if exist "%~dp0..\venv\Scripts\python.exe" (
    "%~dp0..\venv\Scripts\python.exe" "%~dp0..\tools\film_sim_studio.py" %*
) else (
    python "%~dp0..\tools\film_sim_studio.py" %*
)
