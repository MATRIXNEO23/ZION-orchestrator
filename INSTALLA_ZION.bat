@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "MODE="
if /I "%~1"=="/check" set "MODE=-CheckOnly"
if not "%~1"=="" if not defined MODE (
  echo Uso: INSTALLA_ZION.bat oppure INSTALLA_ZION.bat /check
  exit /b 2
)
if not exist "logs" mkdir "logs"
for /f %%I in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"
if not defined STAMP set "STAMP=ultima_esecuzione"
set "LOG=%CD%\logs\setup_%STAMP%.log"
echo [ZION] Controllo Windows. Log: "%LOG%"
if not exist "scripts\setup_windows.ps1" (
  echo ERRORE: scripts\setup_windows.ps1 mancante. > "%LOG%"
  type "%LOG%"
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\setup_windows.ps1" -LogPath "%LOG%" %MODE%
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo [ZION] Problemi rilevati. Leggi: "%LOG%"
) else (
  echo [ZION] Controllo completato. Log: "%LOG%"
)
echo.
echo Premi un tasto per chiudere.
pause >nul
exit /b %RESULT%
