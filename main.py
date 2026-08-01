import sys
import os
import re
import datetime
import shutil
from urllib.parse import parse_qs, quote_plus, urlsplit
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                             QVBoxLayout, QPushButton, QLabel, QComboBox,
                             QProgressBar, QFrame, QLineEdit, QScrollArea,
                             QMessageBox, QInputDialog, QProgressDialog)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QSettings, QTimer
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
from update_service import (
    APP_VERSION, GITHUB_REPOSITORY, UpdateError, check_for_update,
    download_installer, install_macos_update,
)


class DownloadCancelled(Exception):
    """Служебное исключение для немедленной остановки yt-dlp."""

# ============================================================================== 
# Пути QtWebEngine для запуска из исходников и из PyInstaller-сборки на macOS.
# Их нужно задать до создания QApplication.
# ============================================================================== 
if getattr(sys, 'frozen', False):
    qt_root = os.path.join(getattr(sys, '_MEIPASS', ''), 'PyQt6', 'Qt6')
else:
    import PyQt6
    qt_root = os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6')

webengine_framework = os.path.join(qt_root, 'lib', 'QtWebEngineCore.framework')
webengine_process_paths = (
    os.path.join(
        webengine_framework, 'Helpers', 'QtWebEngineProcess.app',
        'Contents', 'MacOS', 'QtWebEngineProcess'
    ),
    os.path.join(
        webengine_framework, 'Versions', 'Resources', 'Helpers',
        'QtWebEngineProcess.app', 'Contents', 'MacOS', 'QtWebEngineProcess'
    ),
)
webengine_resources_paths = (
    os.path.join(webengine_framework, 'Resources'),
    os.path.join(webengine_framework, 'Versions', 'Resources', 'Resources'),
)

webengine_process_path = next(
    (path for path in webengine_process_paths if os.path.isfile(path)), None
)
webengine_resources_path = next(
    (
        path for path in webengine_resources_paths
        if os.path.isfile(os.path.join(path, 'qtwebengine_resources.pak'))
    ),
    None,
)

if webengine_process_path:
    os.environ['QTWEBENGINEPROCESS_PATH'] = webengine_process_path
if webengine_resources_path:
    os.environ['QTWEBENGINE_RESOURCES_PATH'] = webengine_resources_path

# На данной версии macOS QtWebEngine запускается только без Chromium sandbox.
# Пользователь уже подтвердил это диагностическим запуском из терминала.
if sys.platform == 'darwin':
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--no-sandbox')


def sanitize_filename(filename):
    """Очищает имя файла от недопустимых символов"""
    if not filename or filename.strip() == "":
        filename = "video"

    filename = re.sub(r'[\U00010000-\U0010ffff]', '', filename)
    filename = re.sub(r'[<>:"/\\|?*@\[\]]', '_', filename)
    filename = re.sub(r'\s+', ' ', filename).strip()

    if not filename or filename.strip() == "_" or filename.strip() == "":
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"video_{timestamp}"

    if len(filename) > 100:
        filename = filename[:100]

    return filename


