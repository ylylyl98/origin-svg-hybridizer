@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating the local Python environment...
  py -m venv .venv
  if errorlevel 1 goto :error
)

echo Installing build tools...
".venv\Scripts\python.exe" -m pip install -e . "pyinstaller>=6.16,<7"
if errorlevel 1 goto :error

echo Building Origin SVG Hybridizer...
".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name "Origin SVG Hybridizer" ^
  --icon "packaging\origin-svg-hybridizer.ico" ^
  --add-data "packaging\origin-svg-hybridizer.ico;packaging" ^
  --version-file "packaging\version_info.txt" ^
  --paths "src" ^
  "packaging\windows_launcher.py"
if errorlevel 1 goto :error

echo.
echo Build complete:
echo   dist\Origin SVG Hybridizer\Origin SVG Hybridizer.exe
start "" "dist\Origin SVG Hybridizer"
exit /b 0

:error
echo.
echo The Windows build did not finish. Review the message above.
pause
exit /b 1
