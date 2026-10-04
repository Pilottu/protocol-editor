"""
Миграция Access (.mdb) → SQL Server (LocalDB или обычный).
"""

import os
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
        data_dir: str,
        sql_server: str,
        sql_database: str,
        sql_conn_str_builder: Callable[[str], str],
        log: Optional[Callable[[str], None]] = None,
        progress: Optional[Callable[[int], None]] = None,
    ):
        self.data_dir = data_dir
        self.sql_server = sql_server
        self.sql_database = sql_database
        self.conn_builder = sql_conn_str_builder
        self.log = log or (lambda msg: None)
        self.progress = progress or (lambda p: None)

    # -----------------------------------------------------

    def run(self):
        self.data_dir = os.path.normpath(self.data_dir)
        self.log("=== Начало миграции ===")
        self.progress(0)

        if not os.path.isdir(self.data_dir):
            raise RuntimeError(f"Папка не найдена: {self.data_dir}")

        mdb_paths = []
        for name in MDB_FILES:
            path = os.path.join(self.data_dir, name)
            if os.path.exists(path):
                mdb_paths.append(path)
                self.log(f"Найден: {path}")
            else:
                self.log(f"Не найден (пропущен): {path}")

        if not mdb_paths:
            raise RuntimeError("В папке нет ни одного .mdb-файла")

        self._create_database()
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

    def _create_database(self):
        """Создаёт (или пересоздаёт) базу данных в LocalDB/SQL Server."""
        self.log(f"Создание базы '{self.sql_database}'...")
        cfg_master = self.conn_builder("master")
        conn = pyodbc.connect(cfg_master, autocommit=True)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sys.databases WHERE name = ?", self.sql_database)
        if cur.fetchone():
            self.log(f"  База '{self.sql_database}' существует — удаляю")
            cur.execute(
                f"ALTER DATABASE [{self.sql_database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
            )
            cur.execute(f"DROP DATABASE [{self.sql_database}]")

        mdf_path = os.path.normpath(os.path.join(self.data_dir, f"{self.sql_database}.mdf"))
        ldf_path = os.path.normpath(os.path.join(self.data_dir, f"{self.sql_database}_log.ldf"))

        for p in (mdf_path, ldf_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        # ← ИСПРАВЛЕНО: явное NAME для каждого файла
        sql = (
            f"CREATE DATABASE [{self.sql_database}] ON "
            f"(NAME = N'{self.sql_database}', FILENAME = '{mdf_path}'), "
            f"(NAME = N'{self.sql_database}_log', FILENAME = '{ldf_path}')"
        )
        self.log(f"  Создаю: {mdf_path}")
        cur.execute(sql)
        conn.close()

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
            sql_cur.execute(insert_sql, values)
            inserted += 1
            if i % 500 == 0:
                sql_conn.commit()

        sql_conn.commit()

        if identity_col:
            sql_cur.execute(f"SET IDENTITY_INSERT {quote(table)} OFF")
            sql_conn.commit()

        self.log(f"      {table}: {inserted} строк")

        sql_conn.close()
        acc_conn.close()