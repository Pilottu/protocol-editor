"""
Миграция Access (.mdb) -> SQL Server (TEW_SQLEXPRESS).

Скрипт сам:
  1. Проверяет, существует ли база данных в SQL Server. Если нет — создаёт.
  2. Читает все таблицы из .mdb.
  3. Создаёт их в SQL Server и переносит данные.

Использование:
    python migrate_to_sql.py <путь_к_mdb> <имя_базы>

Примеры:
    python migrate_to_sql.py E:\ProtEdit\Protocol2.mdb ProtocolDB
    python migrate_to_sql.py E:\ProtEdit\ProtocolEditTemplates.mdb TemplatesDB
    python migrate_to_sql.py E:\ProtEdit\ProtocolImages.mdb ImagesDB
"""

import pyodbc
import sys
import os
from datetime import datetime


# --- Настройки SQL Server ---
import os
SQL_SERVER = os.environ.get("PROTOCOL_SQL_SERVER", r"(localdb)\MSSQLLocalDB")

# Строка подключения к master (для создания базы)
SQL_MASTER_CONN = (
    r"DRIVER={ODBC Driver 17 for SQL Server};"
    rf"SERVER={SQL_SERVER};"
    r"DATABASE=master;"
    r"Trusted_Connection=yes;"
    r"Encrypt=yes;"
    r"TrustServerCertificate=yes;"
)


def sql_conn_for_db(db_name: str) -> str:
    return (
        r"DRIVER={ODBC Driver 17 for SQL Server};"
        rf"SERVER={SQL_SERVER};"
        rf"DATABASE={db_name};"
        r"Trusted_Connection=yes;"
        r"Encrypt=yes;"
        r"TrustServerCertificate=yes;"
    )


def access_conn_for_mdb(mdb_path: str) -> str:
    return (
        r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
        rf"DBQ={mdb_path};"
    )


# --- Работа с SQL Server ---

def ensure_database(db_name: str):
    """Создаёт базу данных, если её нет."""
    print(f"[SQL] Проверяю базу '{db_name}'...")
    conn = pyodbc.connect(SQL_MASTER_CONN, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sys.databases WHERE name = ?", db_name)
    if cur.fetchone():
        print(f"[SQL] База '{db_name}' уже существует.")
    else:
        print(f"[SQL] Создаю базу '{db_name}'...")
        cur.execute(f"CREATE DATABASE [{db_name}]")
        print(f"[SQL] База '{db_name}' создана.")
    conn.close()


def list_access_tables(acc_cursor) -> list[str]:
    """Возвращает список пользовательских таблиц из .mdb."""
    tables = []
    for row in acc_cursor.tables(tableType="TABLE"):
        name = row.table_name
        # Пропускаем системные
        if name.startswith("MSys") or name.startswith("~"):
            continue
        tables.append(name)
    return sorted(tables)


# --- Маппинг типов Access -> T-SQL ---

def map_type(access_type: str, size: int) -> str:
    t = access_type.upper()
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
        # Всё, что >= 255 или без размера — в MAX, чтобы не было обрезки
        if size and 0 < size < 255:
            return f"NVARCHAR({size})"
        return "NVARCHAR(MAX)"
    return "NVARCHAR(MAX)"

def quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_NAME = ? AND TABLE_TYPE = 'BASE TABLE'",
        table,
    )
    return cursor.fetchone()[0] > 0


def drop_table(cursor, table: str):
    cursor.execute(f"IF OBJECT_ID(N'{table}', N'U') IS NOT NULL DROP TABLE {quote(table)}")


def create_table(sql_cursor, table: str, columns: list[tuple]):
    parts = []
    for name, type_name, size, nullable in columns:
        sql_type = map_type(type_name, size)
        null_clause = "NULL" if nullable else "NOT NULL"
        parts.append(f"{quote(name)} {sql_type} {null_clause}")
    ddl = f"CREATE TABLE {quote(table)} (\n    " + ",\n    ".join(parts) + "\n)"
    sql_cursor.execute(ddl)


