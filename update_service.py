"""Проверка и загрузка обновлений из GitHub Releases."""

import os
import platform
import re
from pathlib import Path

import requests


APP_VERSION = "1.1.1"
GITHUB_REPOSITORY = os.environ.get(
    "MEDIADOWNLOADER_GITHUB_REPOSITORY", "Viktor79n/MediaDownloader"
)
GITHUB_API_URL = "https://api.github.com/repos/{repository}/releases/latest"


class UpdateError(RuntimeError):
    """Обновление не удалось проверить или скачать."""


def version_key(version):
    """Сравнивает версии вида v1.2.3 без ошибочного строкового сравнения."""
    numbers = re.findall(r"\d+", version.lstrip("vV"))
    return tuple(int(number) for number in (numbers + ["0", "0", "0"])[:3])


def check_for_update(repository=GITHUB_REPOSITORY):
    """Возвращает данные релиза, если его версия новее текущей."""
    if not repository or repository.startswith("YOUR_"):
        raise UpdateError(
            "Не задан GitHub-репозиторий. Укажите переменную "
            "MEDIADOWNLOADER_GITHUB_REPOSITORY, например user/MediaDownloader."
        )

    try:
        response = requests.get(
            GITHUB_API_URL.format(repository=repository),
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        response.raise_for_status()
        release = response.json()
    except requests.RequestException as error:
        raise UpdateError(f"Не удалось проверить обновления: {error}") from error

    latest_version = release.get("tag_name", "").lstrip("vV")
    if not latest_version:
        raise UpdateError("В последнем GitHub Release не указан номер версии.")
    return release if version_key(latest_version) > version_key(APP_VERSION) else None


def select_installer(release):
    """Выбирает установочный файл релиза для текущей операционной системы."""
    suffixes = (".dmg",) if platform.system() == "Darwin" else (".exe", ".msi")
    for asset in release.get("assets", []):
        name = asset.get("name", "").lower()
        if name.endswith(suffixes):
            return asset
    raise UpdateError("В релизе нет установщика для этой операционной системы.")


def download_installer(release, progress_callback=None):
    """Скачивает подходящий установщик в папку «Загрузки» и возвращает его путь."""
    asset = select_installer(release)
    url = asset.get("browser_download_url")
    name = Path(asset.get("name", "MediaDownloader-update")).name
    if not url:
        raise UpdateError("У установщика отсутствует ссылка для скачивания.")

    destination = Path.home() / "Downloads" / name
    temporary = destination.with_suffix(destination.suffix + ".part")
    total_size = int(asset.get("size") or 0)

    try:
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            downloaded = 0
            with temporary.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    file.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size:
                        progress_callback(int(downloaded * 100 / total_size))
        temporary.replace(destination)
    except (OSError, requests.RequestException) as error:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось скачать обновление: {error}") from error

    return str(destination)
