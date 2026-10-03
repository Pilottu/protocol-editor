import os
import sys
import subprocess
import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QCheckBox, QRadioButton,
    QMessageBox, QFileDialog, QProgressBar, QComboBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

sys.path.insert(0, r"E:\Prog\Piton\src")
from db import test_connection, make_backup, reload_config
from config import load_config, save_config

import pyodbc


# =========================================================
#  Поток для скачивания файла
# =========================================================

class DownloadThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, dest_path: str):
        super().__init__()
        self.url = url
        self.dest_path = dest_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import requests
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; "
                    "rv:109.0) Gecko/20100101 Firefox/119.0"
                )
            }
            response = requests.get(self.url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            block_size = 8192
            downloaded = 0

            with open(self.dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    if self._cancelled:
                        raise InterruptedError("Скачивание отменено")
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = min(100, int(downloaded * 100 / total_size))
                        self.progress.emit(percent)

            self.finished.emit(self.dest_path)
        except Exception as e:
            self.error.emit(str(e))


# =========================================================
#  Поток для установки MSI
# =========================================================

class InstallThread(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, msi_path: str):
        super().__init__()
        self.msi_path = msi_path

    def run(self):
        try:
            cmd = [
                "msiexec.exe",
                "/i", self.msi_path,
                "IACCEPTSQLLOCALDBLICENSETERMS=YES",
                "/qn",
                "/norestart",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                self.finished.emit(True, "LocalDB успешно установлен")
            else:
                self.finished.emit(
                    False,
                    f"Код возврата: {result.returncode}\n{result.stderr}"
                )
        except Exception as e:
            self.finished.emit(False, str(e))


# =========================================================
#  Фоновые потоки для проверки статуса
# =========================================================

class LocalDBStatusThread(QThread):
    done = pyqtSignal(bool, str)  # (installed, version)

    def run(self):
        installed = is_localdb_installed()
        version = get_localdb_version() if installed else ""
        self.done.emit(installed, version)


class ServersListThread(QThread):
    done = pyqtSignal(list)

    def run(self):
        servers = list_sql_servers()
        self.done.emit(servers)


# =========================================================
#  Утилиты
# =========================================================

def get_app_dir() -> str:
    """Папка для скачивания установщика."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    here = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(os.path.dirname(here))
    return project_dir


def is_localdb_installed() -> bool:
    """Проверяет наличие LocalDB через SqlLocalDB.exe."""
    try:
        result = subprocess.run(
            ["SqlLocalDB.exe", "info"],
            capture_output=True, text=True, shell=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def get_localdb_version() -> str:
    """Возвращает версию LocalDB, если он установлен."""
    try:
        result = subprocess.run(
            ["SqlLocalDB.exe", "info"],
            capture_output=True, text=True, shell=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "MSSQLLocalDB" in line or "Version" in line:
                    return line.strip()
        return "установлен"
    except Exception:
        return ""


def _is_server_alive(server: str, timeout: int = 2) -> bool:
    """Быстрая проверка: отвечает ли SQL Server по указанному имени."""
    try:
        conn_str = (
            r"DRIVER={ODBC Driver 17 for SQL Server};"
            rf"SERVER={server};"
            r"DATABASE=master;"
            r"Trusted_Connection=yes;"
            r"Encrypt=no;"
            r"TrustServerCertificate=yes;"
            f"Connection Timeout={timeout};"
        )
        conn = pyodbc.connect(conn_str, timeout=timeout)
        conn.close()
        return True
    except Exception:
        return False


def list_sql_servers() -> list[str]:
    """
    Возвращает список SQL-серверов, которые реально отвечают.
    """
    candidates = [
        r"(localdb)\MSSQLLocalDB",
        r".\TEW_SQLEXPRESS",
        r".\SQLEXPRESS",
        r"localhost",
        r".",
    ]

    # Добавляем то, что нашёл sqlcmd -L
    try:
        result = subprocess.run(
            ["sqlcmd", "-L"],
            capture_output=True, text=True, shell=True, timeout=15
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "Servers:" in line or "SQL Server" in line:
                    continue
                candidates.append(line)
    except Exception:
        pass

    # Убираем дубликаты
    seen = set()
    unique = []
    for s in candidates:
        if s and s not in seen:
            unique.append(s)
            seen.add(s)

    # Проверяем каждый
    alive = []
    for s in unique:
        if _is_server_alive(s):
            alive.append(s)
    return alive


def attach_database_to_localdb(db_name: str, data_dir: str) -> tuple[bool, str]:
    """Подключает .mdf/.ldf из data_dir к LocalDB."""
    mdf_path = os.path.join(data_dir, f"{db_name}.mdf")
    ldf_path = os.path.join(data_dir, f"{db_name}_log.ldf")

    if not os.path.exists(mdf_path):
        return False, f"Файл данных не найден: {mdf_path}"
    if not os.path.exists(ldf_path):
        return False, f"Файл журнала не найден: {ldf_path}"

    try:
        conn_str = (
            r"DRIVER={ODBC Driver 17 for SQL Server};"
            r"SERVER=(localdb)\MSSQLLocalDB;"
            r"DATABASE=master;"
            r"Trusted_Connection=yes;"
            r"Encrypt=no;"
            r"TrustServerCertificate=yes;"
        )
        conn = pyodbc.connect(conn_str, autocommit=True)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sys.databases WHERE name = ?", db_name)
        if cur.fetchone():
            conn.close()
            return True, f"База '{db_name}' уже подключена к LocalDB"

        sql = (
            f"CREATE DATABASE [{db_name}] ON "
            f"(FILENAME = '{mdf_path}'), "
            f"(FILENAME = '{ldf_path}') "
            f"FOR ATTACH"
        )
        cur.execute(sql)
        conn.close()
        return True, f"База '{db_name}' успешно подключена к LocalDB"
    except Exception as e:
        return False, str(e)


def detach_database_from_server(server: str, db_name: str) -> tuple[bool, str]:
    """Отсоединяет базу от указанного SQL-сервера."""
    try:
        conn_str = (
            r"DRIVER={ODBC Driver 17 for SQL Server};"
            rf"SERVER={server};"
            r"DATABASE=master;"
            r"Trusted_Connection=yes;"
            r"Encrypt=no;"
            r"TrustServerCertificate=yes;"
        )
        conn = pyodbc.connect(conn_str, autocommit=True)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sys.databases WHERE name = ?", db_name)
        if not cur.fetchone():
            conn.close()
            return True, f"База '{db_name}' не найдена на сервере {server}"

        cur.execute(
            f"ALTER DATABASE [{db_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
        )
        cur.execute(f"EXEC sp_detach_db '{db_name}'")
        conn.close()
        return True, f"База '{db_name}' отсоединена от {server}"
    except Exception as e:
        return False, str(e)


# =========================================================
#  Вкладка «База данных»
# =========================================================

class DatabaseTab(QWidget):
    LOCALDB_VERSIONS = {
        "SQL Server 2022": (
            "https://download.microsoft.com/download/3/8/d/"
            "38de7036-2433-4207-8eae-06e247e17b25/SqlLocalDB.msi"
        ),
        "SQL Server 2019": (
            "https://download.microsoft.com/download/7/c/1/"
            "7c14e92e-bdcb-4f89-b7cf-93543e7112d1/SqlLocalDB.msi"
        ),
        "SQL Server 2016": (
            "https://download.microsoft.com/download/9/0/7/"
            "907AD35F-9F9C-43A5-9789-52470555DB90/ENU/SqlLocalDB.msi"
        ),
    }

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        cfg = load_config()

        # ============================================
        #  Секция 1: Установка LocalDB
        # ============================================
        localdb_group = QGroupBox("SQL Server Express LocalDB")
        localdb_layout = QVBoxLayout(localdb_group)

        self.status_label = QLabel("Проверка...")
        self.status_label.setWordWrap(True)
        localdb_layout.addWidget(self.status_label)

        # Выпадающий список версий
        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("Версия LocalDB:"))
        self.version_combo = QComboBox()
        self.version_combo.addItems(self.LOCALDB_VERSIONS.keys())
        self.version_combo.setCurrentText("SQL Server 2019")
        self.version_combo.currentTextChanged.connect(self.on_version_changed)
        version_row.addWidget(self.version_combo, stretch=1)
        localdb_layout.addLayout(version_row)

        # URL (редактируемый)
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL установщика:"))
        self.url_edit = QLineEdit(self.LOCALDB_VERSIONS["SQL Server 2019"])
        url_row.addWidget(self.url_edit, stretch=1)
        localdb_layout.addLayout(url_row)

        # Кнопки
        btn_row = QHBoxLayout()
        self.btn_download = QPushButton("Скачать")
        self.btn_install = QPushButton("Установить")
        self.btn_reinstall = QPushButton("Переустановить")
        self.btn_install.setEnabled(False)
        self.btn_reinstall.setVisible(False)
        btn_row.addWidget(self.btn_download)
        btn_row.addWidget(self.btn_install)
        btn_row.addWidget(self.btn_reinstall)
        btn_row.addStretch()
        localdb_layout.addLayout(btn_row)

        # Прогресс-бар
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        localdb_layout.addWidget(self.progress)

        # Статус процессов
        self.proc_status = QLabel("")
        self.proc_status.setWordWrap(True)
        localdb_layout.addWidget(self.proc_status)

        layout.addWidget(localdb_group)

        # ============================================
        #  Секция 2: Параметры соединения
        # ============================================
        conn_group = QGroupBox("Параметры соединения с SQL Server")
        conn_form = QFormLayout(conn_group)

        server_row = QHBoxLayout()
        self.server_combo = QComboBox()
        self.server_combo.setEditable(True)
        server_row.addWidget(self.server_combo, stretch=1)
        self.btn_refresh_servers = QPushButton("Обновить список")
        self.btn_refresh_servers.clicked.connect(self.refresh_servers)
        server_row.addWidget(self.btn_refresh_servers)
        conn_form.addRow("Сервер", server_row)

        self.db_edit = QLineEdit(cfg["database"])
        conn_form.addRow("База данных", self.db_edit)

        auth_row = QHBoxLayout()
        self.rb_windows = QRadioButton("Windows Authentication")
        self.rb_sql = QRadioButton("SQL Server Authentication")
        if cfg.get("auth") == "sql":
            self.rb_sql.setChecked(True)
        else:
            self.rb_windows.setChecked(True)
        auth_row.addWidget(self.rb_windows)
        auth_row.addWidget(self.rb_sql)
        conn_form.addRow("Аутентификация", auth_row)

        self.user_edit = QLineEdit(cfg.get("user", ""))
        self.pass_edit = QLineEdit(cfg.get("password", ""))
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        conn_form.addRow("Пользователь", self.user_edit)
        conn_form.addRow("Пароль", self.pass_edit)

        self.chk_encrypt = QCheckBox("Encrypt Connection")
        self.chk_encrypt.setChecked(cfg.get("encrypt", "no") == "yes")
        self.chk_trust = QCheckBox("Trust Server Certificate")
        self.chk_trust.setChecked(cfg.get("trust_cert", "yes") == "yes")
        conn_form.addRow("", self.chk_encrypt)
        conn_form.addRow("", self.chk_trust)

        layout.addWidget(conn_group)

        btn_conn_row = QHBoxLayout()
        self.btn_test = QPushButton("Проверить соединение")
        self.btn_save = QPushButton("Сохранить настройки")
        btn_conn_row.addWidget(self.btn_test)
        btn_conn_row.addWidget(self.btn_save)
        btn_conn_row.addStretch()
        layout.addLayout(btn_conn_row)

        self.conn_status = QLabel("")
        self.conn_status.setWordWrap(True)
        layout.addWidget(self.conn_status)

        # ============================================
        #  Секция 3: Работа с базой данных (attach/detach)
        # ============================================
        db_ops_group = QGroupBox("Работа с базой данных")
        db_ops_layout = QVBoxLayout(db_ops_group)

        data_dir_row = QHBoxLayout()
        data_dir_row.addWidget(QLabel("Папка с данными (.mdf/.ldf):"))
        self.data_dir_edit = QLineEdit(cfg.get("backup_dir", r"E:\Prog\Piton\data"))
        data_dir_row.addWidget(self.data_dir_edit, stretch=1)
        btn_data_browse = QPushButton("...")
        btn_data_browse.setFixedWidth(40)
        btn_data_browse.clicked.connect(self.on_browse_data_dir)
        data_dir_row.addWidget(btn_data_browse)
        db_ops_layout.addLayout(data_dir_row)

        ops_btn_row = QHBoxLayout()
        self.btn_attach = QPushButton("Подключить базу к LocalDB")
        self.btn_detach = QPushButton("Отсоединить базу от старого сервера")
        ops_btn_row.addWidget(self.btn_attach)
        ops_btn_row.addWidget(self.btn_detach)
        ops_btn_row.addStretch()
        db_ops_layout.addLayout(ops_btn_row)

        layout.addWidget(db_ops_group)

        # ============================================
        #  Секция 4: Бэкап
        # ============================================
        backup_group = QGroupBox("Резервное копирование")
        backup_layout = QVBoxLayout(backup_group)

        backup_dir_row = QHBoxLayout()
        backup_dir_row.addWidget(QLabel("Папка для бэкапов:"))
        self.backup_dir_edit = QLineEdit(cfg.get("backup_dir", r"E:\Prog\Piton\data"))
        backup_dir_row.addWidget(self.backup_dir_edit, stretch=1)
        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(40)
        btn_browse.clicked.connect(self.on_browse)
        backup_dir_row.addWidget(btn_browse)
        backup_layout.addLayout(backup_dir_row)

        btn_backup_row = QHBoxLayout()
        self.btn_backup = QPushButton("Сделать бэкап сейчас")
        btn_backup_row.addWidget(self.btn_backup)
        btn_backup_row.addStretch()
        backup_layout.addLayout(btn_backup_row)

        layout.addWidget(backup_group)
        layout.addStretch()

        # --- Сигналы ---
        self.btn_test.clicked.connect(self.on_test)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_backup.clicked.connect(self.on_backup)
        btn_browse.clicked.connect(self.on_browse)
        self.btn_download.clicked.connect(self.on_download)
        self.btn_install.clicked.connect(self.on_install)
        self.btn_reinstall.clicked.connect(self.on_download)
        self.btn_attach.clicked.connect(self.on_attach)
        self.btn_detach.clicked.connect(self.on_detach)

        # --- Таймер мигания ---
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_on = False

        # --- Начальная инициализация (в фоне) ---
        self.status_label.setText("⏳ Проверка LocalDB…")
        self.status_label.setStyleSheet("color: gray;")

        self._start_localdb_check()
        self._start_servers_refresh()

    # ============================================
    #  LocalDB: фоновые проверки
    # ============================================

    def _start_localdb_check(self):
        self._localdb_thread = LocalDBStatusThread()
        self._localdb_thread.done.connect(self._on_localdb_checked)
        self._localdb_thread.start()

    def _on_localdb_checked(self, installed: bool, version: str):
        if installed:
            self.status_label.setText(
                f"✅ LocalDB установлен: {version}\n"
                f"Можно использовать сервер: (localdb)\\MSSQLLocalDB"
            )
            self.status_label.setStyleSheet("color: green;")
            self.btn_download.setVisible(False)
            self.btn_install.setVisible(False)
            self.btn_reinstall.setVisible(True)
        else:
            app_dir = get_app_dir()
            msi_path = os.path.join(app_dir, "SqlLocalDB.msi")
            if os.path.exists(msi_path):
                size_mb = os.path.getsize(msi_path) / (1024 * 1024)
                self.status_label.setText(
                    f"❌ LocalDB не найден на этой машине.\n"
                    f"✅ Установщик уже скачан: {msi_path} ({size_mb:.1f} МБ)\n"
                    f"Нажмите «Установить»."
                )
                self.status_label.setStyleSheet("color: orange;")
                self.btn_download.setVisible(True)
                self.btn_download.setText("Перекачать")
                self.btn_install.setVisible(True)
                self.btn_install.setEnabled(True)
                self.btn_reinstall.setVisible(False)
            else:
                self.status_label.setText(
                    "❌ LocalDB не найден на этой машине.\n"
                    "Скачайте установщик и установите его."
                )
                self.status_label.setStyleSheet("color: red;")
                self.btn_download.setVisible(True)
                self.btn_download.setText("Скачать")
                self.btn_install.setVisible(True)
                self.btn_install.setEnabled(False)
                self.btn_reinstall.setVisible(False)

    def on_version_changed(self, version_name: str):
        self.url_edit.setText(self.LOCALDB_VERSIONS.get(version_name, ""))

    def on_download(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Ошибка", "Укажите URL установщика")
            return

        app_dir = get_app_dir()
        dest = os.path.join(app_dir, "SqlLocalDB.msi")

        if os.path.exists(dest):
            ans = QMessageBox.question(
                self, "Файл существует",
                f"Файл уже существует:\n{dest}\n\nПерекачать?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if ans != QMessageBox.StandardButton.Yes:
                self.btn_install.setEnabled(True)
                self.proc_status.setText(f"Используется существующий файл: {dest}")
                return

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.proc_status.setText(f"Скачивание в {dest}...")
        self.btn_download.setEnabled(False)

        self.dl_thread = DownloadThread(url, dest)
        self.dl_thread.progress.connect(self.progress.setValue)
        self.dl_thread.finished.connect(self._on_download_finished)
        self.dl_thread.error.connect(self._on_download_error)
        self.dl_thread.start()

    def _on_download_finished(self, path):
        self.proc_status.setText(f"✅ Скачано: {path}")
        self.progress.setValue(100)
        self.btn_download.setEnabled(True)
        self.btn_install.setEnabled(True)

    def _on_download_error(self, msg):
        self.proc_status.setText(f"❌ Ошибка скачивания: {msg}")
        self.progress.setVisible(False)
        self.btn_download.setEnabled(True)

    def on_install(self):
        app_dir = get_app_dir()
        msi_path = os.path.join(app_dir, "SqlLocalDB.msi")
        if not os.path.exists(msi_path):
            QMessageBox.warning(
                self, "Ошибка",
                f"Файл не найден:\n{msi_path}\nСначала скачайте."
            )
            return

        ans = QMessageBox.question(
            self, "Установка LocalDB",
            "Установка требует прав администратора.\n"
            "Если программа запущена без прав — установка упадёт с кодом 1603.\n"
            "Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.proc_status.setText("Установка LocalDB... (может занять несколько минут)")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.btn_install.setEnabled(False)

        self.inst_thread = InstallThread(msi_path)
        self.inst_thread.finished.connect(self._on_install_finished)
        self.inst_thread.start()

    def _on_install_finished(self, ok, msg):
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        if ok:
            self.proc_status.setText(f"✅ {msg}")
            self._start_localdb_check()
        else:
            self.proc_status.setText(f"❌ Ошибка установки: {msg}")
            self.btn_install.setEnabled(True)

    # ============================================
    #  Мигание статуса поиска серверов
    # ============================================

    def _start_blinking(self, text: str):
        self.conn_status.setText(f"⏳ {text}")
        self.conn_status.setStyleSheet("color: red; font-weight: bold;")
        self._blink_on = True
        self._blink_timer.start()

    def _stop_blinking(self, text: str, color: str = "gray"):
        self._blink_timer.stop()
        self.conn_status.setText(text)
        self.conn_status.setStyleSheet(f"color: {color}; font-weight: normal;")

    def _blink_tick(self):
        if self._blink_on:
            self.conn_status.setStyleSheet("color: darkred; font-weight: bold;")
        else:
            self.conn_status.setStyleSheet("color: red; font-weight: bold;")
        self._blink_on = not self._blink_on

    # ============================================
    #  Список серверов (фоновый поиск)
    # ============================================

    def _start_servers_refresh(self):
        self._start_blinking("Поиск SQL-серверов…")
        self._servers_thread = ServersListThread()
        self._servers_thread.done.connect(self._on_servers_refreshed)
        self._servers_thread.start()

    def _on_servers_refreshed(self, servers: list):
        cfg = load_config()
        current = cfg.get("server", "")

        self.server_combo.clear()
        for s in servers:
            self.server_combo.addItem(s)
        if current:
            self.server_combo.setCurrentText(current)

        if servers:
            self._stop_blinking(f"✅ Найдено серверов: {len(servers)}", "green")
        else:
            self._stop_blinking("⚠ Серверы не найдены", "orange")

    def refresh_servers(self):
        self._start_servers_refresh()

    # ============================================
    #  Соединение
    # ============================================

    def _collect_cfg(self) -> dict:
        return {
            "server": self.server_combo.currentText().strip(),
            "database": self.db_edit.text().strip(),
            "auth": "sql" if self.rb_sql.isChecked() else "windows",
            "user": self.user_edit.text().strip(),
            "password": self.pass_edit.text(),
            "encrypt": "yes" if self.chk_encrypt.isChecked() else "no",
            "trust_cert": "yes" if self.chk_trust.isChecked() else "no",
            "backup_dir": self.backup_dir_edit.text().strip(),
        }

    def on_test(self):
        cfg = self._collect_cfg()
        ok, msg = test_connection(cfg)
        if ok:
            self.conn_status.setText(f"✅ Соединение успешно: {msg}")
            self.conn_status.setStyleSheet("color: green;")
        else:
            self.conn_status.setText(f"❌ Ошибка: {msg}")
            self.conn_status.setStyleSheet("color: red;")

    def on_save(self):
        cfg = self._collect_cfg()
        save_config(cfg)
        reload_config()
        QMessageBox.information(
            self, "Сохранено",
            "Настройки сохранены в config.ini.\nПерезапустите программу."
        )
        self.conn_status.setText("Настройки сохранены. Требуется перезапуск.")
        self.conn_status.setStyleSheet("color: blue;")

    # ============================================
    #  Attach / Detach
    # ============================================

    def on_attach(self):
        db_name = self.db_edit.text().strip() or "ProtocolDB"
        data_dir = self.data_dir_edit.text().strip()

        if not os.path.isdir(data_dir):
            QMessageBox.warning(self, "Ошибка", f"Папка не существует:\n{data_dir}")
            return

        ans = QMessageBox.question(
            self, "Подключить базу",
            f"Подключить базу '{db_name}' из\n{data_dir}\nк LocalDB?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        ok, msg = attach_database_to_localdb(db_name, data_dir)
        if ok:
            self.conn_status.setText(f"✅ {msg}")
            self.conn_status.setStyleSheet("color: green;")
            self.server_combo.setCurrentText(r"(localdb)\MSSQLLocalDB")

            save_config(self._collect_cfg())
            reload_config()

            box = QMessageBox(self)
            box.setWindowTitle("База подключена")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(f"{msg}\n\nПерезапустить программу сейчас?")
            btn_restart = box.addButton("Перезапустить", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
            box.exec()

            if box.clickedButton() == btn_restart:
                self._restart_app()
        else:
            QMessageBox.critical(self, "Ошибка", msg)
            self.conn_status.setText(f"❌ {msg}")
            self.conn_status.setStyleSheet("color: red;")

    def _restart_app(self):
        python = sys.executable
        script = os.path.abspath(sys.argv[0])
        args = sys.argv[1:]

        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

        subprocess.Popen([python, script, *args])

    def on_detach(self):
        server = self.server_combo.currentText().strip()
        db_name = self.db_edit.text().strip() or "ProtocolDB"

        ans = QMessageBox.question(
            self, "Отсоединить базу",
            f"Отсоединить базу '{db_name}' от сервера:\n{server}?\n\n"
            f"Все соединения будут разорваны.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        ok, msg = detach_database_from_server(server, db_name)
        if ok:
            QMessageBox.information(self, "Успех", msg)
            self.conn_status.setText(f"✅ {msg}")
            self.conn_status.setStyleSheet("color: green;")
        else:
            QMessageBox.critical(self, "Ошибка", msg)
            self.conn_status.setText(f"❌ {msg}")
            self.conn_status.setStyleSheet("color: red;")

    # ============================================
    #  Бэкап
    # ============================================

    def on_backup(self):
        cfg = self._collect_cfg()
        backup_dir = cfg["backup_dir"]
        if not os.path.isdir(backup_dir):
            QMessageBox.warning(self, "Ошибка", f"Папка не существует:\n{backup_dir}")
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        db_name = cfg["database"] or "ProtocolDB"
        backup_path = os.path.join(backup_dir, f"{db_name}_{ts}.bak")

        ok, msg = make_backup(cfg, backup_path)
        if ok:
            QMessageBox.information(self, "Бэкап", msg)
            self.conn_status.setText(f"✅ {msg}")
            self.conn_status.setStyleSheet("color: green;")
        else:
            QMessageBox.critical(self, "Ошибка бэкапа", msg)
            self.conn_status.setText(f"❌ {msg}")
            self.conn_status.setStyleSheet("color: red;")

    def on_browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "Выберите папку для бэкапов", self.backup_dir_edit.text()
        )
        if d:
            self.backup_dir_edit.setText(d)

    def on_browse_data_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Выберите папку с файлами базы данных", self.data_dir_edit.text()
        )
        if d:
            self.data_dir_edit.setText(d)