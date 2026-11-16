@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
if not exist "scripts\avvia_windows.ps1" (
  echo [ZION] ERRORE: manca scripts\avvia_windows.ps1.
  echo Scarica ed estrai lo ZIP completo della repository.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\avvia_windows.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo [ZION] Avvio terminato con errore %RESULT%. Controlla la cartella logs.
echo Premi un tasto per chiudere.
pause >nul
exit /b %RESULT%
