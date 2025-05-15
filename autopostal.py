import sys
import os
import time
from datetime import datetime
import vk_api
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QTextEdit, QMessageBox, QSplitter, QDateTimeEdit, QCheckBox
)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6 import QtGui

from concurrent.futures import ThreadPoolExecutor

import threading 

import random


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)



CONFIG_PATH = os.path.join(os.path.dirname(sys.argv[0]), "last_settings.cfg")



def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    config = {}
    try:
        config["token"] = lines[0].strip() if len(lines) > 0 else ""
        config["group_id"] = lines[1].strip() if len(lines) > 1 else ""
        config["photos_per_post"] = lines[2].strip() if len(lines) > 2 else "9"
        if len(lines) > 3:
            config["last_post_time"] = int(lines[3].strip())
        else:
            config["last_post_time"] = None
    except Exception:
        return {}
    return config


def save_config(token="", group_id="", photos_per_post="9", last_post_time=None):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(f"{token}\n")
            f.write(f"{group_id}\n")
            f.write(f"{photos_per_post}\n")
            if last_post_time is not None:
                f.write(f"{last_post_time}\n")
    except Exception as e:
        print(f"[🧰ERROR] Не удалось сохранить конфиг: {e}")



class PosterWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()
    update_last_post_time = Signal(int)

    def __init__(self, token, group_id, interval_hours, folder_path, start_timestamp,
                 photos_per_post, caption="", use_random_emoji=False, emoji_list=None):
        super().__init__()
        self.token = token
        self.group_id = group_id
        self.interval_hours = interval_hours
        self.folder_path = folder_path
        self.start_timestamp = start_timestamp
        self.photos_per_post = photos_per_post
        self.posts_saved = 0
        self.paused = False
        self.pause_cond = threading.Condition(threading.Lock())
        self.caption = caption
        self.use_random_emoji = use_random_emoji
        self.emoji_list = emoji_list or []
    
    def toggle_pause(self):
        with self.pause_cond:
            self.paused = not self.paused
            if not self.paused:
                self.pause_cond.notify()

    def run(self):
        try:
            self.log_signal.emit("[📶] Подключение к API ВКонтакте...")
            vk_session = vk_api.VkApi(token=self.token)
            vk = vk_session.get_api()
        except Exception as e:
            self.log_signal.emit(f"[🧰ERROR] Не удалось подключиться к API ВК: {e}")
            self.finished_signal.emit()
            return

        try:
            server_time = vk.utils.getServerTime()
            current_time = server_time
            self.log_signal.emit(
                f"[⏰] Точное время сервера: {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M')}"
            )
        except:
            current_time = int(time.time())
            self.log_signal.emit(
                f"[🤬WARN] Не удалось получить время сервера. Используется локальное время."
            )

        delay_between_posts = 3
        post_delay_seconds = self.interval_hours * 3600
        current_post_time = self.start_timestamp

        photos = [f for f in os.listdir(self.folder_path) if os.path.isfile(os.path.join(self.folder_path, f))]
        self.log_signal.emit(f"[🔎] Найдено {len(photos)} изображений для публикации.")

        batch_size = int(self.photos_per_post)
        batches = [photos[i:i + batch_size] for i in range(0, len(photos), batch_size)]

        from concurrent.futures import ThreadPoolExecutor

        for batch_number, photo_batch in enumerate(batches):
            while self.paused:
                with self.pause_cond:
                    self.pause_cond.wait(timeout=1.0)

            try:
                media_ids = []

                def upload_single_photo(photo_file):
                    try:
                        self.log_signal.emit(f"[📩] Загружаю {photo_file}")
                        full_path = os.path.join(self.folder_path, photo_file)
                        upload_server = vk.photos.getWallUploadServer(group_id=abs(int(self.group_id)))
                        server, photo_data, photo_hash = self.upload_photo(upload_server, full_path)
                        media_id = self.save_wall_photo(vk, self.group_id, server, photo_data, photo_hash)
                        return media_id
                    except Exception as e:
                        self.log_signal.emit(f"[🧰ERROR] Ошибка при загрузке {photo_file}: {e}")
                        return None

                with ThreadPoolExecutor(max_workers=9) as executor:
                    results = list(executor.map(upload_single_photo, photo_batch))
                    media_ids = [result for result in results if result is not None]

                post_time = current_post_time + batch_number * post_delay_seconds
                if post_time < int(time.time()):
                    post_time = int(time.time()) + 60 * (batch_number + 1)
                    self.log_signal.emit(
                        f"[🤬WARN] Скорректировано время для поста #{batch_number} на {datetime.fromtimestamp(post_time).strftime('%Y-%m-%d %H:%M')}"
                    )
                else:
                    self.log_signal.emit(
                        f"[📅] Пост #{batch_number} запланирован на {datetime.fromtimestamp(post_time).strftime('%Y-%m-%d %H:%M')}"
                    )

                post_text = self.caption

                if self.use_random_emoji and self.emoji_list:
                    emoji = random.choice(self.emoji_list)
                    post_text += f"\n\n{emoji}"

                vk.wall.post(
                    owner_id=int(self.group_id),
                    from_group=1,
                    message=post_text,
                    attachments=",".join(media_ids),
                    publish_date=post_time
                )

                self.posts_saved += 1
                self.update_last_post_time.emit(post_time)
                save_config(self.token, self.group_id, self.photos_per_post, post_time)
                time.sleep(delay_between_posts)

            except Exception as e:
                self.log_signal.emit(f"[🧰ERROR] Ошибка при обработке пакета #{batch_number}: {e}")

        self.log_signal.emit("[📝] 🧃 Все посты добавлены в отложку. Можешь пойти пить пиво.🍺")
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
        
    def save_wall_photo(self, vk, group_id, server, photo_data, photo_hash):
        photos = vk.photos.saveWallPhoto(
            group_id=abs(int(group_id)),
            server=server,
            photo=photo_data,
            hash=photo_hash
        )
        return f"photo{photos[0]['owner_id']}_{photos[0]['id']}"

    def upload_single_photo(self, vk, group_id, folder_path, photo_file):
        try:
            self.log_signal.emit(f"[📩] Загружаю {photo_file}")
            full_path = os.path.join(folder_path, photo_file)
            upload_server = vk.photos.getWallUploadServer(group_id=abs(int(group_id)))
            server, photo_data, photo_hash = self.upload_photo(upload_server, full_path)
            media_id = self.save_wall_photo(vk, group_id, server, photo_data, photo_hash)
            return media_id
        except Exception as e:
            self.log_signal.emit(f"[🧰ERROR] Ошибка при загрузке {photo_file}: {e}")
            return None



class CheckAndClearWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()
    count_ready = Signal(int)

    def __init__(self, token, group_id, action="check"):
        super().__init__()
        self.token = token
        self.group_id = group_id
        self.action = action  

    def run(self):
        try:
            self.log_signal.emit("[📶] Подключение к API ВКонтакте...")
            vk_session = vk_api.VkApi(token=self.token)
            vk = vk_session.get_api()
        except Exception as e:
            self.log_signal.emit(f"[🧰ERROR] Не удалось подключиться к API ВК: {e}")
            self.finished_signal.emit()
            return

        try:
            self.log_signal.emit("[📝⏰] Получаем список отложенных записей...")

            offset = 0
            count = 100
            all_posts = []

            while True:
                response = vk.wall.get(owner_id=int(self.group_id), filter='postponed', count=count, offset=offset)
                items = response.get('items', [])
                if not items:
                    break
                all_posts.extend(items)
                offset += count
                time.sleep(0.3)

            count_posts = len(all_posts)
            self.count_ready.emit(count_posts)

            if self.action == "check":
                self.log_signal.emit(f"[🔎] Найдено {count_posts} отложенных записей.")
            elif self.action == "clear":
                self.log_signal.emit(f"[🧼🧼🧼] Начинаю удаление {count_posts} отложенных записей.")
                for post in all_posts:
                    try:
                        vk.wall.delete(owner_id=int(self.group_id), post_id=post['id'])
                        self.log_signal.emit(f"[🧼] Удалён пост ID={post['id']}")
                        time.sleep(0.2)
                    except Exception as e:
                        self.log_signal.emit(f"[🧰ERROR] Ошибка при удалении поста ID={post['id']}: {e}")
                self.log_signal.emit(f"[👍] Все {count_posts} отложенных записей удалены.")
        except Exception as e:
            self.log_signal.emit(f"[🧰ERROR] Ошибка при работе с API: {e}")

        self.finished_signal.emit()



class VKAutoPosterApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VK Going Auto-Postal!")
        self.resize(900, 550)
        icon_path = resource_path("ico.ico")
        if os.path.exists(icon_path):
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
            QPushButton#clear_button {
                background-color: #ff4444;
            }
            QPushButton#clear_button:hover {
                background-color: #cc3333;
            }
            QPushButton#pause_button {
                background-color: #ffa500;
            }
            QPushButton#pause_button:hover {
                background-color: #dd8800;
            }
            QDateTimeEdit {
                background-color: #444;
                border: 1px solid #555;
                padding: 5px;
                color: white;
            }
            QPushButton#pause_button {
                background-color: #00aaff;
            }
            QPushButton#pause_button:hover {
                background-color: #008ecc;
            }
            QPushButton#pause_button[paused="true"] {
                background-color: #ff4444;
            }
            QPushButton#pause_button[paused="true"]:hover {
                background-color: #cc3333;
            }
        """)
        self.init_ui()
        
        self.emoji_list = [
            "💋", "💄", "🧴", "🧼", "🧖‍♀️", "✨", "🌟", "💫", "💅", "💎", "🌸",
            "👠", "👡", "👢", "👜", "👛", "👒", "🎀", "🧥", "🩱", "👗", "👚", "🕶️",
            "💘", "💗", "💓", "💞", "❤️", "💌", "🌹", "💋", "😏", "😍", "😘", "🥰",
            "🎉", "✨", "🍾", "🥂", "🍷", "🍸", "🍹",
            "🧁", "🍰", "🍭", "🍬", "🍫", "🍩", "🍪", "🍧", "🍨", "🍦", "🧁",
            "🧚", "🦄", "🧸", "🎀", "🔮", "🌌", "🪐", "💫", "🌠",
            "😈", "👅", "🍑", "🍒", "🍓", "🥵", "👙", "🩳", "💦", "🩸",
            "😳", "😍", "🤤", "😜", "😏", "😒", "😌", "🥰", "😱", "🤯", "😵‍💫",
            "🐾", "🌷", "🌼", "🌻", "🌿", "🍀", "🍁", "🥀", "🌺",
            "🌌", "🪐", "🌕", "🌑", "🛸", "👽", "👾", "🛰️",
            "☕", "🍵", "🥛", "🍯", "🧁", "🍰", "🍩", "🍪", "🍧", "🍨", "🍦",
            "🎵", "🎶", "🎧", "📻", "🎹", "🎼", "🎤", "🎙️", "🎚️", "📼",
        ]

    def init_ui(self):
        main_layout = QHBoxLayout()
        left_widget = QWidget()
        left_layout = QVBoxLayout()

        config = load_config()

        
        self.token_input = QLineEdit(config.get("token", ""))
        left_layout.addWidget(QLabel("VK Токен API:"))
        left_layout.addWidget(self.token_input)

        
        self.group_input = QLineEdit(config.get("group_id", ""))
        left_layout.addWidget(QLabel("Числовой ID сообщества|паблика:"))
        left_layout.addWidget(self.group_input)

        
        self.photos_per_post_input = QLineEdit(config.get("photos_per_post", "9"))
        left_layout.addWidget(QLabel("Кол-во фото на один пост (1-9):"))
        left_layout.addWidget(self.photos_per_post_input)

        
        self.interval_input = QLineEdit("2")
        left_layout.addWidget(QLabel("Интервал постов (в часах):"))
        left_layout.addWidget(self.interval_input)

        
        last_post_time = config.get("last_post_time")
        default_start = datetime.now().replace(second=0, microsecond=0)
        if last_post_time:
            default_start = datetime.fromtimestamp(last_post_time + 7200)
        self.datetime_edit = QDateTimeEdit(default_start)
        self.datetime_edit.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.datetime_edit.setCalendarPopup(True)
        left_layout.addWidget(QLabel("Дата и время первого поста:"))
        left_layout.addWidget(self.datetime_edit)
        
        self.caption_input = QLineEdit("")
        left_layout.addWidget(QLabel("Подпись к постам (Необязательно):"))
        left_layout.addWidget(self.caption_input)

        self.random_emoji_checkbox = QCheckBox("Рандомизировать эмодзи")
        left_layout.addWidget(self.random_emoji_checkbox)

        
        self.run_button = QPushButton("GO POSTAL!")
        self.run_button.clicked.connect(self.start_posting)
        left_layout.addWidget(self.run_button)

        
        check_clear_layout = QHBoxLayout()
        self.check_button = QPushButton("Проверить кол-во отложки")
        self.check_button.clicked.connect(self.check_delayed)
        self.clear_button = QPushButton("Очистить отложку")
        self.clear_button.setObjectName("clear_button")
        self.clear_button.clicked.connect(self.clear_delayed)
        check_clear_layout.addWidget(self.check_button)
        check_clear_layout.addWidget(self.clear_button)
        left_layout.addLayout(check_clear_layout)

        
        self.pause_button = QPushButton("⏸️Пауза")
        self.pause_button.setObjectName("pause_button")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setEnabled(False)
        left_layout.addWidget(self.pause_button)

        
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
        photos_per_post = self.photos_per_post_input.text().strip()

        try:
            interval_hours = int(self.interval_input.text().strip())
            if interval_hours < 1:
                raise ValueError("Интервал должен быть больше или равен 1")
        except ValueError as e:
            QMessageBox.critical(self, "Ошибка", f"Некорректный интервал: {e}")
            return

        try:
            photos_per_post_int = int(photos_per_post)
            if photos_per_post_int < 1 or photos_per_post_int > 9:
                raise ValueError("Кол-во фото должно быть от 1 до 9")
        except ValueError as e:
            QMessageBox.critical(self, "Ошибка", f"Некорректное число фото на пост: {e}")
            return

        if not token or not group_id:
            QMessageBox.critical(self, "Ошибка", "Заполни все поля.")
            return

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

        start_datetime = self.datetime_edit.dateTime()
        start_timestamp = start_datetime.toSecsSinceEpoch()
        if start_timestamp < int(time.time()):
            reply = QMessageBox.question(
                self,
                "Время в прошлом",
                "Выбранная дата прошла. Поставить текущее время?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.No:
                return
            else:
                start_timestamp = int(time.time())

        save_config(token, group_id, photos_per_post, None)

        self.run_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        caption = self.caption_input.text().strip()
        use_random_emoji = self.random_emoji_checkbox.isChecked()

        self.worker = PosterWorker(
            token, group_id, interval_hours, folder_path, start_timestamp,
            photos_per_post, caption, use_random_emoji, self.emoji_list
        )
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(lambda: self.run_button.setEnabled(True))
        self.worker.finished_signal.connect(lambda: self.pause_button.setEnabled(False))
        self.worker.update_last_post_time.connect(lambda t: self.datetime_edit.setDateTime(
            datetime.fromtimestamp(t + 7200)
        ))
        self.worker.start()

    def check_delayed(self):
        token = self.token_input.text().strip()
        group_id = self.group_input.text().strip()
        if not token or not group_id:
            QMessageBox.critical(self, "Ошибка", "Заполни оба поля.")
            return
        try:
            group_id_int = int(group_id)
            if group_id_int > 0:
                group_id_int = -group_id_int
            group_id = str(group_id_int)
        except ValueError:
            QMessageBox.critical(self, "Ошибка", "ID должно быть числом.")
            return
        self.check_button.setEnabled(False)
        self.check_worker = CheckAndClearWorker(token, group_id, action="check")
        self.check_worker.log_signal.connect(self.append_log)
        self.check_worker.finished_signal.connect(lambda: self.check_button.setEnabled(True))
        self.check_worker.start()

    def clear_delayed(self):
        token = self.token_input.text().strip()
        group_id = self.group_input.text().strip()
        if not token or not group_id:
            QMessageBox.critical(self, "Ошибка", "Заполните оба поля.")
            return
        try:
            group_id_int = int(group_id)
            if group_id_int > 0:
                group_id_int = -group_id_int
            group_id = str(group_id_int)
        except ValueError:
            QMessageBox.critical(self, "Ошибка", "ID должно быть числом.")
            return
        self.clear_button.setEnabled(False)
        self.clear_worker = CheckAndClearWorker(token, group_id, action="clear")
        self.clear_worker.log_signal.connect(self.append_log)
        self.clear_worker.finished_signal.connect(lambda: self.clear_button.setEnabled(True))
        self.clear_worker.start()

    def toggle_pause(self):
        if hasattr(self, 'worker'):
            self.worker.toggle_pause()
            is_paused = self.worker.paused
            self.pause_button.setText("▶️Пуск" if is_paused else "⏸️Пауза")

            
            self.pause_button.setProperty("paused", is_paused)
            self.pause_button.style().unpolish(self.pause_button)
            self.pause_button.style().polish(self.pause_button)

            if is_paused:
                self.append_log("[⏸️] Работа остановлена.")
            else:
                self.append_log("[▶️] Продолжаю работу...")

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
            f.write(str(e) + "\n")
            f.write(traceback.format_exc())
        print("Произошла ошибка:")
        print(traceback.format_exc())
        input("Нажми Enter для выхода...")
