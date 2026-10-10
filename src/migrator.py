"""
Миграция Access (.mdb) → SQL Server (LocalDB).

Порядок:
  1. Проверка/установка Access Database Engine (через UAC).
  2. CREATE DATABASE без указания пути.
  3. sp_detach_db → копирование .mdf/.ldf в target_dir → FOR ATTACH.
  4. Мигрируем таблицы, сохраняя оригинальные ID.
  5. DBCC CHECKIDENT — пересчёт счётчика.
  6. Деинсталляция Access Database Engine после завершения приложения,
     только если движок был установлен самой миграцией.
"""

import os
import sys
import time
import shutil
import subprocess
import getpass
import ctypes
import winreg
import pyodbc
import base64
import re
import uuid
from datetime import datetime
from typing import Callable, Optional


MDB_FILES = [
    "Protocol2.mdb",
    "ProtocolEditTemplates.mdb",
    "ProtocolImages.mdb",
]

IDENTITY_FIELDS = {
    "Patsient": "PatsientID",
    "Protocol": "ProtocolID",
    "Zakluchenie": "ZakluchenieID",
    "Vrach": "VrachID",
    "VrachType": "VrachTypeID",
    "Napravlenie": "NapravlenieID",
    "ProtocolEditTemplate": "ProtocolEditTemplateID",
    "ProtocolImage": "ImageID",
}


def run_as_admin(exe_path: str, params: str) -> bool:
    """Запускает exe с правами администратора (UAC-запрос)."""
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            exe_path,
            params,
            None,
            0  # SW_HIDE
        )
        return int(result) > 32
    except Exception as e:
        print(f"[ERROR] run_as_admin: {e}")
        return False


def run_detached(exe_path: str, params: str) -> bool:
    """Запускает процесс в текущем пользовательском контексте без ожидания."""
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "open",
            exe_path,
            params,
            None,
            0,
        )
        return int(result) > 32
    except Exception as e:
        print(f"[ERROR] run_detached: {e}")
        return False


def map_type(access_type: str, size: int, is_identity: bool = False) -> str:
    t = access_type.upper()
    if is_identity:
        return "INT IDENTITY(1,1)"
    if "COUNTER" in t or "AUTOINCREMENT" in t:
        return "INT IDENTITY(1,1)"
    if "SMALLINT" in t:
        return "SMALLINT"
    if "BYTE" in t:
        return "TINYINT"
    if "INT" in t or "LONG" in t:
        return "INT"
    if "CURRENCY" in t or "MONEY" in t:
        return "MONEY"
    if "DOUBLE" in t or "FLOAT" in t or "SINGLE" in t or "REAL" in t:
        return "FLOAT"
    if "DECIMAL" in t or "NUMERIC" in t:
        return "DECIMAL(18,4)"
    if "DATETIME" in t or "DATE" in t or "TIME" in t:
        return "DATETIME"
    if "YESNO" in t or "BIT" in t:
        return "BIT"
    if "MEMO" in t or "NOTE" in t:
        return "NVARCHAR(MAX)"
    if "TEXT" in t or "CHAR" in t or "VARCHAR" in t:
        if size and 0 < size < 255:
            return f"NVARCHAR({size})"
        return "NVARCHAR(MAX)"
    return "NVARCHAR(MAX)"


def quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


