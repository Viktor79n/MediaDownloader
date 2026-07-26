#!/bin/bash
set -euo pipefail

echo "Сборка MediaDownloader для macOS..."

cd "$(cd "$(dirname "$0")" && pwd)"

python -m PyInstaller --noconfirm MediaDownloader.spec
hdiutil create -volname "MediaDownloader" -srcfolder "dist/MediaDownloader.app" \
    -ov -format UDZO "dist/MediaDownloader-macOS.dmg"
echo "✅ Установочный файл: dist/MediaDownloader-macOS.dmg"
