import sys
sys.path.insert(0, r"E:\Prog\Piton\src")
from migrate_to_sql import (
    access_conn_for_mdb, sql_conn_for_db,
    get_access_columns, table_exists, drop_table, create_table,
    migrate_table
)
import pyodbc


def add_table(mdb_path: str, db_name: str, table_name: str):
    acc_conn = pyodbc.connect(access_conn_for_mdb(mdb_path))
    acc_cursor = acc_conn.cursor()

    sql_conn = pyodbc.connect(sql_conn_for_db(db_name))
    sql_cursor = sql_conn.cursor()

    print(f"Добавляю таблицу {table_name} из {mdb_path}")
    n = migrate_table(acc_cursor, sql_cursor, table_name)
    print(f"Готово: {n} строк")

    acc_conn.close()
    sql_conn.close()


if __name__ == "__main__":
    # Добавляем шаблоны
    add_table(
        r"E:\ProtEdit\ProtocolEditTemplates.mdb",
        "ProtocolDB",
        "ProtocolEditTemplate",
    )
    # Добавляем изображения
    add_table(
        r"E:\ProtEdit\ProtocolImages.mdb",
        "ProtocolDB",
        "ProtocolImage",
    )