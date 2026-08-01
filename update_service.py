"""Проверка и загрузка обновлений из GitHub Releases."""

import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import requests


APP_VERSION = "1.1.5"
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

    updates_dir = Path.home() / "Downloads" / "MediaDownloader Updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    destination = updates_dir / name
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


def install_macos_update(dmg_path):
    """Заменяет текущую macOS-копию приложения после её закрытия."""
    if platform.system() != "Darwin":
        return False

    dmg = Path(dmg_path).resolve()
    if not dmg.is_file():
        raise UpdateError("Файл обновления не найден.")

    executable = Path(sys.executable).resolve()
    current_app = executable.parents[2] if len(executable.parents) > 2 else None
    if current_app and current_app.suffix == ".app":
        target = current_app
    else:
        target = Path.home() / "Applications" / "MediaDownloader.app"
    helper_file = tempfile.NamedTemporaryFile(
        mode="w", prefix="MediaDownloader-update-", suffix=".sh", delete=False
    )
    helper_path = Path(helper_file.name)
    script = f'''#!/bin/bash
set -euo pipefail
sleep 2
mount_dir="$(mktemp -d /tmp/MediaDownloader-mount.XXXXXX)"
cleanup() {{
  hdiutil detach "$mount_dir" -quiet 2>/dev/null || true
  rmdir "$mount_dir" 2>/dev/null || true
  rm -f "$0"
}}
trap cleanup EXIT
hdiutil attach -nobrowse -readonly -mountpoint "$mount_dir" {shlex.quote(str(dmg))} >/dev/null
source_app="$(find "$mount_dir" -maxdepth 1 -type d -name 'MediaDownloader.app' -print -quit)"
test -n "$source_app"
mkdir -p {shlex.quote(str(target.parent))}
if [ -d {shlex.quote(str(target))} ]; then
  mv {shlex.quote(str(target))} {shlex.quote(str(target))}.previous-$(date +%Y%m%d%H%M%S)
fi
ditto "$source_app" {shlex.quote(str(target))}
open {shlex.quote(str(target))}
'''
    helper_file.write(script)
    helper_file.close()
    helper_path.chmod(0o700)
    subprocess.Popen(["/bin/bash", str(helper_path)], start_new_session=True)
    return True