def get_download_url(url):
    """Отделяет выбранное YouTube-видео от альбома, радио и плейлиста."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or '').lower()
    if hostname == 'youtu.be':
        video_id = parsed.path.strip('/').split('/')[0]
    elif hostname == 'youtube.com' or hostname.endswith('.youtube.com'):
        video_id = parse_qs(parsed.query).get('v', [''])[0]
    else:
        return url

    if video_id:
        return f'https://www.youtube.com/watch?v={video_id}'
    return url


class AdBlocker(QWebEngineUrlRequestInterceptor):
    """Блокировщик рекламы"""

    def __init__(self):
        super().__init__()
        self.ad_patterns = [
            b'doubleclick.net',
            b'adservice.google',
            b'googlesyndication',
            b'googleadservices',
            b'googletagmanager',
            b'google-analytics.com',
        ]

    def interceptRequest(self, info):
        url = bytes(info.requestUrl().toEncoded()).lower()
        for pattern in self.ad_patterns:
            if pattern in url:
                info.block(True)
                return


class DownloadItem(QWidget):
    """Виджет элемента загрузки"""

    def __init__(self, title, url, save_path, parent=None):
        super().__init__(parent)
        self.url = url
        self.save_path = save_path
        self.setup_ui(title)

    def setup_ui(self, title):
        self.setFixedHeight(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.title_label = QLabel(title[:60] + "..." if len(title) > 60 else title)
        self.title_label.setStyleSheet("color: #fff; font-weight: bold; font-size: 13px;")
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumWidth(280)
        layout.addWidget(self.title_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setFormat("Подготовка...")
        self.progress_bar.setMinimumHeight(20)
        layout.addWidget(self.progress_bar)

        btn_layout = QHBoxLayout()

        self.open_folder_btn = QPushButton("📁 Папка")
        self.open_folder_btn.setObjectName("openFolderBtn")
        self.open_folder_btn.setFixedHeight(32)
        self.open_folder_btn.setFixedWidth(80)
        btn_layout.addWidget(self.open_folder_btn)

        btn_layout.addStretch()

        self.stop_btn = QPushButton("⏹ Стоп")
        self.stop_btn.setObjectName("stopDownloadBtn")
        self.stop_btn.setFixedHeight(32)
        self.stop_btn.setFixedWidth(80)
        btn_layout.addWidget(self.stop_btn)

        layout.addLayout(btn_layout)


class DownloadThread(QThread):
    """Поток загрузки"""
    progress = pyqtSignal(int, str, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()
    title_received = pyqtSignal(str)

    def __init__(self, url, format_type, save_path, content_type="Видео/Клип"):
        super().__init__()
        self.url = url
        self.format_type = format_type
        self.save_path = save_path
        self.content_type = content_type
        self._is_cancelled = False

    def run(self):
        try:
            import yt_dlp

            os.makedirs(self.save_path, exist_ok=True)

            print(f"\n{'=' * 70}")
            print(f"[ЗАГРУЗКА] URL: {self.url}")
            print(f"[ЗАГРУЗКА] Тип: {self.content_type}")
            print(f"[ЗАГРУЗКА] Формат: {self.format_type}")
            print(f"[ЗАГРУЗКА] Папка: {self.save_path}")
            print(f"{'=' * 70}")

            if '4K HDR' in self.format_type:
                fmt = (
                    'bestvideo[height<=2160][dynamic_range=HDR]+bestaudio/'
                    'bestvideo[height<=2160]+bestaudio/best[height<=2160]'
                )
                print("[ЗАГРУЗКА] 📺 4K HDR (если есть у источника)")
            elif '4K UHD' in self.format_type:
                fmt = (
                    'bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height<=2160]+bestaudio/best[height<=2160]'
                )
                print("[ЗАГРУЗКА] 📺 4K UHD до 2160p")
            elif '3D' in self.format_type:
                fmt = (
                    'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
                )
                print("[ЗАГРУЗКА] 🕶️ Исходное 3D-видео (если оно есть у источника)")
            elif '720p' in self.format_type:
                fmt = (
                    'bestvideo[vcodec^=avc][height<=720][ext=mp4]+'
                    'bestaudio[acodec^=mp4a][ext=m4a]/'
                    'best[vcodec^=avc][height<=720][ext=mp4]/best[height<=720]'
                )
                print("[ЗАГРУЗКА] 📺 Старый ТВ: H.264 MP4 до 720p")
            elif 'Авто-Формат (магнитола' in self.format_type:
                fmt = (
                    'bestvideo[vcodec^=avc][height<=1024][ext=mp4]+'
                    'bestaudio[acodec^=mp4a][ext=m4a]/'
                    'best[vcodec^=avc][height<=1024][ext=mp4]/best[height<=1024]'
                )
                print(f"[ЗАГРУЗКА] 🚗 Авто-формат: магнитола 10' (до 1024p)")
            elif '1080p' in self.format_type:
                fmt = (
                    'bestvideo[vcodec^=avc][height<=1080][ext=mp4]+'
                    'bestaudio[acodec^=mp4a][ext=m4a]/'
                    'best[vcodec^=avc][height<=1080][ext=mp4]/best[height<=1080]'
                )
                print("[ЗАГРУЗКА] 📺 Совместимый MP4 до 1080p")
            elif 'MP3' in self.format_type or 'Аудио' in self.format_type:
                fmt = 'bestaudio/best'
                print(f"[ЗАГРУЗКА] 🎵 MP3 аудио")
            else:
                fmt = (
                    'bestvideo[vcodec^=avc][ext=mp4]+'
                    'bestaudio[acodec^=mp4a][ext=m4a]/'
                    'best[vcodec^=avc][ext=mp4]/best[ext=mp4]/best'
                )
                print("[ЗАГРУЗКА] 🏆 Максимальное качество, совместимое с QuickTime")

            output_template = os.path.join(self.save_path, '%(title).180B [%(id)s].%(ext)s')

            ydl_opts = {
                'format': fmt,
                'outtmpl': output_template,
                'noplaylist': True,
                'playlist_items': '1',
                'continuedl': True,
                'retries': 5,
                'fragment_retries': 5,
                'socket_timeout': 30,
                'http_headers': {
                    'User-Agent': (
                        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/131.0.0.0 Safari/537.36'
                    ),
                    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                },
                'progress_hooks': [self._progress_hook],
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'ios', 'android', 'mweb'],
                    }
                },
            }

            ffmpeg_path = self._find_ffmpeg()
            if ffmpeg_path:
                ydl_opts['ffmpeg_location'] = ffmpeg_path

            if 'MP3' not in self.format_type and 'Аудио' not in self.format_type:
                ydl_opts['merge_output_format'] = 'mp4'
            else:
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                }]

            print("[ЗАГРУЗКА] 🔒 Будет загружено только выбранное видео")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                print(f"[ЗАГРУЗКА]  Получаю информацию...")
                self.progress.emit(0, "", "Получение информации...")

                info = ydl.extract_info(self.url, download=False)

                if isinstance(info, dict):
                    video_title = info.get('title', 'video')
                else:
                    video_title = 'video'

                safe_title = sanitize_filename(video_title)

                print(f"[ЗАГРУЗКА] 📹 Название: {safe_title}")
                self.title_received.emit(safe_title)

                print(f"[ЗАГРУЗКА] ⬇️ Начинаю скачивание...")
                self.progress.emit(5, "", "Начало загрузки...")

                result = ydl.download([self.url])
                print(f"[ЗАГРУЗКА] ✅ Результат: {result}")

            print(f"[ЗАГРУЗКА] 🎉 Завершено!")
            self.finished.emit(safe_title)

        except DownloadCancelled:
            print("[ЗАГРУЗКА] 🚫 Отменено пользователем")
            self.cancelled.emit()
        except Exception as e:
            if self._is_cancelled:
                print("[ЗАГРУЗКА] 🚫 Отменено пользователем")
                self.cancelled.emit()
                return
            error_msg = str(e)
            print(f"[ЗАГРУЗКА] ❌ Ошибка: {error_msg}")
            import traceback
            traceback.print_exc()
            self.error.emit(error_msg)

    @staticmethod
    def _find_ffmpeg():
        """Находит FFmpeg как в собранном приложении, так и в PATH."""
        candidates = []
        if getattr(sys, 'frozen', False):
            candidates.append(os.path.join(getattr(sys, '_MEIPASS', ''), 'ffmpeg'))
        candidates.append(shutil.which('ffmpeg'))
        return next((path for path in candidates if path and os.path.isfile(path)), None)

    def _progress_hook(self, d):
        """Обработка прогресса загрузки"""
        if self._is_cancelled:
            raise DownloadCancelled()

        try:
            status = d.get('status', '')

            if status == 'downloading':
                percent_str = d.get('_percent_str', '0%').strip()
                speed_str = d.get('_speed_str', 'N/A').strip()
                eta_str = d.get('_eta_str', 'N/A').strip()

                percent_clean = percent_str.replace('%', '').strip()
                try:
                    percent = int(float(percent_clean))
                except:
                    percent = 0

                print(f"[ПРОГРЕСС] {percent}% | {speed_str} | ETA: {eta_str}")
                self.progress.emit(percent, speed_str, eta_str)

            elif status == 'finished':
                print("[ПРОГРЕСС] ⏳ Обработка...")
                self.progress.emit(95, "", "Конвертация...")

        except DownloadCancelled:
            raise
        except Exception as e:
            print(f"[ПРОГРЕСС] Ошибка: {e}")

    def cancel(self):
        print("[ЗАГРУЗКА] 🚫 Отмена...")
        self._is_cancelled = True


class UpdateCheckThread(QThread):
    """Не блокирует интерфейс во время проверки GitHub Release."""
    checked = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, repository):
        super().__init__()
        self.repository = repository

    def run(self):
        try:
            self.checked.emit(check_for_update(self.repository))
        except UpdateError as error:
            self.error.emit(str(error))


class UpdateDownloadThread(QThread):
    """Скачивает установщик обновления в фоне."""
    progress = pyqtSignal(int)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, release):
        super().__init__()
        self.release = release

    def run(self):
        try:
            self.completed.emit(download_installer(self.release, self.progress.emit))
        except UpdateError as error:
            self.error.emit(str(error))


class VideoApp(QMainWindow):
    """Главное окно приложения"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MediaDownloader Pro")
        self.resize(1600, 1000)

        self.download_path = os.path.expanduser("~/Downloads/MediaDownloader")
        os.makedirs(self.download_path, exist_ok=True)
        self.movies_path = os.path.join(self.download_path, 'Фильмы')
        os.makedirs(self.movies_path, exist_ok=True)
        self.clips_path = os.path.join(self.download_path, 'Клипы')
        os.makedirs(self.clips_path, exist_ok=True)

        self.active_downloads = {}
        self.download_items = []
        self.settings = QSettings("MediaDownloader", "MediaDownloader")
        self.update_check_thread = None
        self.update_download_thread = None
        self.update_progress_dialog = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        left_panel = self._create_left_panel()
        main_layout.addWidget(left_panel)

        right_panel = self._create_browser_panel()
        main_layout.addWidget(right_panel, stretch=1)

        self._apply_styles()
        self._setup_shortcuts()

    def _create_left_panel(self):
        panel = QFrame()
        panel.setFixedWidth(320)
        panel.setObjectName("leftPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("🎵 MediaDownloader")
        title.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        layout.addWidget(title)

        content_type_label = QLabel("Тип контента:")
        content_type_label.setStyleSheet("color: #888; font-size: 12px; margin-top: 10px;")
        layout.addWidget(content_type_label)

        self.content_type_combo = QComboBox()
        self.content_type_combo.addItems(["Видео/Клип", "Фильм"])
        self.content_type_combo.setObjectName("contentTypeCombo")
        layout.addWidget(self.content_type_combo)

        format_label = QLabel("\nКачество / Формат:")
        format_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(format_label)

        self.format_combo = QComboBox()
        self.format_combo.addItems([
            "📺 ТВ 4K UHD до 2160p (MP4)",
            "🌈 ТВ 4K HDR до 2160p (если доступно)",
            "📺 ТВ MP4 H.264 до 1080p (универсальный)",
            "📺 Старый ТВ MP4 H.264 до 720p",
            "🕶️ ТВ 3D до 1080p (только исходное 3D)",
            "🏆 Макс. качество MP4 (QuickTime)",
            "🚗 Авто-Формат (магнитола 10')",
            "🎵 MP3 Аудио"
        ])
        self.format_combo.setToolTip(
            "3D нельзя создать из обычного ролика: этот профиль сохраняет 3D, "
            "только если оно уже есть в исходном видео."
        )
        self.format_combo.setObjectName("formatCombo")
        layout.addWidget(self.format_combo)

        self.download_btn = QPushButton("⬇ Скачать видео")
        self.download_btn.setObjectName("downloadBtn")
        self.download_btn.setMinimumHeight(48)
        self.download_btn.clicked.connect(self._start_download)
        layout.addWidget(self.download_btn)

        self.stop_all_btn = QPushButton("⏹ Остановить все")
        self.stop_all_btn.setObjectName("stopBtn")
        self.stop_all_btn.setMinimumHeight(44)
        self.stop_all_btn.clicked.connect(self._stop_all_downloads)
        layout.addWidget(self.stop_all_btn)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #333;")
        line.setFixedHeight(1)
        layout.addWidget(line)

        path_label = QLabel(f"📁 {self.download_path}")
        path_label.setStyleSheet("color: #666; font-size: 11px;")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        open_folder_btn = QPushButton("📂 Открыть папку загрузок")
        open_folder_btn.clicked.connect(self._open_download_folder)
        open_folder_btn.setStyleSheet("""
            QPushButton { background-color: #252540; border: 1px solid #3a3a55; 
                         border-radius: 6px; padding: 8px; color: #aaa; }
            QPushButton:hover { background-color: #2a2a50; }""")
        layout.addWidget(open_folder_btn)

        self.update_btn = QPushButton(f"🔄 Проверить обновления (v{APP_VERSION})")
        self.update_btn.clicked.connect(self._check_for_updates)
        self.update_btn.setStyleSheet("""
            QPushButton { background-color: #252540; border: 1px solid #3a3a55;
                         border-radius: 6px; padding: 8px; color: #aaa; }
            QPushButton:hover { background-color: #2a2a50; }""")
        layout.addWidget(self.update_btn)

        self.list_label = QLabel("Активные загрузки: 0")
        self.list_label.setStyleSheet("color: #888; font-size: 12px; margin-top: 10px;")
        layout.addWidget(self.list_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background-color: transparent;")
        self.scroll_area.setMinimumHeight(200)

        self.downloads_container = QWidget()
        self.downloads_layout = QVBoxLayout(self.downloads_container)
        self.downloads_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.downloads_layout.setSpacing(10)
        self.downloads_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area.setWidget(self.downloads_container)
        layout.addWidget(self.scroll_area, stretch=1)

        return panel

    def _create_browser_panel(self):
        panel = QWidget()
        panel.setObjectName("browserPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(10, 10, 10, 10)
        nav_layout.setSpacing(5)

        back_btn = QPushButton("← Назад")
        back_btn.setObjectName("navBtn")
        back_btn.setFixedWidth(80)
        back_btn.clicked.connect(self._go_back)
        nav_layout.addWidget(back_btn)

        forward_btn = QPushButton("Вперед →")
        forward_btn.setObjectName("navBtn")
        forward_btn.setFixedWidth(80)
        forward_btn.clicked.connect(self._go_forward)
        nav_layout.addWidget(forward_btn)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("  Введите адрес любого сайта...")
        self.search_bar.setObjectName("searchBar")
        self.search_bar.setFixedHeight(40)
        self.search_bar.returnPressed.connect(lambda: self._navigate(self.search_bar.text()))
        nav_layout.addWidget(self.search_bar, stretch=1)

        go_btn = QPushButton("Перейти")
        go_btn.setObjectName("goBtn")
        go_btn.setFixedWidth(80)
        go_btn.clicked.connect(lambda: self._navigate(self.search_bar.text()))
        nav_layout.addWidget(go_btn)

        layout.addLayout(nav_layout)

        self.browser = QWebEngineView()

        self.ad_blocker = AdBlocker()
        self.browser.page().profile().setUrlRequestInterceptor(self.ad_blocker)

        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        self.browser.page().profile().setHttpUserAgent(user_agent)

        self.browser.setUrl(QUrl("https://duckduckgo.com/"))
        self.browser.urlChanged.connect(self._on_url_changed)
        layout.addWidget(self.browser)

        return panel

    def _go_back(self):
        if self.browser.history().canGoBack():
            self.browser.back()

    def _go_forward(self):
        if self.browser.history().canGoForward():
            self.browser.forward()

    def _navigate(self, query):
        if not query:
            return
        if query.startswith('http://') or query.startswith('https://'):
            self.browser.setUrl(QUrl(query))
        elif '.' in query and ' ' not in query and len(query) > 3:
            self.browser.setUrl(QUrl('https://' + query))
        else:
            search_url = f"https://duckduckgo.com/?q={quote_plus(query)}"
            self.browser.setUrl(QUrl(search_url))

    def _on_url_changed(self, url):
        url_str = url.toString()
        self.search_bar.setText(url_str)

    def _setup_shortcuts(self):
        from PyQt6.QtGui import QAction, QKeySequence

        fullscreen_action = QAction("Полноэкранный режим", self)
        fullscreen_action.setShortcut(QKeySequence("F11"))
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        self.addAction(fullscreen_action)

        browser_fullscreen = QAction("Браузер на весь экран", self)
        browser_fullscreen.setShortcut(QKeySequence("Ctrl+F"))
        browser_fullscreen.triggered.connect(self._toggle_browser_fullscreen)
        self.addAction(browser_fullscreen)

        escape_action = QAction("Выйти", self)
        escape_action.setShortcut(QKeySequence("Esc"))
        escape_action.triggered.connect(self._exit_fullscreen)
        self.addAction(escape_action)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.search_bar.setVisible(True)
        else:
            self.search_bar.setVisible(False)
            self.showFullScreen()

    def _toggle_browser_fullscreen(self):
        left_panel = self.centralWidget().layout().itemAt(0).widget()
        left_panel.setVisible(not left_panel.isVisible())
        if left_panel.isVisible():
            self.search_bar.setVisible(True)

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.search_bar.setVisible(True)
        left_panel = self.centralWidget().layout().itemAt(0).widget()
        left_panel.setVisible(True)

    def _start_download(self):
        current_url = self.browser.url().toString()
        format_type = self.format_combo.currentText()
        content_type = self.content_type_combo.currentText()

        print(f"\n{'=' * 70}")
        print(f"[СКАЧИВАНИЕ] Запрос")
        print(f"[СКАЧИВАНИЕ] URL: {current_url}")
        print(f"[СКАЧИВАНИЕ] Тип: {content_type}")
        print(f"[СКАЧИВАНИЕ] Формат: {format_type}")

        if not current_url or current_url == "about:blank":
            QMessageBox.warning(self, "Ошибка", "❌ Откройте страницу с видео")
            return

        clean_url = get_download_url(current_url)
        if clean_url != current_url:
            print(f"[СКАЧИВАНИЕ] Плейлист отброшен → {clean_url}")
        else:
            print(f"[СКАЧИВАНИЕ] URL: {clean_url}")

        if content_type == "Фильм":
            save_path = self.movies_path
            print("[СКАЧИВАНИЕ] ✅ Фильм → папка «Фильмы»")
        else:
            save_path = self.clips_path
            print("[СКАЧИВАНИЕ] ✅ Клип → папка «Клипы»")

        download_item = DownloadItem("Загрузка...", clean_url, save_path)
        self.downloads_layout.addWidget(download_item)
        self.download_items.append(download_item)

        download_item.stop_btn.clicked.connect(lambda: self._cancel_download(download_item))
        download_item.open_folder_btn.clicked.connect(
            lambda: self._open_folder(download_item.save_path)
        )

        print(f"[СКАЧИВАНИЕ] 📁 Итоговая папка: {save_path}")
        print(f"{'=' * 70}")

        thread = DownloadThread(clean_url, format_type, save_path, content_type)
        thread.title_received.connect(lambda title: self._update_title(download_item, title))
        thread.progress.connect(lambda p, s, e: self._update_progress(download_item, p, s, e))
        thread.finished.connect(lambda _title: self._download_finished(download_item))
        thread.error.connect(lambda err: self._download_error(download_item, err))
        thread.cancelled.connect(lambda: self._download_cancelled(download_item))

        self.active_downloads[download_item] = thread
        self.list_label.setText(f"Активные загрузки: {len(self.active_downloads)}")
        thread.start()

    def _update_title(self, item_widget, title):
        item_widget.title_label.setText(title[:60] + "..." if len(title) > 60 else title)

    def _update_progress(self, item_widget, percent, speed, eta):
        item_widget.progress_bar.setValue(percent)
        if speed and speed != 'N/A':
            item_widget.progress_bar.setFormat(f"{percent}% | {speed}")
        else:
            item_widget.progress_bar.setFormat(f"{percent}%")

    def _download_finished(self, item_widget):
        item_widget.progress_bar.setValue(100)
        item_widget.progress_bar.setFormat("✓ Завершено")
        item_widget.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #10b981; }")
        if item_widget in self.active_downloads:
            del self.active_downloads[item_widget]
        self.list_label.setText(f"Активные загрузки: {len(self.active_downloads)}")

    def _download_error(self, item_widget, error):
        print(f"[ОШИБКА] {error}")
        item_widget.progress_bar.setFormat("✗ Ошибка")
        item_widget.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #ef4444; }")
        item_widget.title_label.setStyleSheet("color: #ef4444;")

        if 'GVS PO Token' in error or 'Sign in' in error or '403' in error:
            QMessageBox.warning(
                self, "Ошибка YouTube",
                "⚠️ YouTube блокирует загрузку!\n\n"
                "РЕШЕНИЕ:\n"
                "1. Открой Терминал\n"
                "2. Выполни: pip install --upgrade yt-dlp\n"
                "3. Перезапусти приложение\n"
                "4. Попробуй снова"
            )
        else:
            QMessageBox.warning(self, "Ошибка", error)

        if item_widget in self.active_downloads:
            del self.active_downloads[item_widget]
        self.list_label.setText(f"Активные загрузки: {len(self.active_downloads)}")

    def _download_cancelled(self, item_widget):
        item_widget.progress_bar.setFormat("✗ Отменено")
        item_widget.progress_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #f59e0b; }"
        )
        if item_widget in self.active_downloads:
            del self.active_downloads[item_widget]
        self.list_label.setText(f"Активные загрузки: {len(self.active_downloads)}")

    def _cancel_download(self, item_widget):
        if item_widget in self.active_downloads:
            self.active_downloads[item_widget].cancel()
            item_widget.progress_bar.setFormat("✗ Отменено")

    def _stop_all_downloads(self):
        for item in list(self.active_downloads.keys()):
            self._cancel_download(item)

    def _open_folder(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_download_folder(self):
        self._open_folder(self.download_path)

    def _check_for_updates(self):
        repository = self.settings.value("github_repository", GITHUB_REPOSITORY, type=str).strip()
        if not repository:
            repository, accepted = QInputDialog.getText(
                self,
                "Настройка обновлений",
                "GitHub-репозиторий с релизами (например, user/MediaDownloader):",
            )
            repository = repository.strip()
            if not accepted or not repository:
                return
            self.settings.setValue("github_repository", repository)

        self.update_btn.setEnabled(False)
        self.update_btn.setText("🔄 Проверяю обновления...")
        self.update_check_thread = UpdateCheckThread(repository)
        self.update_check_thread.checked.connect(self._on_update_checked)
        self.update_check_thread.error.connect(self._on_update_check_error)
        self.update_check_thread.start()

    def _restore_update_button(self):
        self.update_btn.setEnabled(True)
        self.update_btn.setText(f"🔄 Проверить обновления (v{APP_VERSION})")

    def _on_update_checked(self, release):
        self._restore_update_button()
        if release is None:
            QMessageBox.information(self, "Обновления", "Установлена последняя версия.")
            return

        latest_version = release.get("tag_name", "новая версия")
        notes = release.get("body", "")[:1000] or "Описание изменений не добавлено."
        answer = QMessageBox.question(
            self,
            "Доступно обновление",
            f"Доступна версия {latest_version}.\n\n{notes}\n\nСкачать установщик?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_update(release)

    def _on_update_check_error(self, message):
        self._restore_update_button()
        QMessageBox.warning(self, "Обновления", message)

    def _download_update(self, release):
        self.update_progress_dialog = QProgressDialog(
            "Скачивание установщика…", None, 0, 100, self
        )
        self.update_progress_dialog.setWindowTitle("Обновление")
        self.update_progress_dialog.setAutoClose(False)
        self.update_progress_dialog.setCancelButton(None)
        self.update_progress_dialog.show()

        self.update_download_thread = UpdateDownloadThread(release)
        self.update_download_thread.progress.connect(self.update_progress_dialog.setValue)
        self.update_download_thread.completed.connect(self._on_update_downloaded)
        self.update_download_thread.error.connect(self._on_update_download_error)
        self.update_download_thread.start()

    def _on_update_downloaded(self, installer_path):
        self.update_progress_dialog.close()
        if sys.platform == "darwin":
            try:
                install_macos_update(installer_path)
            except UpdateError as error:
                QMessageBox.warning(self, "Обновление", str(error))
                return
            QMessageBox.information(
                self,
                "Обновление готово",
                "Программа сама установит обновление в вашу папку «Программы» и перезапустится.",
            )
            QTimer.singleShot(300, QApplication.quit)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(installer_path))
        QMessageBox.information(
            self,
            "Обновление скачано",
            "Установщик открыт. Завершите установку и перезапустите приложение.",
        )

    def _on_update_download_error(self, message):
        self.update_progress_dialog.close()
        QMessageBox.warning(self, "Обновления", message)

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0f0f1a; }
            #leftPanel { background-color: #161625; border-right: 1px solid #2a2a3e; }
            #browserPanel { background-color: #0f0f1a; }
            QLabel { color: #e0e0e0; }
            #formatCombo, #contentTypeCombo { background-color: #252540; border: 1px solid #3a3a55; 
                         border-radius: 8px; padding: 12px; color: white; font-size: 14px; }
            #formatCombo::drop-down, #contentTypeCombo::drop-down { border: none; }
            #formatCombo QAbstractItemView, #contentTypeCombo QAbstractItemView { 
                background-color: #252540; selection-background-color: #4a4aff; color: white; }
            #downloadBtn { background-color: #3b82f6; color: white; border: none; 
                        border-radius: 8px; font-size: 16px; font-weight: bold; }
            #downloadBtn:hover { background-color: #2563eb; }
            #stopBtn { background-color: rgba(239, 68, 68, 0.15); color: #ef4444; 
                      border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; }
            #stopBtn:hover { background-color: rgba(239, 68, 68, 0.25); }
            #navBtn { background-color: #252540; color: white; border: 1px solid #3a3a55;
                     border-radius: 6px; font-weight: bold; }
            #navBtn:hover { background-color: #3a3a55; }
            #searchBar { background-color: #1a1a2e; border: 1px solid #2a2a3e; 
                       border-radius: 8px; padding: 0 15px; color: white; }
            #searchBar:focus { border: 1px solid #4a4aff; }
            #goBtn { background-color: #3b82f6; color: white; border: none; 
                    border-radius: 8px; font-weight: bold; }
            #progressBar { background-color: #0f0f1a; border-radius: 4px; height: 20px; }
            #progressBar::chunk { background-color: #3b82f6; border-radius: 4px; }
            #stopDownloadBtn, #openFolderBtn { background-color: rgba(239, 68, 68, 0.2); 
                                              color: #ef4444; border: none; border-radius: 6px; }
            #openFolderBtn { background-color: rgba(59, 130, 246, 0.2); color: #3b82f6; }
        """)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("MediaDownloader Pro")
    window = VideoApp()
    window.show()
    sys.exit(app.exec())
