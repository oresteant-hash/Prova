@echo off
setlocal
cd /d "%~dp0"
title Atlas Market Regime 3.1

echo Avvio di Atlas Market Regime...
where py >nul 2>nul
if %errorlevel%==0 (
  start "Atlas server" /min py app.py
) else (
  where python >nul 2>nul
  if errorlevel 1 goto :no_python
  start "Atlas server" /min python app.py
)

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
echo.
echo Atlas e stato aperto nel browser.
echo Per riaprirlo usa: http://127.0.0.1:8765
echo.
pause
exit /b 0

:no_python
echo.
echo ERRORE: Python non e installato o non e presente nel PATH.
echo Scaricalo da https://www.python.org/downloads/ e seleziona
ECHO l'opzione "Add Python to PATH" durante l'installazione.
echo.
pause
exit /b 1
