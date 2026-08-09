@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: creating the local Python environment...
  py -m venv .venv
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 goto :error
)
start "Origin SVG Hybridizer" ".venv\Scripts\pythonw.exe" -m origin_hybridizer
exit /b 0

:error
echo.
echo Setup did not finish. Make sure Python 3.10 or newer is installed,
echo then run this file again.
pause
exit /b 1