def get_access_columns(acc_cursor, table: str) -> list[tuple]:
    """Возвращает [(name, type_name, size, nullable), ...]."""
    acc_cursor.execute(f"SELECT * FROM [{table}] WHERE 1=0")
    columns = []
    for col in acc_cursor.description:
        name = col[0]
        type_code = col[1]
        size = col[3] or 0
        nullable = bool(col[6]) if col[6] is not None else True
        columns.append((name, str(type_code), size, nullable))
    return columns


def migrate_table(acc_cursor, sql_cursor, table: str) -> int:
    print(f"\n  >>> {table}")

    columns = get_access_columns(acc_cursor, table)
    if not columns:
        print("      Пропуск: нет колонок")
        return 0

    if table_exists(sql_cursor, table):
        print("      Удаляю старую таблицу")
        drop_table(sql_cursor, table)

    create_table(sql_cursor, table, columns)
    sql_cursor.commit()

    acc_cursor.execute(f"SELECT * FROM [{table}]")
    rows = acc_cursor.fetchall()
    print(f"      Строк: {len(rows)}")
    if not rows:
        return 0

    col_names = [c[0] for c in columns]
    placeholders = ", ".join(["?"] * len(col_names))
    cols_sql = ", ".join(quote(c) for c in col_names)
    insert_sql = f"INSERT INTO {quote(table)} ({cols_sql}) VALUES ({placeholders})"

    inserted = 0
    for i, row in enumerate(rows, 1):
        try:
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
            sql_cursor.execute(insert_sql, values)
            inserted += 1
            if i % 500 == 0:
                sql_cursor.commit()
                print(f"      ...{i} из {len(rows)}")
        except Exception as e:
            print(f"      ОШИБКА в строке {i}: {e}")
            print(f"      Значения: {row}")
            raise

    sql_cursor.commit()
    print(f"      ГОТОВО: {inserted}")
    return inserted


def migrate(mdb_path: str, db_name: str):
    print("=" * 60)
    print(f"Миграция: {mdb_path}")
    print(f"     в базу: {db_name}")
    print("=" * 60)

    if not os.path.exists(mdb_path):
        print(f"ОШИБКА: файл не найден: {mdb_path}")
        sys.exit(1)

    # 1. Создаём базу, если нужно
    ensure_database(db_name)

    # 2. Подключаемся к Access и SQL Server
    acc_conn = pyodbc.connect(access_conn_for_mdb(mdb_path))
    acc_cursor = acc_conn.cursor()

    sql_conn = pyodbc.connect(sql_conn_for_db(db_name))
    sql_cursor = sql_conn.cursor()

    # 3. Список таблиц
    tables = list_access_tables(acc_cursor)
    print(f"\nНайдено таблиц: {len(tables)}")
    for t in tables:
        print(f"  - {t}")

    # 4. Миграция
    total = 0
    for table in tables:
        try:
            total += migrate_table(acc_cursor, sql_cursor, table)
        except Exception as e:
            print(f"\n!!! ОШИБКА в таблице {table}: {e}")
            acc_conn.close()
            sql_conn.close()
            sys.exit(1)

    acc_conn.close()
    sql_conn.close()

    print(f"\n=== ГОТОВО ===")
    print(f"Всего перенесено строк: {total}")


def main():
    if len(sys.argv) < 3:
        print("Использование:")
        print("  python migrate_to_sql.py <путь_к_mdb> <имя_базы_в_SQL>")
        print()
        print("Примеры:")
        print("  python migrate_to_sql.py E:\\ProtEdit\\Protocol2.mdb ProtocolDB")
        print("  python migrate_to_sql.py E:\\ProtEdit\\ProtocolEditTemplates.mdb TemplatesDB")
        sys.exit(1)

    mdb_path = sys.argv[1]
    db_name = sys.argv[2]
    migrate(mdb_path, db_name)


if __name__ == "__main__":
    main()