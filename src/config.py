"""
Чтение и запись настроек подключения к SQL Server.
Хранит config.ini рядом с проектом (E:\Prog\Piton\config.ini).
"""

import os
import configparser

# Конфиг хранится в %LOCALAPPDATA%\ProtocolEditor\config.ini
# — так программа работает и из Program Files, и из dev-режима
CONFIG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ProtocolEditor"
)
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.ini")


DEFAULTS = {
    "server": r"(localdb)\MSSQLLocalDB",
    "database": "ProtocolDB",
    "auth": "windows",          # windows | sql
    "user": "",
    "password": "",
    "encrypt": "no",
    "trust_cert": "yes",
    "backup_dir": os.path.join(
        os.path.expanduser("~"), "Documents", "ProtocolEditor", "backups"
    ),
}


def load_config() -> dict:
    """Читает config.ini. Если файла нет — возвращает значения по умолчанию."""
    cfg = DEFAULTS.copy()
    if not os.path.exists(CONFIG_PATH):
        return cfg
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    if parser.has_section("database"):
        for key in DEFAULTS:
            if parser.has_option("database", key):
                cfg[key] = parser.get("database", key)
    return cfg


def save_config(cfg: dict) -> None:
    """Записывает config.ini."""
    parser = configparser.ConfigParser()
    parser.add_section("database")
    for key in DEFAULTS:
        parser.set("database", key, str(cfg.get(key, DEFAULTS[key])))
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        parser.write(f)


def build_connection_string(cfg: dict = None) -> str:
    """Собирает строку подключения для pyodbc."""
    if cfg is None:
        cfg = load_config()

    parts = [
        r"DRIVER={ODBC Driver 17 for SQL Server}",
        f"SERVER={cfg['server']}",
        f"DATABASE={cfg['database']}",
    ]

    if cfg.get("auth") == "sql":
        parts.append(f"UID={cfg['user']}")
        parts.append(f"PWD={cfg['password']}")
    else:
        parts.append("Trusted_Connection=yes")

    parts.append(f"Encrypt={cfg.get('encrypt', 'yes')}")
    parts.append(f"TrustServerCertificate={cfg.get('trust_cert', 'yes')}")

    return ";".join(parts) + ";"