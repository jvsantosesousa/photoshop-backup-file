@echo off
REM Builds dist\PSDBackup.exe. Run on Windows with Python 3.9+ installed.
python -m pip install -r requirements-dev.txt || exit /b 1
python -m pytest -q || exit /b 1
python -m PyInstaller --noconfirm --onefile --noconsole --name PSDBackup ^
  --hidden-import pystray._win32 main.py || exit /b 1
echo.
echo Built dist\PSDBackup.exe
