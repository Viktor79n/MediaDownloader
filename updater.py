import subprocess

from update_service import APP_VERSION, UpdateError, check_for_update, download_installer


def check_update():
    """Проверяет наличие обновлений"""
    try:
        latest = check_for_update()
    except UpdateError as error:
        print(f"⚠️ {error}")
        return None
    if latest:
        print(f"🔔 Доступна новая версия: {latest['tag_name']}")
    else:
        print(f"✅ У вас последняя версия: {APP_VERSION}")
    return latest


def download_update(release_info):
    """Скачивает обновление"""
    try:
        installer_path = download_installer(release_info)
        print("✅ Обновление скачано!")
        subprocess.run(['open', installer_path], check=False)
        return True
    except UpdateError as error:
        print(f"❌ {error}")
        return False


def main():
    print("=" * 60)
    print(f"🎵 MediaDownloader Updater v{APP_VERSION}")
    print("=" * 60)
    print()

    release = check_update()
    if release:
        print()
        choice = input("Установить обновление? (y/n): ")
        if choice.lower() in ['y', 'yes', 'да', 'д']:
            download_update(release)
    else:
        print()
        input("Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
