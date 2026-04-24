@echo off
setlocal
cd /d %~dp0
set RELEASE_NAME=GPR_Lab_Pro_V4
set DIST_ROOT=%TEMP%\gpr_dist_v4
set WORK_ROOT=%TEMP%\gpr_build_v4
set RELEASE_ROOT=release

if not exist .venv\Scripts\python.exe (
  echo [ERROR] Missing virtual environment: .venv
  exit /b 1
)

if not exist .venv\Scripts\pyinstaller.exe (
  call .venv\Scripts\python.exe -m pip install pyinstaller
  if errorlevel 1 exit /b 1
)

if exist "%DIST_ROOT%" rmdir /s /q "%DIST_ROOT%"
if exist "%WORK_ROOT%" rmdir /s /q "%WORK_ROOT%"

call .venv\Scripts\pyinstaller.exe --noconfirm --clean --distpath %DIST_ROOT% --workpath %WORK_ROOT% GPR_V11_Pyside.spec
if errorlevel 1 exit /b 1

if not exist %RELEASE_ROOT% mkdir %RELEASE_ROOT%
if exist %RELEASE_ROOT%\%RELEASE_NAME% rmdir /s /q %RELEASE_ROOT%\%RELEASE_NAME%
xcopy /e /i /y %DIST_ROOT%\%RELEASE_NAME% %RELEASE_ROOT%\%RELEASE_NAME% >nul
copy /y qt.conf %RELEASE_ROOT%\%RELEASE_NAME%\qt.conf >nul
copy /y release_launcher.bat %RELEASE_ROOT%\%RELEASE_NAME%\release_launcher.bat >nul
copy /y RELEASE_INSTRUCTIONS.txt %RELEASE_ROOT%\%RELEASE_NAME%\RELEASE_INSTRUCTIONS.txt >nul
if exist online_map.local.json copy /y online_map.local.json %RELEASE_ROOT%\%RELEASE_NAME%\online_map.local.json >nul
if exist ..\offline_tiles xcopy /e /i /y ..\offline_tiles %RELEASE_ROOT%\%RELEASE_NAME%\offline_tiles >nul
powershell -NoProfile -Command "if (Test-Path '%RELEASE_ROOT%\\%RELEASE_NAME%.zip') { Remove-Item -LiteralPath '%RELEASE_ROOT%\\%RELEASE_NAME%.zip' -Force }; Compress-Archive -Path '%RELEASE_ROOT%\\%RELEASE_NAME%' -DestinationPath '%RELEASE_ROOT%\\%RELEASE_NAME%.zip'"

echo Release package ready:
echo   %cd%\%RELEASE_ROOT%\%RELEASE_NAME%
echo   %cd%\%RELEASE_ROOT%\%RELEASE_NAME%.zip
endlocal