class Migrator:
    ACCESS_ENGINE_EXE = "accessdatabaseengine_X64.exe"
    # Fallback — English-версия Access Database Engine 2016
    ACCESS_ENGINE_PRODUCT_CODE = "{90160000-00D1-0409-1000-0000000FF1CE}"

    def __init__(
        self,
        source_dir: str,
        target_dir: str,
        sql_server: str,
        sql_database: str,
        sql_conn_str_builder: Callable[[str], str],
        log: Optional[Callable[[str], None]] = None,
        progress: Optional[Callable[[int], None]] = None,
        cleanup_log_path: Optional[str] = None,
    ):
        self.source_dir = os.path.normpath(source_dir)
        self.target_dir = os.path.normpath(target_dir)
        self.sql_server = sql_server
        self.sql_database = sql_database
        self.conn_builder = sql_conn_str_builder
        self.log = log or (lambda msg: None)
        self.progress = progress or (lambda p: None)
        self.cleanup_log_path = cleanup_log_path or os.path.join(
            self.target_dir, "migration.log"
        )
        self.access_driver_installed_by_migration = False
        self.access_driver_cleanup_started = False

    # =========================================================
    #  Access Database Engine
    # =========================================================

    def _is_access_driver_installed(self) -> bool:
        """Проверяет наличие 64-битного ACE-драйвера через pyodbc.drivers()."""
        try:
            for d in pyodbc.drivers():
                if "Microsoft Access Driver" in d and ".accdb" in d:
                    return True
            return False
        except Exception:
            return False

    def _ensure_access_driver(self) -> str:
        """Проверяет Access-драйвер. Ставит через UAC, если нет.
        Возвращает: 'already' | 'installed' | 'declined' | 'failed'."""
        if self._is_access_driver_installed():
            self.log("  Access-драйвер уже установлен")
            return "already"

        # В собранном приложении deps лежат рядом с exe или в _internal
        base_dir = os.path.dirname(os.path.abspath(__file__))
        exe_path = os.path.join(base_dir, "..", "deps", self.ACCESS_ENGINE_EXE)
        if not os.path.exists(exe_path):
            # Nuitka onefile распаковывает рядом с exe
            exe_path = os.path.join(base_dir, "deps", self.ACCESS_ENGINE_EXE)
        if not os.path.exists(exe_path):
            # Fallback — искать в корне приложения
            exe_path = os.path.join(
                os.path.dirname(sys.executable), "deps", self.ACCESS_ENGINE_EXE
            )
        if not os.path.exists(exe_path):
            self.log(f"  ⚠ Файл не найден: {exe_path}")
            return "failed"

        self.log("  Устанавливаю Access-драйвер (UAC)...")
        ok = run_as_admin(exe_path, "/quiet")
        if not ok:
            self.log("  ⚠ UAC отклонён")
            return "declined"

        # Ждём завершения установки
        time.sleep(15)

        if self._is_access_driver_installed():
            self.log("  ✅ Access-драйвер установлен")
            return "installed"
        else:
            self.log("  ⚠ Драйвер не появился после установки")
            return "failed"

    def _find_access_engine_product_code(self) -> str:
        """Ищет ProductCode Access Database Engine в реестре."""
        roots = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]
        keywords = [
            "Access database engine",
            "Access Database Engine",
            "Microsoft Access database engine",
        ]
        for root in roots:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    subkey_name = winreg.EnumKey(key, i)
                    try:
                        subkey = winreg.OpenKey(key, subkey_name)
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                        if "access" in name.lower():
                            self.log(f"      [DEBUG] Найден: {subkey_name} = '{name}'")
                        for kw in keywords:
                            if kw.lower() in name.lower():
                                self.log(f"      ✅ ProductCode: {subkey_name}")
                                return subkey_name
                    except Exception:
                        pass
            except Exception:
                pass
        return ""

    @staticmethod
    def _powershell_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _encode_powershell(script: str) -> list[str]:
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return ["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]

    def _schedule_after_exit(
        self,
        restart_command: Optional[list[str]],
        cleanup_marker: Optional[str] = None,
    ) -> bool:
        """Перезапускает приложение непривилегированным процессом после выхода."""
        log_path = self._powershell_literal(self.cleanup_log_path)
        lines = [
            "$ErrorActionPreference = 'Stop'",
            f"$logPath = {log_path}",
            "try {",
            "    Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Restart helper started\" -Encoding UTF8",
            "    try {",
            f"        $parent = [System.Diagnostics.Process]::GetProcessById({os.getpid()})",
            "        $parent.WaitForExit()",
            "        $parent.Dispose()",
            "    } catch [System.ArgumentException] { }",
            "    Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Previous application process exited\" -Encoding UTF8",
        ]
        if cleanup_marker:
            marker_path = self._powershell_literal(cleanup_marker)
            lines.extend([
                f"    $markerPath = {marker_path}",
                "    $deadline = (Get-Date).AddMinutes(30)",
                "    while (-not (Test-Path -LiteralPath $markerPath) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }",
                "    if (-not (Test-Path -LiteralPath $markerPath)) { throw 'Timed out waiting for Access Engine uninstall' }",
                "    Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Access Engine cleanup finished\" -Encoding UTF8",
            ])
        if restart_command:
            executable = self._powershell_literal(restart_command[0])
            arguments = self._powershell_literal(
                subprocess.list2cmdline(restart_command[1:])
            )
            working_dir = self._powershell_literal(os.getcwd())
            lines.extend([
                "    $startInfo = New-Object System.Diagnostics.ProcessStartInfo",
                f"    $startInfo.FileName = {executable}",
                f"    $startInfo.Arguments = {arguments}",
                f"    $startInfo.WorkingDirectory = {working_dir}",
                "    $startInfo.UseShellExecute = $false",
                "    $newProcess = [System.Diagnostics.Process]::Start($startInfo)",
                "    if ($null -eq $newProcess) { throw 'Windows did not return a process handle for the restarted application' }",
                "    Start-Sleep -Seconds 2",
                "    $newProcess.Refresh()",
                "    if ($newProcess.HasExited) {",
                "        Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Restarted application exited immediately with code $($newProcess.ExitCode)\" -Encoding UTF8",
                "    } else {",
                "        Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Application restarted (PID $($newProcess.Id))\" -Encoding UTF8",
                "    }",
                "    $newProcess.Dispose()",
            ])
        if cleanup_marker:
            lines.append(
                "    Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue"
            )
        lines.extend([
            "} catch {",
            "    Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Post-migration action failed: $($_.Exception.Message)\" -Encoding UTF8",
            "    exit 1",
            "}",
        ])
        try:
            params = " ".join(self._encode_powershell("\n".join(lines)))
            started = run_detached(
                "powershell.exe",
                params,
            )
            if started:
                self.log("Перезапуск программы запланирован после её закрытия.")
                return True
            self.log("  ⚠ Windows не запустил помощник перезапуска.")
            return False
        except Exception as error:
            self.log(f"  ⚠ Не удалось запланировать перезапуск: {error}")
            return False

    def _schedule_access_driver_uninstall(
        self, restart_command: Optional[list[str]] = None
    ) -> bool:
        """Удаляет Access Engine после закрытия приложения."""
        code = self._find_access_engine_product_code()
        if not code:
            code = self.ACCESS_ENGINE_PRODUCT_CODE
            self.log(f"  Использую ProductCode по умолчанию: {code}")
        else:
            self.log(f"  Использую ProductCode из реестра: {code}")

        if not re.fullmatch(
            r"\{[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\}",
            code,
        ):
            self.log(f"  ⚠ Некорректный ProductCode Access Engine: {code!r}")
            return False

        marker_path = f"{self.cleanup_log_path}.{uuid.uuid4().hex}.cleanup-done"
        marker_literal = self._powershell_literal(marker_path)
        log_literal = self._powershell_literal(self.cleanup_log_path)
        script = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            f"$logPath = {log_literal}",
            f"$markerPath = {marker_literal}",
            "try {",
            "    try {",
            f"        $parent = [System.Diagnostics.Process]::GetProcessById({os.getpid()})",
            "        $parent.WaitForExit()",
            "        $parent.Dispose()",
            "    } catch [System.ArgumentException] { }",
            "    try {",
            f"        $installer = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/x', '{code}', '/quiet', '/norestart') -Wait -PassThru",
            "        Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Access Engine uninstall exit code: $($installer.ExitCode)\" -Encoding UTF8",
            "        if ($installer.ExitCode -ne 0) { throw \"msiexec exited with code $($installer.ExitCode)\" }",
            "    } catch {",
            "        Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Access Engine uninstall failed: $($_.Exception.Message)\" -Encoding UTF8",
            "    }",
            "} catch {",
            "    Add-Content -LiteralPath $logPath -Value \"$(Get-Date -Format o) Access Engine cleanup failed: $($_.Exception.Message)\" -Encoding UTF8",
            "} finally {",
            "    Set-Content -LiteralPath $markerPath -Value 'done' -Encoding UTF8",
            "}",
        ])
        elevated = run_as_admin(
            "powershell.exe",
            " ".join(self._encode_powershell(script)),
        )
        if not elevated:
            self.log(
                "  ⚠ Не удалось запланировать удаление Access Engine "
                "(возможно, запрос UAC был отклонён)."
            )
            return False

        self.access_driver_cleanup_started = True
        return self._schedule_after_exit(restart_command, cleanup_marker=marker_path)

    def schedule_application_restart(self, restart_command: list[str]) -> bool:
        return self._schedule_after_exit(restart_command)

    # =========================================================
    #  Запуск миграции
    # =========================================================

    def run(self):
        # 1. Проверяем/ставим Access-драйвер
        driver_status = self._ensure_access_driver()
        self.access_driver_installed_by_migration = driver_status == "installed"
        if driver_status in ("declined", "failed"):
            raise RuntimeError(
                "Для миграции нужен Microsoft Access Database Engine.\n"
                "Установка не выполнена. Обратитесь к администратору."
            )

        self.log("=== Начало миграции ===")
        self.progress(0)

        if not os.path.isdir(self.source_dir):
            raise RuntimeError(f"Папка с .mdb не найдена: {self.source_dir}")

        mdb_paths = []
        for name in MDB_FILES:
            path = os.path.join(self.source_dir, name)
            if os.path.exists(path):
                mdb_paths.append(path)
                self.log(f"Найден: {path}")
            else:
                self.log(f"Не найден (пропущен): {path}")

        if not mdb_paths:
            raise RuntimeError("В папке нет ни одного .mdb-файла")

        self._create_and_move_database()
        self.progress(10)

        total_steps = 0
        for mdb in mdb_paths:
            total_steps += len(self._list_access_tables(mdb))

        self.log(f"Всего таблиц к миграции: {total_steps}")
        self.progress(15)

        step = 0
        for mdb in mdb_paths:
            self.log(f"\n--- Файл: {os.path.basename(mdb)} ---")
            for table in self._list_access_tables(mdb):
                step += 1
                percent = 15 + int(step * 85 / max(total_steps, 1))
                self.progress(percent)
                self.log(f"  >>> {table}")
                self._migrate_table(mdb, table)

        self.progress(100)
        self.log("=== Миграция завершена ===")

        # Финальный шаг
        self._create_datebirth_column()
        self._create_istochnik_naprav_column()
        self._fill_datebirth()
    # =========================================================
    #  Создание и перенос базы
    # =========================================================

    def _create_and_move_database(self):
        self.log(f"Создание базы '{self.sql_database}' (в папке SQL Server)...")

        conn_master = pyodbc.connect(self.conn_builder("master"), autocommit=True)
        cur = conn_master.cursor()

        cur.execute("SELECT name FROM sys.databases WHERE name = ?", self.sql_database)
        if cur.fetchone():
            self.log(f"  База '{self.sql_database}' существует — удаляю")
            dropped = False
            for attempt in range(3):
                try:
                    cur.execute(
                        f"ALTER DATABASE [{self.sql_database}] "
                        f"SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
                    )
                except Exception:
                    pass
                try:
                    cur.execute(
                        f"ALTER DATABASE [{self.sql_database}] "
                        f"SET OFFLINE WITH ROLLBACK IMMEDIATE"
                    )
                except Exception:
                    pass
                try:
                    cur.execute(f"DROP DATABASE [{self.sql_database}]")
                    dropped = True
                    break
                except Exception as e:
                    self.log(f"  ⚠ Попытка {attempt+1}: {e}")
                    time.sleep(2)

            if not dropped:
                raise RuntimeError(
                    f"Не удалось удалить базу '{self.sql_database}'.\n"
                    f"Пересоздайте LocalDB:\n"
                    f"  sqllocaldb stop MSSQLLocalDB\n"
                    f"  sqllocaldb delete MSSQLLocalDB\n"
                    f"  sqllocaldb create MSSQLLocalDB\n"
                    f"  sqllocaldb start MSSQLLocalDB"
                )
            time.sleep(1)

        cur.execute(f"CREATE DATABASE [{self.sql_database}]")
        self.log(f"  База '{self.sql_database}' создана")

        cur.execute(
            "SELECT physical_name FROM sys.master_files "
            "WHERE database_id = DB_ID(?) ORDER BY file_id",
            self.sql_database
        )
        rows = cur.fetchall()
        if len(rows) < 2:
            raise RuntimeError(f"Не удалось получить пути: {rows}")

        src_mdf = rows[0][0]
        src_ldf = rows[1][0]
        self.log(f"  MDF: {src_mdf}")
        self.log(f"  LDF: {src_ldf}")

        self.log("  Отсоединяю базу (sp_detach_db)...")
        cur.execute(
            f"ALTER DATABASE [{self.sql_database}] "
            f"SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
        )
        cur.execute(f"EXEC sp_detach_db '{self.sql_database}'")
        conn_master.close()
        time.sleep(2)

        if not os.path.exists(src_mdf):
            raise RuntimeError(f"Исходный файл не найден: {src_mdf}")
        if not os.path.exists(src_ldf):
            raise RuntimeError(f"Исходный файл не найден: {src_ldf}")

        os.makedirs(self.target_dir, exist_ok=True)
        dst_mdf = os.path.join(self.target_dir, f"{self.sql_database}.mdf")
        dst_ldf = os.path.join(self.target_dir, f"{self.sql_database}_log.ldf")

        for p in (dst_mdf, dst_ldf):
            if os.path.exists(p):
                os.remove(p)

        self.log(f"  Копирую в {self.target_dir}...")
        shutil.copy2(src_mdf, dst_mdf)
        shutil.copy2(src_ldf, dst_ldf)

        current_user = getpass.getuser()
        user_domain = os.environ.get("USERDOMAIN", "")
        account = f"{user_domain}\\{current_user}" if user_domain else current_user

        for path in (dst_mdf, dst_ldf):
            try:
                subprocess.run(
                    ["icacls", path, "/grant", f"{account}:F"],
                    check=True, capture_output=True, text=True
                )
                self.log(f"  Права выданы: {path}")
            except Exception as e:
                self.log(f"  ⚠ Не удалось выдать права на {path}: {e}")

        conn_master = pyodbc.connect(self.conn_builder("master"), autocommit=True)
        cur = conn_master.cursor()
        sql = (
            f"CREATE DATABASE [{self.sql_database}] ON "
            f"(FILENAME = '{dst_mdf}'), "
            f"(FILENAME = '{dst_ldf}') "
            f"FOR ATTACH"
        )
        self.log(f"  Привязываю базу к {dst_mdf}")
        cur.execute(sql)
        time.sleep(1)

        try:
            cur.execute(f"ALTER DATABASE [{self.sql_database}] SET READ_WRITE")
            self.log("  База переведена в режим READ_WRITE")
        except Exception as e:
            self.log(f"  ⚠ Не удалось снять read-only: {e}")

        conn_master.close()

        try:
            os.remove(src_mdf)
            os.remove(src_ldf)
            self.log("  Исходные файлы удалены")
        except Exception as e:
            self.log(f"  ⚠ Не удалось удалить исходные файлы: {e}")

        self.log(f"  ✅ База готова, файлы в {self.target_dir}")

    # =========================================================
    #  Access-подключение
    # =========================================================

    def _access_conn(self, mdb_path: str):
        conn_str = (
            r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
            rf"DBQ={mdb_path};"
        )
        return pyodbc.connect(conn_str)

    def _list_access_tables(self, mdb_path: str) -> list[str]:
        conn = None
        try:
            conn = self._access_conn(mdb_path)
            cur = conn.cursor()
            tables = []
            for row in cur.tables(tableType="TABLE"):
                name = row.table_name
                if name.startswith("MSys") or name.startswith("~"):
                    continue
                tables.append(name)
            return sorted(tables)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # =========================================================
    #  Миграция таблицы
    # =========================================================

    def _migrate_table(self, mdb_path: str, table: str):
        self.log(f"      [DEBUG] {table}: старт _migrate_table")

        acc_conn = None
        acc_cur = None
        sql_conn = None
        sql_cur = None

        try:
            acc_conn = self._access_conn(mdb_path)
            acc_cur = acc_conn.cursor()

            acc_cur.execute(f"SELECT * FROM [{table}] WHERE 1=0")
            columns = []
            for col in acc_cur.description:
                name = col[0]
                type_code = col[1]
                size = col[3] or 0
                nullable = bool(col[6]) if col[6] is not None else True
                columns.append((name, str(type_code), size, nullable))

            identity_col = IDENTITY_FIELDS.get(table)
            self.log(f"      [DEBUG] {table}: identity_col = {identity_col}")

            sql_conn = pyodbc.connect(self.conn_builder(self.sql_database), autocommit=False)
            sql_cur = sql_conn.cursor()

            sql_cur.execute("SELECT DB_NAME(), @@TRANCOUNT")
            row = sql_cur.fetchone()
            self.log(f"      [DEBUG] {table}: подключено к db={row[0]}, trancount={row[1]}")

            sql_cur.execute(
                f"IF OBJECT_ID(N'{table}', N'U') IS NOT NULL DROP TABLE {quote(table)}"
            )

            parts = []
            for name, type_name, size, nullable in columns:
                is_identity = (name == identity_col)
                sql_type = map_type(type_name, size, is_identity)
                if is_identity:
                    parts.append(f"{quote(name)} {sql_type} PRIMARY KEY")
                else:
                    null_clause = "NULL" if nullable else "NOT NULL"
                    parts.append(f"{quote(name)} {sql_type} {null_clause}")

            ddl = f"CREATE TABLE {quote(table)} (\n    " + ",\n    ".join(parts) + "\n)"
            sql_cur.execute(ddl)
            sql_conn.commit()

            acc_cur.execute(f"SELECT * FROM [{table}]")
            rows = acc_cur.fetchall()
            self.log(f"      [DEBUG] {table}: в Access {len(rows)} строк")

            col_names = [c[0] for c in columns]
            placeholders = ", ".join(["?"] * len(col_names))
            cols_sql = ", ".join(quote(c) for c in col_names)
            insert_sql = f"INSERT INTO {quote(table)} ({cols_sql}) VALUES ({placeholders})"

            if identity_col:
                sql_cur.execute(f"SET IDENTITY_INSERT {quote(table)} ON")
                sql_conn.commit()

            inserted = 0
            skipped = 0
            for i, row_data in enumerate(rows, 1):
                values = []
                for v in row_data:
                    if v is None:
                        values.append(None)
                    elif isinstance(v, datetime):
                        values.append(v)
                    elif isinstance(v, (int, float, str, bytes)):
                        values.append(v)
                    else:
                        values.append(str(v))
                try:
                    sql_cur.execute(insert_sql, values)
                    inserted += 1
                except pyodbc.IntegrityError as e:
                    skipped += 1
                    self.log(f"      ⚠ Строка {i} пропущена: {str(e)[:120]}")
                    try:
                        sql_conn.rollback()
                    except Exception:
                        pass
                    continue
                if i % 500 == 0:
                    sql_conn.commit()

            sql_cur.execute(f"SELECT COUNT(*) FROM {quote(table)}")
            cnt_before = sql_cur.fetchone()[0]
            self.log(f"      [DEBUG] {table}: в транзакции {cnt_before} строк (до commit)")

            sql_conn.commit()

            sql_cur.execute(f"SELECT COUNT(*) FROM {quote(table)}")
            cnt_after = sql_cur.fetchone()[0]
            self.log(f"      [DEBUG] {table}: после commit {cnt_after} строк")

            if identity_col:
                sql_cur.execute(f"SET IDENTITY_INSERT {quote(table)} OFF")
                sql_conn.commit()

                try:
                    sql_cur.execute(f"DBCC CHECKIDENT ('{table}', RESEED)")
                    sql_conn.commit()
                    self.log(f"      {table}: счётчик IDENTITY пересчитан")
                except Exception as e:
                    self.log(f"      {table}: ⚠ не удалось пересчитать IDENTITY: {e}")

            if skipped:
                self.log(f"      {table}: {inserted} строк (пропущено {skipped} дубликатов)")
            else:
                self.log(f"      {table}: {inserted} строк")

        finally:
            # КРИТИЧНО: закрываем Access-соединение ДО деинсталляции драйвера
            if acc_conn is not None:
                try:
                    acc_conn.close()
                except Exception:
                    pass
            if sql_conn is not None:
                try:
                    sql_conn.close()
                except Exception:
                    pass

    # =========================================================
    #  Дополнительные колонки
    # =========================================================

    def _create_datebirth_column(self):
        """Создаёт колонку Date_Rozhd в Patsient, если её нет."""
        self.log("\n--- Добавление колонки Patsient.Date_Rozhd ---")
        sql_conn = None
        try:
            sql_conn = pyodbc.connect(self.conn_builder(self.sql_database), autocommit=True)
            sql_cur = sql_conn.cursor()
            sql_cur.execute("""
                IF COL_LENGTH('Patsient', 'Date_Rozhd') IS NULL
                BEGIN
                    ALTER TABLE Patsient ADD Date_Rozhd DATETIME NULL
                END
            """)
            self.log("      Колонка Date_Rozhd готова")
        except Exception as e:
            self.log(f"      ⚠ Ошибка при создании колонки: {e}")
        finally:
            if sql_conn is not None:
                sql_conn.close()

    def _create_istochnik_naprav_column(self):
        """Создаёт колонку Istochnik_naprav в Protocol, если её нет."""
        self.log("\n--- Добавление колонки Protocol.Istochnik_naprav ---")
        sql_conn = None
        try:
            sql_conn = pyodbc.connect(self.conn_builder(self.sql_database), autocommit=True)
            sql_cur = sql_conn.cursor()
            sql_cur.execute("""
                IF COL_LENGTH('Protocol', 'Istochnik_naprav') IS NULL
                BEGIN
                    ALTER TABLE Protocol ADD Istochnik_naprav NVARCHAR(255) NULL
                END
            """)
            self.log("      Колонка Istochnik_naprav готова")
        except Exception as e:
            self.log(f"      ⚠ Ошибка при создании колонки: {e}")
        finally:
            if sql_conn is not None:
                sql_conn.close()

    def _fill_datebirth(self):
        """Заполняет Patsient.Date_Rozhd = Protocol.ProtocolDate - Protocol.Vozrast."""
        self.log("\n--- Заполнение Patsient.Date_Rozhd ---")
        sql_conn = None
        try:
            sql_conn = pyodbc.connect(self.conn_builder(self.sql_database), autocommit=True)
            sql_cur = sql_conn.cursor()

            sql_cur.execute("""
                UPDATE Patsient
                SET Date_Rozhd = (
                    SELECT TOP 1
                        CASE
                            WHEN p.Vozrast BETWEEN 0 AND 120
                                 AND p.ProtocolDate IS NOT NULL
                            THEN DATEADD(year, -p.Vozrast, p.ProtocolDate)
                            ELSE NULL
                        END
                    FROM Protocol p
                    WHERE p.PatsientID = Patsient.PatsientID
                      AND p.ProtocolDate IS NOT NULL
                    ORDER BY p.ProtocolDate ASC
                )
                WHERE Date_Rozhd IS NULL
            """)
            updated = sql_cur.rowcount
            self.log(f"      Обновлено: {updated} пациентов")
        except Exception as e:
            self.log(f"      ⚠ Ошибка при заполнении Date_Rozhd: {e}")
        finally:
            if sql_conn is not None:
                sql_conn.close()