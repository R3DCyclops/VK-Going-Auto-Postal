import sys
import os
import time
from datetime import datetime
import vk_api
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QTextEdit, QMessageBox, QSplitter, QDateTimeEdit
)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6 import QtGui


# === Функция для поиска ресурсов внутри .exe ===
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# Путь к конфигу рядом с .exe
CONFIG_PATH = os.path.join(os.path.dirname(sys.argv[0]), "last_settings.cfg")


# === Функции работы с конфигом ===
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    config = {}
    try:
        config["token"] = lines[0].strip() if len(lines) > 0 else ""
        config["group_id"] = lines[1].strip() if len(lines) > 1 else ""
        if len(lines) > 2:
            config["last_post_time"] = int(lines[2].strip())
        else:
            config["last_post_time"] = None
    except Exception:
        return {}
    return config


def save_config(token="", group_id="", last_post_time=None):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(f"{token}\n")
            f.write(f"{group_id}\n")
            if last_post_time is not None:
                f.write(f"{last_post_time}\n")
    except Exception as e:
        print(f"[ERROR] Не удалось сохранить конфиг: {e}")


# === Класс для выполнения задачи в отдельном потоке ===
class PosterWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()
    update_last_post_time = Signal(int)

    def __init__(self, token, group_id, interval_hours, folder_path, start_timestamp):
        super().__init__()
        self.token = token
        self.group_id = group_id
        self.interval_hours = interval_hours
        self.folder_path = folder_path
        self.start_timestamp = start_timestamp
        self.posts_saved = 0

    def run(self):
        try:
            self.log_signal.emit("[INFO] Подключение к API ВКонтакте...")
            vk_session = vk_api.VkApi(token=self.token)
            vk = vk_session.get_api()
        except Exception as e:
            self.log_signal.emit(f"[ERROR] Не удалось подключиться к API ВК: {e}")
            self.finished_signal.emit()
            return

        # Получаем точное время сервера или локальное
        try:
            server_time = vk.utils.getServerTime()
            current_time = server_time
            self.log_signal.emit(
                f"[INFO] Точное время сервера: {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M')}"
            )
        except:
            current_time = int(time.time())
            self.log_signal.emit(
                f"[WARN] Не удалось получить время сервера. Используется локальное время."
            )

        delay_between_posts = 3
        post_number = 0

        photos = [f for f in os.listdir(self.folder_path) if os.path.isfile(os.path.join(self.folder_path, f))]
        self.log_signal.emit(f"[INFO] Найдено {len(photos)} изображений для публикации.")

        post_delay_seconds = self.interval_hours * 3600

        for photo_file in photos:
            try:
                self.log_signal.emit(f"[+] Загружаю {photo_file}")
                full_path = os.path.join(self.folder_path, photo_file)

                upload_server = vk.photos.getWallUploadServer(group_id=abs(int(self.group_id)))
                server, photo_data, photo_hash = self.upload_photo(upload_server, full_path)
                media_id = self.save_wall_photo(vk, self.group_id, server, photo_data, photo_hash)

                post_time = self.start_timestamp + post_number * post_delay_seconds

                if post_time < int(time.time()):
                    post_time = int(time.time()) + 60 * (post_number + 1)
                    self.log_signal.emit(
                        f"[WARN] Скорректировано время для поста #{post_number} на {datetime.fromtimestamp(post_time).strftime('%Y-%m-%d %H:%M')}"
                    )
                else:
                    self.log_signal.emit(
                        f"[✓] Пост #{post_number} запланирован на {datetime.fromtimestamp(post_time).strftime('%Y-%m-%d %H:%M')}"
                    )

                vk.wall.post(
                    owner_id=int(self.group_id),
                    from_group=1,
                    attachments=media_id,
                    publish_date=post_time
                )

                post_number += 1
                self.posts_saved += 1

                if self.posts_saved % 12 == 0:
                    self.update_last_post_time.emit(post_time)
                    save_config(self.token, self.group_id, post_time)

                time.sleep(delay_between_posts)

            except Exception as e:
                self.log_signal.emit(f"[!] Ошибка при обработке {photo_file}: {e}")

        self.update_last_post_time.emit(post_time)
        save_config(self.token, self.group_id, post_time)

        self.log_signal.emit("[INFO] 🧃 Все посты добавлены в отложку. Можешь пойти пить пиво.🍺")
        self.finished_signal.emit()

    def upload_photo(self, server, photo_path):
        import requests
        with open(photo_path, 'rb') as f:
            files = {'photo': f}
            response = requests.post(server['upload_url'], files=files)
        result = response.json()
        return result['server'], result['photo'], result['hash']

    def save_wall_photo(self, vk, group_id, server, photo, photo_hash):
        photos = vk.photos.saveWallPhoto(
            group_id=abs(int(group_id)),
            server=server,
            photo=photo,
            hash=photo_hash
        )
        return f"photo{photos[0]['owner_id']}_{photos[0]['id']}"


