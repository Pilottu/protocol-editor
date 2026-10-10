import pyodbc
from contextlib import contextmanager
from typing import Optional

from config import load_config, build_connection_string


# Ленивая инициализация, чтобы падение конфига не убивало импорт
_CFG = None
CONN_STR = None

def _ensure_cfg():
    global _CFG, CONN_STR
    if _CFG is None:
        _CFG = load_config()
        CONN_STR = build_connection_string(_CFG)
    return _CFG, CONN_STR


def reload_config():
    global _CFG, CONN_STR
    _CFG = load_config()
    CONN_STR = build_connection_string(_CFG)


@contextmanager
def get_connection():
    _ensure_cfg()
    conn = pyodbc.connect(CONN_STR, autocommit=False)
    try:
        yield conn
    finally:
        conn.close()


# =========================================================
#  ПРОТОКОЛЫ
# =========================================================

def get_protocols(limit: int = 100, offset: int = 0,
                  year: Optional[int] = None,
                  search_fio: Optional[str] = None) -> list[dict]:
    limit = int(limit)
    sql = f"""
        SELECT TOP ({limit})
            p.ProtocolID, p.Nomer, p.ProtocolDate, p.[Year], p.Diagnos,
            p.ProtocolText, p.Vozrast,
            pat.FIO AS PatsientFIO, pat.Pol AS PatsientPol, pat.Karta AS PatsientKarta,
            it.Issledovanie AS IssledovanieName,
            o.OtdelenieName AS OtdelenieName,
            a.ApparatName AS ApparatName
        FROM Protocol p
            LEFT JOIN Patsient pat ON p.PatsientID = pat.PatsientID
            LEFT JOIN IssledovanieType it ON p.IssledovanieID = it.IssledovanieID
            LEFT JOIN Otdelenie o ON p.OtdelenieID = o.OtdelenieID
            LEFT JOIN Apparat a ON p.ApparatID = a.ApparatID
        WHERE 1=1
    """
    params = []
    if year:
        sql += " AND p.[Year] = ?"
        params.append(year)
    if search_fio:
        sql += " AND pat.FIO LIKE ?"
        params.append(f"%{search_fio}%")
    sql += " ORDER BY p.ProtocolDate DESC, p.ProtocolID DESC"

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_protocol(protocol_id: int) -> Optional[dict]:
    sql = """
        SELECT p.*, 
               pat.FIO AS PatsientFIO, pat.Pol AS PatsientPol, pat.Karta AS PatsientKarta,
               it.Issledovanie AS IssledovanieName,
               o.OtdelenieName AS OtdelenieName,
               a.ApparatName AS ApparatName,
               org.OrganName AS OrganizatsiaName
        FROM Protocol p
            LEFT JOIN Patsient pat ON p.PatsientID = pat.PatsientID
            LEFT JOIN IssledovanieType it ON p.IssledovanieID = it.IssledovanieID
            LEFT JOIN Otdelenie o ON p.OtdelenieID = o.OtdelenieID
            LEFT JOIN Apparat a ON p.ApparatID = a.ApparatID
            LEFT JOIN Organizatsia org ON p.OrganizatsiaID = org.OrganizatsiaID
        WHERE p.ProtocolID = ?
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [protocol_id])
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


# =========================================================
#  ДЕРЕВО
# =========================================================

def get_tree_data() -> list[dict]:
    sql = """
        SELECT 
            it.IssledovanieID, it.Issledovanie AS IssledovanieName,
            p.PatsientID, pat.FIO,
            p.ProtocolID, p.Nomer, p.[Year]
        FROM Protocol p
            INNER JOIN IssledovanieType it ON p.IssledovanieID = it.IssledovanieID
            INNER JOIN Patsient pat ON p.PatsientID = pat.PatsientID
        ORDER BY it.Issledovanie, pat.FIO, p.[Year], p.Nomer
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()

    tree = {}
    for row in rows:
        iss_id = row.IssledovanieID
        pat_id = row.PatsientID
        if iss_id not in tree:
            tree[iss_id] = {
                "issledovanie_id": iss_id,
                "issledovanie_name": row.IssledovanieName,
                "patients": {},
            }
        patients = tree[iss_id]["patients"]
        if pat_id not in patients:
            patients[pat_id] = {
                "patsient_id": pat_id,
                "fio": row.FIO,
                "protocols": [],
            }
        patients[pat_id]["protocols"].append({
            "protocol_id": row.ProtocolID,
            "nomer": row.Nomer,
            "year": row.Year,
        })

    result = []
    for iss in tree.values():
        iss["patients"] = list(iss["patients"].values())
        result.append(iss)
    return result


