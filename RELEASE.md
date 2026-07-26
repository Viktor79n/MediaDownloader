# Сборка и обновления

## macOS

На macOS выполните:

```bash
source .venv/bin/activate
bash build_macos.sh
```

Установочный образ появится в `dist/MediaDownloader-macOS.dmg`.

## Windows

На Windows установите Python 3.12 и Inno Setup, затем выполните:

```bat
build_windows.bat
```

Готовый установщик: `installer_output\MediaDownloader-Setup.exe`.

## Публикация обновления

1. Создайте GitHub Release с тегом новее версии в `update_service.py`, например `v1.1.2`.
2. Прикрепите к релизу `MediaDownloader-macOS.dmg` и `MediaDownloader-Setup.exe`.
3. Приложение по умолчанию проверяет `Viktor79n/MediaDownloader`. При необходимости
   другой репозиторий можно указать через переменную `MEDIADOWNLOADER_GITHUB_REPOSITORY`.

После этого приложение проверяет GitHub Releases, скачивает установщик своей ОС в папку «Загрузки» и открывает его только после подтверждения пользователя.
