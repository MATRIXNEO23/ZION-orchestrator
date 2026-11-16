@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
if not exist "zion_gui.py" (
  echo [ZION] ERRORE: zion_gui.py non trovato.
  echo Scarica lo ZIP completo ed estrailo prima di avviare.
  pause
  exit /b 2
)
where pyw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" /D "%~dp0" pyw -3 "%~dp0zion_gui.py"
  exit /b 0
)
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" /D "%~dp0" pythonw "%~dp0zion_gui.py"
  exit /b 0
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%~dp0zion_gui.py"
  exit /b %ERRORLEVEL%
)
echo [ZION] Python non trovato. Esegui INSTALLA_ZION.bat.
pause
exit /b 1