# =========================================================
#  ПОЛНЫЙ ПРОТОКОЛ + ЗАКЛЮЧЕНИЯ + ВРАЧИ
# =========================================================

def get_protocol_full(protocol_id: int) -> Optional[dict]:
    sql = """
        SELECT 
            p.*,
            pat.FIO AS PatsientFIO, pat.Pol AS PatsientPol, pat.Karta AS PatsientKarta,
            pat.PatsientGroupID, pat.Date_Rozhd,
            pg.PatsientGroupName,
            it.Issledovanie AS IssledovanieName,
            o.OtdelenieName AS OtdelenieName, o.OtdelenieHead,
            a.ApparatName AS ApparatName,
            org.OrganName AS OrganizatsiaName
        FROM Protocol p
            LEFT JOIN Patsient pat ON p.PatsientID = pat.PatsientID
            LEFT JOIN PatsientGroup pg ON pat.PatsientGroupID = pg.PatsientGroupID
            LEFT JOIN IssledovanieType it ON p.IssledovanieID = it.IssledovanieID
            LEFT JOIN Otdelenie o ON p.OtdelenieID = o.OtdelenieID
            LEFT JOIN Apparat a ON p.ApparatID = a.ApparatID
            LEFT JOIN Organizatsia org ON p.OrganizatsiaID = org.OrganizatsiaID
        WHERE p.ProtocolID = ?
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [protocol_id])
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def get_zakluchenia(protocol_id: int) -> list[dict]:
    sql = """
        SELECT ZakluchenieID, ProtocolID, ZakluchenieText, ZakluchenieOrder,
               IssledovanieID, OrganizatsiaID
        FROM Zakluchenie WHERE ProtocolID = ? ORDER BY ZakluchenieOrder
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [protocol_id])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_vrachi(protocol_id: int) -> list[dict]:
    sql = """
        SELECT v.VrachID, v.ProtocolID, v.VrachTypeID, v.VrachOrder,
               vt.FIO AS VrachFIO
        FROM Vrach v LEFT JOIN VrachType vt ON v.VrachTypeID = vt.VrachTypeID
        WHERE v.ProtocolID = ? ORDER BY v.VrachOrder
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [protocol_id])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_vrach_types() -> list[tuple]:
    sql = "SELECT VrachTypeID, FIO FROM VrachType ORDER BY FIO"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [(row[0], row[1]) for row in cur.fetchall()]


def get_anestezia_types() -> list[str]:
    sql = "SELECT Anestezia FROM AnesteziaType ORDER BY Anestezia"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [row[0] for row in cur.fetchall()]


def get_templates_for_issledovanie(issledovanie_id: int) -> list[dict]:
    sql = """
        SELECT ProtocolEditTemplateID, IssledovanieID, ProtocolEditTemplate,
               ProtocolEditTemplateComment, ProtocolEditTemplateType, OrganizatsiaID
        FROM ProtocolEditTemplate WHERE IssledovanieID = ? ORDER BY ProtocolEditTemplateID
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [issledovanie_id])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def search_patients(query: str, limit: int = 50) -> list[dict]:
    sql = f"""
        SELECT TOP ({int(limit)}) PatsientID, FIO, Pol, Karta, PatsientGroupID, OrganizatsiaID
        FROM Patsient WHERE FIO LIKE ? OR Karta LIKE ? ORDER BY FIO
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [f"%{query}%", f"%{query}%"])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_references() -> dict:
    refs = {}
    with get_connection() as conn:
        cur = conn.cursor()
        for table, id_col, name_col in [
            ("Apparat", "ApparatID", "ApparatName"),
            ("Otdelenie", "OtdelenieID", "OtdelenieName"),
            ("IssledovanieType", "IssledovanieID", "Issledovanie"),
            ("Organizatsia", "OrganizatsiaID", "OrganName"),
            ("PatsientGroup", "PatsientGroupID", "PatsientGroupName"),
        ]:
            cur.execute(f"SELECT {id_col}, {name_col} FROM {table} ORDER BY {name_col}")
            refs[table] = [(row[0], row[1]) for row in cur.fetchall()]
    return refs


# =========================================================
#  БЭКАП
# =========================================================

def test_connection(cfg: dict) -> tuple[bool, str]:
    try:
        conn_str = build_connection_string(cfg)
        conn = pyodbc.connect(conn_str, timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT @@VERSION")
        version = cur.fetchone()[0]
        conn.close()
        return True, version.split("\n")[0]
    except Exception as e:
        return False, str(e)


def make_backup(cfg: dict, backup_path: str) -> tuple[bool, str]:
    import os
    try:
        backup_dir = os.path.dirname(backup_path)
        if backup_dir and not os.path.isdir(backup_dir):
            os.makedirs(backup_dir, exist_ok=True)

        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except Exception:
                pass

        cfg_master = cfg.copy()
        cfg_master["database"] = "master"
        conn = pyodbc.connect(build_connection_string(cfg_master), autocommit=True)
        cur = conn.cursor()
        db_name = cfg["database"]

        safe_path = backup_path.replace("'", "''")
        sql = f"BACKUP DATABASE [{db_name}] TO DISK = N'{safe_path}' WITH INIT"
        cur.execute(sql)
        conn.close()

        if not os.path.exists(backup_path):
            return False, f"Бэкап не создан: {backup_path}"

        return True, f"Бэкап сохранён: {backup_path}"
    except Exception as e:
        return False, str(e)


def restore_backup(cfg: dict, backup_path: str) -> tuple[bool, str]:
    """Восстанавливает базу из .bak-файла с MOVE."""
    import os
    import time

    conn = None
    try:
        backup_path = os.path.abspath(backup_path)
        if not os.path.exists(backup_path):
            return False, f"Файл не найден: {backup_path}"

        cfg_master = cfg.copy()
        cfg_master["database"] = "master"
        conn = pyodbc.connect(build_connection_string(cfg_master), autocommit=True)
        cur = conn.cursor()
        db_name = cfg["database"]

        cur.execute("SELECT state_desc FROM sys.databases WHERE name = ?", db_name)
        row = cur.fetchone()
        if row:
            if row[0] == "ONLINE":
                cur.execute(
                    f"ALTER DATABASE [{db_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
                )
            elif row[0] != "RESTORING":
                return False, (
                    f"Невозможно восстановить базу '{db_name}': "
                    f"текущее состояние {row[0]}"
                )

        data_dir = os.path.dirname(backup_path)
        mdf_path = os.path.join(data_dir, f"{db_name}.mdf")
        ldf_path = os.path.join(data_dir, f"{db_name}_log.ldf")

        safe_backup = backup_path.replace("'", "''")
        safe_mdf = mdf_path.replace("'", "''")
        safe_ldf = ldf_path.replace("'", "''")
        sql = (
            f"RESTORE DATABASE [{db_name}] "
            f"FROM DISK = N'{safe_backup}' "
            f"WITH REPLACE, RECOVERY, "
            f"MOVE '{db_name}' TO N'{safe_mdf}', "
            f"MOVE '{db_name}_log' TO N'{safe_ldf}'"
        )
        cur.execute(sql)
        while cur.nextset():
            pass
        conn.close()
        conn = None

        state_desc = None
        for _ in range(50):
            check_conn = pyodbc.connect(
                build_connection_string(cfg_master), autocommit=True
            )
            try:
                check_cur = check_conn.cursor()
                check_cur.execute(
                    "SELECT state_desc FROM sys.databases WHERE name = ?", db_name
                )
                state = check_cur.fetchone()
                state_desc = state[0] if state else None
            finally:
                check_conn.close()
            if state_desc == "ONLINE":
                break
            time.sleep(0.2)

        if state_desc != "ONLINE":
            actual_state = state_desc or "не найдена"
            return False, (
                f"Восстановление завершилось, но база '{db_name}' "
                f"не перешла в состояние ONLINE (текущее состояние: {actual_state})"
            )
        conn = pyodbc.connect(build_connection_string(cfg_master), autocommit=True)
        cur = conn.cursor()
        cur.execute(f"ALTER DATABASE [{db_name}] SET AUTO_CLOSE OFF")
        cur.execute(f"ALTER DATABASE [{db_name}] SET MULTI_USER")
        return True, f"База '{db_name}' восстановлена из {backup_path}"
    except Exception as e:
        return False, str(e)
    finally:
        if conn is not None:
            conn.close()


# =========================================================
#  ПАЦИЕНТЫ
# =========================================================

def update_patsient(patsient_id: int, data: dict) -> bool:
    sql = """
        UPDATE Patsient
        SET FIO = ?, Pol = ?, Karta = ?, PatsientGroupID = ?, Date_Rozhd = ?
        WHERE PatsientID = ?
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [
            data.get("FIO", ""),
            data.get("Pol", ""),
            data.get("Karta", ""),
            data.get("PatsientGroupID"),
            data.get("Date_Rozhd"),
            patsient_id,
        ])
        conn.commit()
    return True


