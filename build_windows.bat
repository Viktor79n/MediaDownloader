@echo off
setlocal
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --windowed --onedir --name MediaDownloader ^
  --collect-all PyQt6 --hidden-import PyQt6.QtWebEngineWidgets ^
  --hidden-import PyQt6.QtWebEngineCore --hidden-import yt_dlp main.py
if errorlevel 1 exit /b 1

iscc installer_windows.iss
if errorlevel 1 (
  echo Inno Setup не найден. Установите его с https://jrsoftware.org/isdl.php
  exit /b 1
)

echo Installer: installer_output\MediaDownloader-Setup.exe
