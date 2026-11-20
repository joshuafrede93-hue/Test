@echo off
cd /d %~dp0
echo Membuat lingkungan Python...
python -m venv .venv
if errorlevel 1 (
  echo Python belum terpasang atau tidak ada di PATH. Pasang Python 3.11 atau 3.12 dari python.org dan centang "Add Python to PATH".
  pause
  exit /b 1
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Selesai. Jalankan run.bat untuk memulai.
pause