def insert_patsient(data: dict) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ISNULL(MAX(PatsientID), 0) + 1 FROM Patsient")
        new_id = int(cur.fetchone()[0])

        cur.execute("SET IDENTITY_INSERT Patsient ON")
        cur.execute(
            """
            INSERT INTO Patsient (PatsientID, FIO, Pol, Karta, PatsientGroupID, OrganizatsiaID, Date_Rozhd)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                new_id,
                data.get("FIO", ""),
                data.get("Pol", ""),
                data.get("Karta", ""),
                data.get("PatsientGroupID", 1),
                data.get("OrganizatsiaID", 1),
                data.get("Date_Rozhd"),
            ]
        )
        cur.execute("SET IDENTITY_INSERT Patsient OFF")
        conn.commit()
    return new_id


def get_next_protocol_nomer(issledovanie_id: int) -> int:
    sql = "SELECT ISNULL(MAX(CAST(Nomer AS INT)), 0) + 1 FROM Protocol WHERE IssledovanieID = ?"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [issledovanie_id])
        row = cur.fetchone()
        return int(row[0]) if row and row[0] else 1


def update_protocol(protocol_id: int, data: dict) -> bool:
    sql = """
        UPDATE Protocol SET
            PatsientID = ?, Vozrast = ?, OrganizatsiaID = ?, Nomer = ?,
            ProtocolDate = ?, Anestezia = ?, ProtocolText = ?, Diagnos = ?,
            OtdelenieID = ?, Adres = ?, Istor = ?, ApparatID = ?,
            Otdelenie = ?, Anamnez = ?, Biopsia = ?, IssledovanieID = ?,
            [Year] = ?, Tsitologia = ?, Gistologia = ?, Lecheb = ?,
            Sanats = ?, Intybastia = ?, PHMetr = ?, Smiv = ?, State = ?,
            Istochnik_naprav = ?
        WHERE ProtocolID = ?
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [
            data.get("PatsientID"),
            data.get("Vozrast"),
            data.get("OrganizatsiaID", 1),
            data.get("Nomer", ""),
            data.get("ProtocolDate"),
            data.get("Anestezia", ""),
            data.get("ProtocolText", ""),
            data.get("Diagnos", ""),
            data.get("OtdelenieID"),
            data.get("Adres", ""),
            data.get("Istor", ""),
            data.get("ApparatID"),
            data.get("Otdelenie", ""),
            data.get("Anamnez", ""),
            data.get("Biopsia", "нет"),
            data.get("IssledovanieID"),
            data.get("Year"),
            data.get("Tsitologia", "нет"),
            data.get("Gistologia", "нет"),
            data.get("Lecheb", "нет"),
            data.get("Sanats", "нет"),
            data.get("Intybastia", "нет"),
            data.get("PHMetr", "нет"),
            data.get("Smiv", "нет"),
            data.get("State", 1),
            data.get("Istochnik_naprav", ""),
            protocol_id,
        ])
        conn.commit()
    return True


