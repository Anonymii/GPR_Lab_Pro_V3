@echo off
setlocal
cd /d %~dp0

set EXE_NAME=GPR_Lab_Pro_V3.exe

if not exist "%EXE_NAME%" (
  echo [ERROR] Missing %EXE_NAME%
  pause
  exit /b 1
)

if not exist "_internal" (
  echo [ERROR] Missing _internal folder.
  echo Please fully extract the entire release package before launching the software.
  pause
  exit /b 1
)

start "" "%~dp0%EXE_NAME%"
endlocal