# === Основное окно приложения ===
class VKAutoPosterApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VK Going Auto-Postal!")
        self.resize(800, 500)

        icon_path = resource_path("ico.ico")
        self.setWindowIcon(QtGui.QIcon(icon_path))

        self.setStyleSheet("""
            QWidget {
                background-color: #2e2e2e;
                color: white;
                font-family: Arial;
                font-size: 14px;
            }
            QLineEdit {
                background-color: #444;
                border: 1px solid #555;
                padding: 5px;
                color: white;
            }
            QTextEdit {
                background-color: #1e1e1e;
                color: #cccccc;
                border: 1px solid #444;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
            QPushButton {
                background-color: #00aaff;
                border: none;
                padding: 8px 16px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #008ecc;
            }
            QDateTimeEdit {
                background-color: #444;
                border: 1px solid #555;
                padding: 5px;
                color: white;
            }
        """)

        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout()

        left_widget = QWidget()
        left_layout = QVBoxLayout()

        config = load_config()

        # Токен API
        self.token_input = QLineEdit(config.get("token", ""))
        left_layout.addWidget(QLabel("VK Токен API:"))
        left_layout.addWidget(self.token_input)

        # ID Сообщества
        self.group_input = QLineEdit(config.get("group_id", ""))
        left_layout.addWidget(QLabel("Числовой ID сообщества|паблика:"))
        left_layout.addWidget(self.group_input)

        # Интервал
        self.interval_input = QLineEdit("2")
        left_layout.addWidget(QLabel("Интервал постов (в часах):"))
        left_layout.addWidget(self.interval_input)

        # Дата первого поста
        last_post_time = config.get("last_post_time")
        default_start = datetime.now().replace(second=0, microsecond=0)
        if last_post_time:
            default_start = datetime.fromtimestamp(last_post_time + 7200)

        self.datetime_edit = QDateTimeEdit(default_start)
        self.datetime_edit.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.datetime_edit.setCalendarPopup(True)
        left_layout.addWidget(QLabel("Дата и время первого поста:"))
        left_layout.addWidget(self.datetime_edit)

        # Кнопка запуска
        self.run_button = QPushButton("GO POSTAL!")
        self.run_button.clicked.connect(self.start_posting)
        left_layout.addWidget(self.run_button)

        # Логотип / картинка под кнопкой
        self.logo_label = QLabel()
        logo_path = resource_path("bckg.png")
        if os.path.exists(logo_path):
            logo_pixmap = QtGui.QPixmap(logo_path)
            self.logo_label.setPixmap(logo_pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.logo_label.setText("Лого не найдено")
        self.logo_label.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.logo_label)

        left_layout.addStretch()
        left_widget.setLayout(left_layout)

        # Правая панель — логи
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(self.log_area)

        main_layout = QVBoxLayout()
        main_layout.addWidget(splitter)
        self.setLayout(main_layout)

    def start_posting(self):
        token = self.token_input.text().strip()
        group_id = self.group_input.text().strip()
        try:
            interval_hours = int(self.interval_input.text().strip())
            if interval_hours < 1:
                raise ValueError("Интервал должен быть ≥ 1")
        except ValueError as e:
            QMessageBox.critical(self, "Ошибка", f"Некорректный интервал: {e}")
            return

        if not token or not group_id:
            QMessageBox.critical(self, "Ошибка", "Заполни все поля.")
            return

        # Автоматически добавляем минус к ID
        try:
            group_id_int = int(group_id)
            if group_id_int > 0:
                group_id_int = -group_id_int
            group_id = str(group_id_int)
            self.group_input.setText(group_id)
        except ValueError:
            QMessageBox.critical(self, "Ошибка", "ID должно быть числом.")
            return

        folder_path = os.path.join(os.path.dirname(sys.argv[0]), "photos")
        if not os.path.exists(folder_path):
            QMessageBox.critical(self, "Ошибка", f'Папка "{folder_path}" не найдена.')
            return

        # Получаем время старта
        start_datetime = self.datetime_edit.dateTime()
        start_timestamp = start_datetime.toSecsSinceEpoch()

        if start_timestamp < int(time.time()):
            reply = QMessageBox.question(
                self,
                "Время в прошлом",
                "Выбранная дата уже прошла. Поставить текущее время?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.No:
                return
            else:
                start_timestamp = int(time.time())

        self.run_button.setEnabled(False)
        self.worker = PosterWorker(token, group_id, interval_hours, folder_path, start_timestamp)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(lambda: self.run_button.setEnabled(True))
        self.worker.update_last_post_time.connect(lambda t: self.datetime_edit.setDateTime(
            datetime.fromtimestamp(t + 7200)
        ))
        self.worker.start()

    @Slot(str)
    def append_log(self, text):
        self.log_area.append(text)


if __name__ == "__main__":
    import traceback
    try:
        app = QApplication(sys.argv)
        window = VKAutoPosterApp()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        with open("error_log.txt", "w", encoding="utf-8") as f:
            f.write("Критическая ошибка:\n")
            f.write(str(e) + "\n\n")
            f.write(traceback.format_exc())
        print("Произошла ошибка:")
        print(traceback.format_exc())
        input("Нажмите Enter для выхода...")