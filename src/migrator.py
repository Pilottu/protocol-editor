"""
Миграция Access (.mdb) → SQL Server (LocalDB).

Порядок:
  1. CREATE DATABASE без указания пути (SQL Server сам выберет папку).
  2. DROP DATABASE (файлы остаются в папке SQL Server).
  3. Переносим .mdf/.ldf в target_dir.
  4. CREATE DATABASE ... FOR ATTACH (привязываем к новым файлам).
  5. Мигрируем таблицы.
"""

import os
import time
import shutil
import pyodbc
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
    def __init__(
        self,
        source_dir: str,
        target_dir: str,
        sql_server: str,
        sql_database: str,
        sql_conn_str_builder: Callable[[str], str],
        log: Optional[Callable[[str], None]] = None,
        progress: Optional[Callable[[int], None]] = None,
    ):
        self.source_dir = os.path.normpath(source_dir)
        self.target_dir = os.path.normpath(target_dir)
        self.sql_server = sql_server
        self.sql_database = sql_database
        self.conn_builder = sql_conn_str_builder
        self.log = log or (lambda msg: None)
        self.progress = progress or (lambda p: None)

    # -----------------------------------------------------

    def run(self):
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

    # -----------------------------------------------------
    #  Создание базы → перенос файлов → ATTACH
    # -----------------------------------------------------

    def _create_and_move_database(self):
        """
        1. CREATE DATABASE.
        2. Получаем реальные пути к .mdf/.ldf.
        3. sp_detach_db (отсоединяем базу, файлы остаются).
        4. Копируем файлы в target_dir.
        5. CREATE DATABASE ... FOR ATTACH.
        6. Удаляем исходные файлы.
        """
        self.log(f"Создание базы '{self.sql_database}' (в папке SQL Server)...")

        conn_master = pyodbc.connect(self.conn_builder("master"), autocommit=True)
        cur = conn_master.cursor()

        # На случай, если база уже существует — удаляем
        cur.execute("SELECT name FROM sys.databases WHERE name = ?", self.sql_database)
        if cur.fetchone():
            self.log(f"  База '{self.sql_database}' существует — удаляю")
            try:
                cur.execute(
                    f"ALTER DATABASE [{self.sql_database}] "
                    f"SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
                )
                cur.execute(f"DROP DATABASE [{self.sql_database}]")
                time.sleep(1)
            except Exception as e:
                self.log(f"  ⚠ Не удалось удалить базу: {e}")
                self.log(f"  ⚠ Возможно, это 'висячая' база. Пропускаю.")
                # Пробуем force drop через sys.databases
                try:
                    cur.execute(f"DROP DATABASE [{self.sql_database}]")
                    time.sleep(1)
                except Exception:
                    raise RuntimeError(
                        f"База '{self.sql_database}' существует, но не удаляется.\n"
                        f"Выполните вручную:\n"
                        f"  sqllocaldb stop MSSQLLocalDB\n"
                        f"  sqllocaldb delete MSSQLLocalDB\n"
                        f"  sqllocaldb create MSSQLLocalDB\n"
                        f"  sqllocaldb start MSSQLLocalDB"
                    )

        # --- 1. CREATE DATABASE ---
        cur.execute(f"CREATE DATABASE [{self.sql_database}]")
        self.log(f"  База '{self.sql_database}' создана")

        # --- 2. Получаем пути ---
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

        # --- 3. sp_detach_db (отсоединяем — файлы остаются) ---
        self.log("  Отсоединяю базу (sp_detach_db)...")
        cur.execute(
            f"ALTER DATABASE [{self.sql_database}] "
            f"SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
        )
        cur.execute(f"EXEC sp_detach_db '{self.sql_database}'")
        conn_master.close()
        time.sleep(2)

        self.log("  База отсоединена, файлы свободны")

        # --- 4. Проверяем, что исходные файлы существуют ---
        if not os.path.exists(src_mdf):
            raise RuntimeError(f"Исходный файл не найден: {src_mdf}")
        if not os.path.exists(src_ldf):
            raise RuntimeError(f"Исходный файл не найден: {src_ldf}")

        # --- 5. Копируем в target_dir ---
        os.makedirs(self.target_dir, exist_ok=True)

        dst_mdf = os.path.join(self.target_dir, f"{self.sql_database}.mdf")
        dst_ldf = os.path.join(self.target_dir, f"{self.sql_database}_log.ldf")

        for p in (dst_mdf, dst_ldf):
            if os.path.exists(p):
                os.remove(p)

        self.log(f"  Копирую в {self.target_dir}...")
        shutil.copy2(src_mdf, dst_mdf)
        shutil.copy2(src_ldf, dst_ldf)
                # Выдаём SQL Server полный доступ к файлам
        import subprocess
        import getpass
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

        self.log("  Файлы скопированы")

        # --- 6. CREATE DATABASE ... FOR ATTACH ---
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

        # Снимаем READ_ONLY (LocalDB иногда привязывает базы как read-only)
        try:
            cur.execute(f"ALTER DATABASE [{self.sql_database}] SET READ_WRITE")
            self.log("  База переведена в режим READ_WRITE")
        except Exception as e:
            self.log(f"  ⚠ Не удалось снять read-only: {e}")

        conn_master.close()

        # --- 7. Удаляем исходные файлы ---
        try:
            os.remove(src_mdf)
            os.remove(src_ldf)
            self.log("  Исходные файлы удалены")
        except Exception as e:
            self.log(f"  ⚠ Не удалось удалить исходные файлы: {e}")

        self.log(f"  ✅ База готова, файлы в {self.target_dir}")

    # -----------------------------------------------------

    def _access_conn(self, mdb_path: str):
        conn_str = (
            r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
            rf"DBQ={mdb_path};"
        )
        return pyodbc.connect(conn_str)

    def _list_access_tables(self, mdb_path: str) -> list[str]:
        conn = self._access_conn(mdb_path)
        cur = conn.cursor()
        tables = []
        for row in cur.tables(tableType="TABLE"):
            name = row.table_name
            if name.startswith("MSys") or name.startswith("~"):
                continue
            tables.append(name)
        conn.close()
        return sorted(tables)

    # -----------------------------------------------------

    def _migrate_table(self, mdb_path: str, table: str):
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

        sql_conn = pyodbc.connect(self.conn_builder(self.sql_database), autocommit=False)
        sql_cur = sql_conn.cursor()

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

        col_names = [c[0] for c in columns]
        placeholders = ", ".join(["?"] * len(col_names))
        cols_sql = ", ".join(quote(c) for c in col_names)
        insert_sql = f"INSERT INTO {quote(table)} ({cols_sql}) VALUES ({placeholders})"

        if identity_col:
            sql_cur.execute(f"SET IDENTITY_INSERT {quote(table)} ON")

        inserted = 0
        skipped = 0
        for i, row in enumerate(rows, 1):
            values = []
            for v in row:
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
                # Дубликат ключа или другая проблема целостности
                skipped += 1
                self.log(f"      ⚠ Строка {i} пропущена: {str(e)[:120]}")
                sql_conn.rollback()
                continue
            if i % 500 == 0:
                
                sql_conn.commit()

        if identity_col:
            sql_cur.execute(f"SET IDENTITY_INSERT {quote(table)} OFF")
            sql_conn.commit()

            # Пересчёт счётчика IDENTITY по текущему максимуму,
            # иначе SCOPE_IDENTITY() вернёт NULL при следующей вставке
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

        sql_conn.close()
        acc_conn.close()