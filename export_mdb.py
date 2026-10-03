import pyodbc
import csv
import os
import sys

# Путь к базе данных Access
DB_PATH = r"E:\ProtEdit\ProtocolImages.mdb"

# Папка для выгрузки CSV
OUT_DIR = r"E:\Prog\Piton\data\export"

def main():
    if not os.path.exists(DB_PATH):
        print(f"ОШИБКА: файл базы не найден: {DB_PATH}")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)

    # Подключение через 64-разрядный драйвер Access
    conn_str = (
        r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
        rf"DBQ={DB_PATH};"
    )

    try:
        conn = pyodbc.connect(conn_str)
    except pyodbc.Error as e:
        print("Не удалось подключиться к базе:")
        print(e)
        print("\nДоступные драйверы ODBC:")
        for d in pyodbc.drivers():
            print(" -", d)
        sys.exit(1)

    cursor = conn.cursor()

    # Список таблиц
    tables = sorted([row.table_name for row in cursor.tables(tableType='TABLE')])
    print(f"Найдено таблиц: {len(tables)}")
    for t in tables:
        print(" -", t)

    # Экспорт каждой таблицы в CSV
    for table in tables:
        try:
            cursor.execute(f"SELECT * FROM [{table}]")
            cols = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

            csv_path = os.path.join(OUT_DIR, f"{table}.csv")
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(cols)
                for row in rows:
                    writer.writerow(row)

            print(f"  OK: {table} -> {len(rows)} строк, {len(cols)} колонок")
        except Exception as e:
            print(f"  ОШИБКА в таблице {table}: {e}")

    conn.close()
    print(f"\nГотово. CSV лежат в: {OUT_DIR}")


if __name__ == "__main__":
    main()