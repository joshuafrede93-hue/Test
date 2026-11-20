@echo off
cd /d %~dp0
echo === Membangun AsistenPC.exe ===

if not exist .venv\Scripts\activate.bat (
  echo Membuat lingkungan Python...
  python -m venv .venv
  if errorlevel 1 (
    echo Python belum terpasang atau tidak ada di PATH. Pasang Python 3.11 atau 3.12 dari python.org dan centang "Add Python to PATH".
    pause
    exit /b 1
  )
)

call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

echo Membungkus program...
pyinstaller --noconfirm --clean --windowed --name AsistenPC --collect-submodules comtypes --collect-submodules pywinauto --hidden-import pyperclip --hidden-import keyboard main.py
if errorlevel 1 (
  echo Build gagal. Salin pesan error di atas dan kirim ke Claude.
  pause
  exit /b 1
)

if not exist dist\AsistenPC\data mkdir dist\AsistenPC\data
if exist config.json copy /y config.json dist\AsistenPC\config.json >nul

echo.
echo Selesai. Program ada di folder: dist\AsistenPC
echo Jalankan dist\AsistenPC\AsistenPC.exe
echo Seluruh folder dist\AsistenPC bisa disalin ke komputer lain (jangan hanya file .exe-nya).
pause
