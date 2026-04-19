@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m gpr_lab_pro.app
) else (
    python -m gpr_lab_pro.app
)
pause