def insert_protocol(data: dict) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ISNULL(MAX(ProtocolID), 0) + 1 FROM Protocol")
        new_id = int(cur.fetchone()[0])

        cur.execute("SET IDENTITY_INSERT Protocol ON")
        cur.execute(
            """
            INSERT INTO Protocol (
                ProtocolID, PatsientID, Vozrast, OrganizatsiaID, Nomer, ProtocolDate,
                Anestezia, ProtocolText, Diagnos, OtdelenieID, Adres, Istor,
                ApparatID, Otdelenie, Anamnez, Biopsia, IssledovanieID, [Year],
                Tsitologia, Gistologia, Lecheb, Sanats, Intybastia, PHMetr, Smiv, State,
                Istochnik_naprav
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                new_id,
                data.get("PatsientID"),
                data.get("Vozrast"),
                data.get("OrganizatsiaID", 1),
                data.get("Nomer", ""),
                data.get("ProtocolDate"),
                data.get("Anestezia", ""),
                data.get("ProtocolText", ""),
                data.get("Diagnos", ""),
                data.get("OtdelenieID"),
                data.get("Adres", ""),
                data.get("Istor", ""),
                data.get("ApparatID"),
                data.get("Otdelenie", ""),
                data.get("Anamnez", ""),
                data.get("Biopsia", "нет"),
                data.get("IssledovanieID"),
                data.get("Year"),
                data.get("Tsitologia", "нет"),
                data.get("Gistologia", "нет"),
                data.get("Lecheb", "нет"),
                data.get("Sanats", "нет"),
                data.get("Intybastia", "нет"),
                data.get("PHMetr", "нет"),
                data.get("Smiv", "нет"),
                data.get("State", 1),
                data.get("Istochnik_naprav", ""),
            ]
        )
        cur.execute("SET IDENTITY_INSERT Protocol OFF")
        conn.commit()
    return new_id


def delete_protocol(protocol_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM Napravlenie WHERE ProtocolID = ?", [protocol_id])
        cur.execute("DELETE FROM Zakluchenie WHERE ProtocolID = ?", [protocol_id])
        cur.execute("DELETE FROM Vrach WHERE ProtocolID = ?", [protocol_id])
        cur.execute("DELETE FROM Protocol WHERE ProtocolID = ?", [protocol_id])
        conn.commit()
    return True


# =========================================================
#  ЗАКЛЮЧЕНИЯ
# =========================================================

def get_distinct_zakluchenia() -> list[str]:
    sql = "SELECT DISTINCT ZakluchenieText FROM Zakluchenie WHERE ZakluchenieText <> '' ORDER BY ZakluchenieText"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [row[0] for row in cur.fetchall()]


def insert_zakluchenie(protocol_id: int, text: str, order: int,
                       issledovanie_id: int, organizatsia_id: int = 1) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ISNULL(MAX(ZakluchenieID), 0) + 1 FROM Zakluchenie")
        new_id = int(cur.fetchone()[0])

        cur.execute("SET IDENTITY_INSERT Zakluchenie ON")
        cur.execute(
            """
            INSERT INTO Zakluchenie (ZakluchenieID, ProtocolID, ZakluchenieText,
                                     ZakluchenieOrder, IssledovanieID, OrganizatsiaID)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [new_id, protocol_id, text, order, issledovanie_id, organizatsia_id]
        )
        cur.execute("SET IDENTITY_INSERT Zakluchenie OFF")
        conn.commit()
    return new_id


