import os
import sys
import subprocess
import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QCheckBox, QRadioButton,
    QMessageBox, QFileDialog, QProgressBar, QComboBox, QTextEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

sys.path.insert(0, r"E:\Prog\Piton\src")
from db import test_connection, make_backup, reload_config, restore_backup
from config import load_config, save_config, build_connection_string
from migrator import Migrator

import pyodbc


def build_restart_command() -> list[str]:
    """Возвращает команду, используемую штатным перезапуском приложения."""
    return [
        sys.executable,
        os.path.abspath(sys.argv[0]),
        *sys.argv[1:],
    ]


# =========================================================
#  Потоки
# =========================================================

class DownloadThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, dest_path: str):
        super().__init__()
        self.url = url
        self.dest_path = dest_path

    def run(self):
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/119.0"}
            response = requests.get(self.url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            with open(self.dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        self.progress.emit(min(100, int(downloaded * 100 / total_size)))
            self.finished.emit(self.dest_path)
        except Exception as e:
            self.error.emit(str(e))


class InstallThread(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, msi_path: str):
        super().__init__()
        self.msi_path = msi_path

    def run(self):
        try:
            cmd = ["msiexec.exe", "/i", self.msi_path,
                   "IACCEPTSQLLOCALDBLICENSETERMS=YES", "/qn", "/norestart"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                self.finished.emit(True, "LocalDB установлен")
            else:
                self.finished.emit(False, f"Код {result.returncode}: {result.stderr}")
        except Exception as e:
            self.finished.emit(False, str(e))


class MigrationThread(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, source_dir: str, target_dir: str, server: str, database: str):
        super().__init__()
        self.source_dir = source_dir
        self.target_dir = target_dir
        self.server = server
        self.database = database
        self.restart_scheduled = False

    def run(self):
        log_file = None
        migrator = None
        try:
            log_dir = os.path.join(get_app_dir(), "data")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "migration.log")
            log_file = open(log_path, "a", encoding="utf-8")
            log_file.write(
                f"\n=== Начало миграции: "
                f"{datetime.datetime.now().isoformat(timespec='seconds')} ===\n"
            )
            log_file.flush()

            def write_log(message: str):
                log_file.write(
                    f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                    f"{message}\n"
                )
                log_file.flush()
                self.log.emit(message)

            cfg = {
                "server": self.server,
                "database": self.database,
                "auth": "windows",
                "user": "",
                "password": "",
                "encrypt": "no",
                "trust_cert": "yes",
                "backup_dir": self.source_dir,
            }

            def conn_builder(db_name):
                c = cfg.copy()
                c["database"] = db_name
                return build_connection_string(c)

            migrator = Migrator(
                source_dir=self.source_dir,
                target_dir=self.target_dir,
                sql_server=self.server,
                sql_database=self.database,
                sql_conn_str_builder=conn_builder,
                log=write_log,
                progress=lambda p: self.progress.emit(p),
                cleanup_log_path=log_path,
            )
            migrator.run()
            restart_command = build_restart_command()

            if migrator.access_driver_installed_by_migration:
                self.restart_scheduled = migrator._schedule_access_driver_uninstall(
                    restart_command
                )
                if (
                    not self.restart_scheduled
                    and not migrator.access_driver_cleanup_started
                ):
                    self.log.emit(
                        "Не удалось запланировать удаление Access Engine; "
                        "запланирована попытка перезапуска без удаления."
                    )
                    self.restart_scheduled = migrator.schedule_application_restart(
                        restart_command
                    )
                elif not self.restart_scheduled:
                    self.log.emit(
                        "Удаление Access Engine запущено, но помощник "
                        "перезапуска не удалось запланировать."
                    )
            else:
                self.restart_scheduled = migrator.schedule_application_restart(
                    restart_command
                )

            if self.restart_scheduled:
                completion_message = (
                    "Миграция завершена. Программа сейчас перезапустится."
                )
            else:
                completion_message = (
                    "Миграция завершена, но автоматический перезапуск "
                    "не удалось запланировать."
                )
            write_log(completion_message)
            self.finished.emit(True, completion_message)
        except Exception as e:
            import traceback
            details = traceback.format_exc()
            if log_file is not None:
                try:
                    log_file.write(details + "\n")
                    log_file.flush()
                except OSError as log_error:
                    self.log.emit(
                        f"Не удалось записать ошибку в журнал миграции: {log_error}"
                    )
            self.log.emit(details)
            if migrator and migrator.access_driver_installed_by_migration:
                try:
                    if not migrator._schedule_access_driver_uninstall():
                        self.log.emit(
                            "Не удалось запланировать удаление Access Engine "
                            "после ошибки миграции."
                        )
                except Exception as cleanup_error:
                    self.log.emit(
                        f"Ошибка планирования удаления Access Engine: {cleanup_error}"
                    )
            self.finished.emit(False, str(e))
        finally:
            if log_file is not None:
                log_file.close()


class LocalDBStatusThread(QThread):
    done = pyqtSignal(bool, str)

    def run(self):
        installed = is_localdb_installed()
        version = get_localdb_version() if installed else ""
        self.done.emit(installed, version)


class ServersListThread(QThread):
    done = pyqtSignal(list)

    def run(self):
        self.done.emit(list_sql_servers())


# =========================================================
#  Утилиты
# =========================================================

def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def is_localdb_installed() -> bool:
    try:
        result = subprocess.run(["SqlLocalDB.exe", "info"],
                                capture_output=True, text=True, shell=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def get_localdb_version() -> str:
    try:
        result = subprocess.run(["SqlLocalDB.exe", "info"],
                                capture_output=True, text=True, shell=True, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "MSSQLLocalDB" in line:
                    return line.strip()
        return "установлен"
    except Exception:
        return ""


def _is_server_alive(server: str, timeout: int = 2) -> bool:
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
    candidates = [
        r"(localdb)\MSSQLLocalDB",
        r".\TEW_SQLEXPRESS",
        r".\SQLEXPRESS",
        r"localhost",
        r".",
    ]
    try:
        result = subprocess.run(["sqlcmd", "-L"],
                                capture_output=True, text=True, shell=True, timeout=15)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or "Servers:" in line or "SQL Server" in line:
                    continue
                candidates.append(line)
    except Exception:
        pass
    seen = set()
    unique = []
    for s in candidates:
        if s and s not in seen:
            unique.append(s)
            seen.add(s)
    return [s for s in unique if _is_server_alive(s)]


def attach_database_to_localdb(db_name: str, data_dir: str) -> tuple[bool, str]:
    data_dir = os.path.normpath(data_dir)
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
            return True, f"База '{db_name}' уже подключена"
        sql = (
            f"CREATE DATABASE [{db_name}] ON "
            f"(FILENAME = '{mdf_path}'), "
            f"(FILENAME = '{ldf_path}') "
            f"FOR ATTACH"
        )
        cur.execute(sql)
        conn.close()
        return True, f"База '{db_name}' подключена"
    except Exception as e:
        return False, str(e)


def detach_database_from_server(server: str, db_name: str) -> tuple[bool, str]:
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
            return True, f"База '{db_name}' не найдена на {server}"
        cur.execute(f"ALTER DATABASE [{db_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        cur.execute(f"EXEC sp_detach_db '{db_name}'")
        conn.close()
        return True, f"База '{db_name}' отсоединена"
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
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)
        cfg = load_config()

        # ============================================
        #  Секция 1: Установка LocalDB
        # ============================================
        localdb_group = QGroupBox("SQL Server Express LocalDB")
        localdb_layout = QVBoxLayout(localdb_group)
        localdb_layout.setSpacing(4)

        self.status_label = QLabel("Проверка...")
        self.status_label.setWordWrap(True)
        self.status_label.setMaximumHeight(24)
        localdb_layout.addWidget(self.status_label)

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("Версия:"))
        self.version_combo = QComboBox()
        self.version_combo.addItems(self.LOCALDB_VERSIONS.keys())
        self.version_combo.setCurrentText("SQL Server 2019")
        self.version_combo.currentTextChanged.connect(self.on_version_changed)
        version_row.addWidget(self.version_combo, stretch=1)
        localdb_layout.addLayout(version_row)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit(self.LOCALDB_VERSIONS["SQL Server 2019"])
        url_row.addWidget(self.url_edit, stretch=1)
        localdb_layout.addLayout(url_row)

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

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(14)
        localdb_layout.addWidget(self.progress)

        self.proc_status = QLabel("")
        self.proc_status.setWordWrap(True)
        self.proc_status.setMaximumHeight(24)
        localdb_layout.addWidget(self.proc_status)

        layout.addWidget(localdb_group)

        # ============================================
        #  Секция 2: Параметры соединения
        # ============================================
        conn_group = QGroupBox("Параметры соединения")
        conn_form = QFormLayout(conn_group)
        conn_form.setSpacing(3)

        server_row = QHBoxLayout()
        self.server_combo = QComboBox()
        self.server_combo.setEditable(True)
        server_row.addWidget(self.server_combo, stretch=1)
        self.btn_refresh_servers = QPushButton("↻")
        self.btn_refresh_servers.setFixedWidth(28)
        self.btn_refresh_servers.clicked.connect(self.refresh_servers)
        server_row.addWidget(self.btn_refresh_servers)
        conn_form.addRow("Сервер", server_row)

        self.db_edit = QLineEdit(cfg["database"])
        conn_form.addRow("База", self.db_edit)

        auth_row = QHBoxLayout()
        self.rb_windows = QRadioButton("Windows")
        self.rb_sql = QRadioButton("SQL")
        if cfg.get("auth") == "sql":
            self.rb_sql.setChecked(True)
        else:
            self.rb_windows.setChecked(True)
        auth_row.addWidget(self.rb_windows)
        auth_row.addWidget(self.rb_sql)
        auth_row.addStretch()
        conn_form.addRow("Аутент.", auth_row)

        options_row = QHBoxLayout()
        self.chk_encrypt = QCheckBox("Encrypt")
        self.chk_encrypt.setChecked(cfg.get("encrypt", "no") == "yes")
        self.chk_trust = QCheckBox("Trust Server Cert")
        self.chk_trust.setChecked(cfg.get("trust_cert", "yes") == "yes")
        options_row.addWidget(self.chk_encrypt)
        options_row.addWidget(self.chk_trust)
        options_row.addStretch()
        conn_form.addRow("Опции", options_row)

        layout.addWidget(conn_group)

        btn_conn_row = QHBoxLayout()
        self.btn_test = QPushButton("Проверить соединение")
        self.btn_save = QPushButton("Сохранить настройки")
        self.btn_restart = QPushButton("Перезапустить программу")
        self.btn_restart.setVisible(False)
        btn_conn_row.addWidget(self.btn_test)
        btn_conn_row.addWidget(self.btn_save)
        btn_conn_row.addWidget(self.btn_restart)
        btn_conn_row.addStretch()
        layout.addLayout(btn_conn_row)

        self.conn_status = QLabel("")
        self.conn_status.setWordWrap(True)
        self.conn_status.setMaximumHeight(30)
        layout.addWidget(self.conn_status)

        # ============================================
        #  Секция 3: Миграция
        # ============================================
        migr_group = QGroupBox("Миграция данных из Access")
        migr_layout = QVBoxLayout(migr_group)
        migr_layout.setSpacing(4)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Папка с .mdb:"))
        self.data_dir_edit = QLineEdit(cfg.get("backup_dir", r"E:\Prog\Piton\data"))
        dir_row.addWidget(self.data_dir_edit, stretch=1)
        btn_dir = QPushButton("...")
        btn_dir.setFixedWidth(28)
        btn_dir.clicked.connect(self.on_browse_data_dir)
        dir_row.addWidget(btn_dir)
        migr_layout.addLayout(dir_row)

        migr_btn_row = QHBoxLayout()
        self.btn_migrate = QPushButton("Запустить миграцию")
        migr_btn_row.addWidget(self.btn_migrate)
        migr_btn_row.addStretch()
        migr_layout.addLayout(migr_btn_row)

        migr_layout.addWidget(QLabel(
            "Ищутся базы: Protocol2.mdb, ProtocolEditTemplates.mdb, ProtocolImages.mdb"
        ))

        self.migr_progress = QProgressBar()
        self.migr_progress.setValue(0)
        self.migr_progress.setMaximumHeight(14)
        migr_layout.addWidget(self.migr_progress)

        self.migr_log = QTextEdit()
        self.migr_log.setReadOnly(True)
        self.migr_log.setMaximumHeight(70)
        migr_layout.addWidget(self.migr_log)

        layout.addWidget(migr_group)

        # ============================================
        #  Секция 4: Для переноса и копирования
        # ============================================
        transfer_group = QGroupBox("Для переноса и копирования")
        transfer_layout = QVBoxLayout(transfer_group)
        transfer_layout.setSpacing(4)

        transfer_btn_row = QHBoxLayout()
        self.btn_detach = QPushButton("Отсоединить базу от старого сервера")
        self.btn_attach = QPushButton("Подключить базу к LocalDB")
        transfer_btn_row.addWidget(self.btn_detach)
        transfer_btn_row.addWidget(self.btn_attach)
        transfer_btn_row.addStretch()
        transfer_layout.addLayout(transfer_btn_row)

        transfer_hint = QLabel(
            "Отсоединяет базу от текущего SQL Server, чтобы файлы .mdf/.ldf "
            "можно было перенести на другой компьютер."
        )
        transfer_hint.setStyleSheet("color: gray;")
        transfer_hint.setWordWrap(True)
        transfer_layout.addWidget(transfer_hint)

        layout.addWidget(transfer_group)

        # ============================================
        #  Секция 5: Резервное копирование
        # ============================================
        backup_group = QGroupBox("Резервное копирование")
        backup_layout = QVBoxLayout(backup_group)
        backup_layout.setSpacing(4)

        backup_dir_row = QHBoxLayout()
        backup_dir_row.addWidget(QLabel("Папка для бэкапов:"))
        self.backup_dir_edit = QLineEdit(cfg.get("backup_dir", r"E:\Prog\Piton\data"))
        backup_dir_row.addWidget(self.backup_dir_edit, stretch=1)
        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(28)
        btn_browse.clicked.connect(self.on_browse)
        backup_dir_row.addWidget(btn_browse)
        backup_layout.addLayout(backup_dir_row)

        backup_btn_row = QHBoxLayout()
        self.btn_backup = QPushButton("Сделать бэкап")
        self.btn_restore = QPushButton("Восстановить из бэкапа")
        backup_btn_row.addWidget(self.btn_backup)
        backup_btn_row.addWidget(self.btn_restore)
        backup_btn_row.addStretch()
        backup_layout.addLayout(backup_btn_row)

        layout.addWidget(backup_group)
        layout.addStretch()

        # ============================================
        #  Сигналы
        # ============================================
        self.btn_test.clicked.connect(self.on_test)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_restart.clicked.connect(self.on_restart)
        self.btn_backup.clicked.connect(self.on_backup)
        self.btn_restore.clicked.connect(self.on_restore)
        btn_browse.clicked.connect(self.on_browse)
        self.btn_download.clicked.connect(self.on_download)
        self.btn_install.clicked.connect(self.on_install)
        self.btn_reinstall.clicked.connect(self.on_download)
        self.btn_attach.clicked.connect(self.on_attach)
        self.btn_detach.clicked.connect(self.on_detach)
        self.btn_migrate.clicked.connect(self.on_migrate)

        # ============================================
        #  Таймер мигания
        # ============================================
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_on = False

        # ============================================
        #  Инициализация
        # ============================================
        self.status_label.setText("⏳ Проверка LocalDB…")
        self.status_label.setStyleSheet("color: gray;")

        self._start_localdb_check()
        self._start_servers_refresh()

    # ============================================
    #  LocalDB
    # ============================================

    def _start_localdb_check(self):
        self._localdb_thread = LocalDBStatusThread()
        self._localdb_thread.done.connect(self._on_localdb_checked)
        self._localdb_thread.start()

    def _on_localdb_checked(self, installed: bool, version: str):
        if installed:
            self.status_label.setText(f"✅ LocalDB установлен: {version}")
            self.status_label.setStyleSheet("color: green;")
            self.btn_download.setVisible(False)
            self.btn_install.setVisible(False)
            self.btn_reinstall.setVisible(True)
        else:
            app_dir = get_app_dir()
            msi_path = os.path.join(app_dir, "SqlLocalDB.msi")
            if os.path.exists(msi_path):
                size_mb = os.path.getsize(msi_path) / (1024 * 1024)
                self.status_label.setText(f"❌ LocalDB не найден. Установщик: {size_mb:.1f} МБ")
                self.status_label.setStyleSheet("color: orange;")
                self.btn_download.setVisible(True)
                self.btn_download.setText("Перекачать")
                self.btn_install.setVisible(True)
                self.btn_install.setEnabled(True)
                self.btn_reinstall.setVisible(False)
            else:
                self.status_label.setText("❌ LocalDB не найден. Нажмите «Скачать».")
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
            QMessageBox.warning(self, "Ошибка", "Укажите URL")
            return
        app_dir = get_app_dir()
        dest = os.path.join(app_dir, "SqlLocalDB.msi")
        if os.path.exists(dest):
            ans = QMessageBox.question(
                self, "Файл существует", f"Перекачать?\n{dest}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if ans != QMessageBox.StandardButton.Yes:
                self.btn_install.setEnabled(True)
                self.proc_status.setText(f"Используется: {dest}")
                return
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.proc_status.setText("Скачивание...")
        self.btn_download.setEnabled(False)
        self.dl_thread = DownloadThread(url, dest)
        self.dl_thread.progress.connect(self.progress.setValue)
        self.dl_thread.finished.connect(self._on_download_finished)
        self.dl_thread.error.connect(self._on_download_error)
        self.dl_thread.start()

    def _on_download_finished(self, path):
        self.proc_status.setText("✅ Скачано")
        self.progress.setValue(100)
        self.btn_download.setEnabled(True)
        self.btn_install.setEnabled(True)

    def _on_download_error(self, msg):
        self.proc_status.setText(f"❌ {msg}")
        self.progress.setVisible(False)
        self.btn_download.setEnabled(True)

    def on_install(self):
        app_dir = get_app_dir()
        msi_path = os.path.join(app_dir, "SqlLocalDB.msi")
        if not os.path.exists(msi_path):
            QMessageBox.warning(self, "Ошибка", f"Файл не найден:\n{msi_path}")
            return
        ans = QMessageBox.question(
            self, "Установка", "Требуются права администратора.\nПродолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.proc_status.setText("Установка...")
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
            self.proc_status.setText(f"❌ {msg}")
            self.btn_install.setEnabled(True)

    # ============================================
    #  Мигание
    # ============================================

    def _start_blinking(self, text: str):
        self.conn_status.setText(f"⏳ {text}")
        self.conn_status.setStyleSheet("color: red; font-weight: bold;")
        self._blink_on = True
        self._blink_timer.start()

    def _stop_blinking(self, text: str, color: str = "gray"):
        self._blink_timer.stop()
        self.conn_status.setText(text)
        self.conn_status.setStyleSheet(f"color: {color};")

    def _blink_tick(self):
        if self._blink_on:
            self.conn_status.setStyleSheet("color: darkred; font-weight: bold;")
        else:
            self.conn_status.setStyleSheet("color: red; font-weight: bold;")
        self._blink_on = not self._blink_on

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
            self._stop_blinking(f"✅ Найдено: {len(servers)}", "green")
        else:
            self._stop_blinking("⚠ Не найдено", "orange")

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
            "user": "",
            "password": "",
            "encrypt": "yes" if self.chk_encrypt.isChecked() else "no",
            "trust_cert": "yes" if self.chk_trust.isChecked() else "no",
            "backup_dir": os.path.normpath(self.backup_dir_edit.text().strip()),
        }

    def on_test(self):
        cfg = self._collect_cfg()
        ok, msg = test_connection(cfg)
        if ok:
            self.conn_status.setText(f"✅ {msg}")
            self.conn_status.setStyleSheet("color: green;")
        else:
            self.conn_status.setText(f"❌ {msg}")
            self.conn_status.setStyleSheet("color: red;")

    def on_save(self):
        cfg = self._collect_cfg()
        save_config(cfg)
        reload_config()
        self.conn_status.setText("✅ Настройки сохранены. Требуется перезапуск.")
        self.conn_status.setStyleSheet("color: blue;")
        self.btn_restart.setVisible(True)

    # ============================================
    #  Миграция
    # ============================================

    def on_migrate(self):
        data_dir = os.path.normpath(self.data_dir_edit.text().strip())
        if not os.path.isdir(data_dir):
            QMessageBox.warning(self, "Ошибка", f"Папка не существует:\n{data_dir}")
            return

        target_dir = os.path.normpath(get_app_dir() + r"\data")
        os.makedirs(target_dir, exist_ok=True)

        server = self.server_combo.currentText().strip()
        database = self.db_edit.text().strip() or "ProtocolDB"

        ans = QMessageBox.question(
            self, "Миграция",
            f"Мигрировать в базу '{database}'?\n\n"
            f"Источник (.mdb): {data_dir}\n"
            f"Файлы базы: {target_dir}\n\n"
            f"Внимание: база будет пересоздана!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.migr_progress.setValue(0)
        self.migr_log.clear()
        self.btn_migrate.setEnabled(False)

        self.migr_thread = MigrationThread(data_dir, target_dir, server, database)
        self.migr_thread.log.connect(self._on_migr_log)
        self.migr_thread.progress.connect(self.migr_progress.setValue)
        self.migr_thread.finished.connect(self._on_migr_finished)
        self.migr_thread.start()

    def _on_migr_log(self, msg: str):
        self.migr_log.append(msg)

    def _on_migr_finished(self, ok: bool, msg: str):
        try:
            self.btn_migrate.setEnabled(True)
            if ok:
                self.conn_status.setText(f"✅ {msg}")
                self.conn_status.setStyleSheet("color: green;")
                if self.migr_thread.restart_scheduled:
                    from PyQt6.QtWidgets import QApplication
                    QApplication.instance().quit()
                else:
                    QMessageBox.information(self, "Миграция", f"✅ {msg}")
            else:
                QMessageBox.critical(self, "Ошибка миграции", msg)
                self.conn_status.setText(f"❌ {msg}")
                self.conn_status.setStyleSheet("color: red;")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка после миграции", f"{e}")

    # ============================================
    #  Attach / Detach
    # ============================================

    def on_attach(self):
        db_name = self.db_edit.text().strip() or "ProtocolDB"
        data_dir = os.path.normpath(self.data_dir_edit.text().strip())
        if not os.path.isdir(data_dir):
            QMessageBox.warning(self, "Ошибка", f"Папка не существует:\n{data_dir}")
            return
        ans = QMessageBox.question(
            self, "Подключить базу",
            f"Подключить '{db_name}' из\n{data_dir}\nк LocalDB?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        ok, msg = attach_database_to_localdb(db_name, data_dir)
        if ok:
            QMessageBox.information(self, "Успех", msg)
            self.conn_status.setText(f"✅ {msg}")
            self.conn_status.setStyleSheet("color: green;")
            self.server_combo.setCurrentText(r"(localdb)\MSSQLLocalDB")
        else:
            QMessageBox.critical(self, "Ошибка", msg)

    def on_detach(self):
        server = self.server_combo.currentText().strip()
        db_name = self.db_edit.text().strip() or "ProtocolDB"
        ans = QMessageBox.question(
            self, "Отсоединить",
            f"Отсоединить '{db_name}' от {server}?",
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

    # ============================================
    #  Бэкап / восстановление
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
            QMessageBox.critical(self, "Ошибка", msg)
            self.conn_status.setText(f"❌ {msg}")
            self.conn_status.setStyleSheet("color: red;")

    def on_restore(self):
        cfg = self._collect_cfg()
        db_name = cfg["database"] or "ProtocolDB"

        backup_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл бэкапа (.bak)",
            cfg.get("backup_dir", ""),
            "SQL Server Backup (*.bak);;Все файлы (*)"
        )
        if not backup_path:
            return

        ans = QMessageBox.question(
            self, "Восстановление базы",
            f"Восстановить базу '{db_name}' из файла:\n{backup_path}?\n\n"
            f"ВНИМАНИЕ: текущая база '{db_name}' будет УДАЛЕНА и заменена!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        
        ok, msg = restore_backup(cfg, backup_path)
        if ok:
            QMessageBox.information(self, "Восстановлено", msg)
            self.conn_status.setText(f"✅ {msg}")
            self.conn_status.setStyleSheet("color: green;")
        else:
            QMessageBox.critical(self, "Ошибка восстановления", msg)
            self.conn_status.setText(f"❌ {msg}")
            self.conn_status.setStyleSheet("color: red;")

    # ============================================
    #  Папки
    # ============================================

    def on_browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "Выберите папку для бэкапов", self.backup_dir_edit.text()
        )
        if d:
            self.backup_dir_edit.setText(d)

    def on_browse_data_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Выберите папку с .mdb-файлами", self.data_dir_edit.text()
        )
        if d:
            self.data_dir_edit.setText(d)

    def on_restart(self):
        """Перезапускает текущий процесс программы."""
        import subprocess

        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()
        subprocess.Popen(build_restart_command())