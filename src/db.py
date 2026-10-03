import pyodbc
from contextlib import contextmanager
from typing import Optional

from config import load_config, build_connection_string


# --- Загружаем настройки при старте ---
_CFG = load_config()
CONN_STR = build_connection_string(_CFG)


def reload_config():
    """Перечитывает config.ini и обновляет CONN_STR."""
    global _CFG, CONN_STR
    _CFG = load_config()
    CONN_STR = build_connection_string(_CFG)


@contextmanager
def get_connection():
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
            pat.PatsientGroupID, pg.PatsientGroupName,
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
#  БЭКАП И ВОССТАНОВЛЕНИЕ
# =========================================================

def test_connection(cfg: dict) -> tuple[bool, str]:
    """Проверяет соединение с указанными параметрами. Возвращает (ok, сообщение)."""
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
    """Делает BACKUP DATABASE в указанный файл."""
    try:
        # Подключаемся к master, чтобы не блокировать саму БД
        cfg_master = cfg.copy()
        cfg_master["database"] = "master"
        conn = pyodbc.connect(build_connection_string(cfg_master), autocommit=True)
        cur = conn.cursor()
        db_name = cfg["database"]
        sql = f"BACKUP DATABASE [{db_name}] TO DISK = ? WITH INIT"
        cur.execute(sql, [backup_path])
        conn.close()
        return True, f"Бэкап сохранён: {backup_path}"
    except Exception as e:
        return False, str(e)