def update_zakluchenie(zakl_id: int, text: str, order: int) -> bool:
    sql = "UPDATE Zakluchenie SET ZakluchenieText = ?, ZakluchenieOrder = ? WHERE ZakluchenieID = ?"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [text, order, zakl_id])
        conn.commit()
    return True


def delete_zakluchenie(zakl_id: int) -> bool:
    sql = "DELETE FROM Zakluchenie WHERE ZakluchenieID = ?"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [zakl_id])
        conn.commit()
    return True


# =========================================================
#  ВРАЧИ
# =========================================================

def insert_vrach_type_if_missing(fio: str) -> int:
    fio = fio.strip()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT VrachTypeID FROM VrachType WHERE FIO = ?", [fio])
        row = cur.fetchone()
        if row:
            return int(row[0])

        cur.execute("SELECT ISNULL(MAX(VrachTypeID), 0) + 1 FROM VrachType")
        new_id = int(cur.fetchone()[0])

        cur.execute("SET IDENTITY_INSERT VrachType ON")
        cur.execute(
            "INSERT INTO VrachType (VrachTypeID, FIO, OrganizatsiaID) VALUES (?, ?, ?)",
            [new_id, fio, 1]
        )
        cur.execute("SET IDENTITY_INSERT VrachType OFF")
        conn.commit()
    return new_id


