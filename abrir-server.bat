@echo off
cd /d "%~dp0"
python server.py
if errorlevel 1 (
  echo.
  echo No se pudo abrir el servidor.
  pause
)