def insert_vrach(protocol_id: int, vrach_type_id: int, order: int,
                 issledovanie_id: int, organizatsia_id: int = 1) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ISNULL(MAX(VrachID), 0) + 1 FROM Vrach")
        new_id = int(cur.fetchone()[0])

        cur.execute("SET IDENTITY_INSERT Vrach ON")
        cur.execute(
            """
            INSERT INTO Vrach (VrachID, ProtocolID, VrachTypeID, OrganizatsiaID,
                               IssledovanieID, VrachOrder)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [new_id, protocol_id, vrach_type_id, organizatsia_id,
             issledovanie_id, order]
        )
        cur.execute("SET IDENTITY_INSERT Vrach OFF")
        conn.commit()
    return new_id


def delete_vrach(vrach_id: int) -> bool:
    sql = "DELETE FROM Vrach WHERE VrachID = ?"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [vrach_id])
        conn.commit()
    return True


def update_vrach_type(vrach_type_id: int, fio: str) -> bool:
    sql = "UPDATE VrachType SET FIO = ? WHERE VrachTypeID = ?"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [fio, vrach_type_id])
        conn.commit()
    return True


def delete_vrach_type(vrach_type_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Vrach WHERE VrachTypeID = ?", [vrach_type_id])
        if cur.fetchone()[0] > 0:
            return False
        cur.execute("DELETE FROM VrachType WHERE VrachTypeID = ?", [vrach_type_id])
        conn.commit()
    return True


# =========================================================
#  НАСТРОЙКИ (таблица Settings)
# =========================================================

def ensure_settings_table() -> bool:
    """Создаёт таблицу Settings, если её нет."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                IF OBJECT_ID('Settings', 'U') IS NULL
                BEGIN
                    CREATE TABLE Settings (
                        SettingKey NVARCHAR(100) NOT NULL PRIMARY KEY,
                        SettingValue NVARCHAR(MAX) NULL
                    );
                END
            """)
            conn.commit()
        return True
    except Exception as e:
        print(f"[ERROR] ensure_settings_table: {e}")
        return False


DEFAULT_SETTINGS = {
    "format_protocol": "html",
    "format_report": "html",
    "kartoteka_orientation": "portrait",
    "page_margin_left": "19.05",
    "page_margin_right": "19.05",
    "page_margin_top": "19.05",
    "page_margin_bottom": "19.05",
    "kartoteka_header": "&w&bPage &p of &P",
    "kartoteka_footer": "&u&b&d",
    "document_title": "",
    "filter_letter": "",
    "filter_only_current": "0",
    "filter_only_unprinted": "0",
    "otdelenie_name": "",
    "otdelenie_head": "",
    "protocol_style": "two_pages",
    "report_form": "nagruzka",       # nagruzka | gistologia
    "report_period": "month",        # month | year
    "report_year": "2025",
    "report_month": "10",
    "organizatsia_name": "",
    "otdelenie_name": "",
    "otdelenie_head": "",
}


def get_setting(key: str, default: str = "") -> str:
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT SettingValue FROM Settings WHERE SettingKey = ?", [key])
            row = cur.fetchone()
            return row[0] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str) -> bool:
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM Settings WHERE SettingKey = ?", [key])
            if cur.fetchone():
                cur.execute(
                    "UPDATE Settings SET SettingValue = ? WHERE SettingKey = ?",
                    [str(value), key]
                )
            else:
                cur.execute(
                    "INSERT INTO Settings (SettingKey, SettingValue) VALUES (?, ?)",
                    [key, str(value)]
                )
            conn.commit()
        return True
    except Exception as e:
        print(f"[ERROR] set_setting({key}): {e}")
        return False


def get_all_settings() -> dict:
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT SettingKey, SettingValue FROM Settings")
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception:
        return {}


def save_settings(data: dict) -> bool:
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            for key, value in data.items():
                cur.execute("SELECT 1 FROM Settings WHERE SettingKey = ?", [key])
                if cur.fetchone():
                    cur.execute(
                        "UPDATE Settings SET SettingValue = ? WHERE SettingKey = ?",
                        [str(value), key]
                    )
                else:
                    cur.execute(
                        "INSERT INTO Settings (SettingKey, SettingValue) VALUES (?, ?)",
                        [key, str(value)]
                    )
            conn.commit()
        return True
    except Exception as e:
        print(f"[ERROR] save_settings: {e}")
        return False


def get_settings_with_defaults() -> dict:
    saved = get_all_settings()
    result = DEFAULT_SETTINGS.copy()
    result.update(saved)
    return result