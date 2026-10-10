"""
Генерация HTML-протокола эндоскопического исследования.
Собирает данные из базы по protocol_id и возвращает HTML-строку.
"""

import os
import sys
import tempfile
import webbrowser
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_connection


MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]


def format_date_ru(d) -> str:
    """24 июля 2024 г."""
    if not d:
        return ""
    return f"«{d.day:02d}» {MONTHS_RU[d.month]} {d.year} г."


def _value(v) -> str:
    """Пустое значение → пустая строка."""
    if v is None:
        return ""
    return str(v).strip()


def _format_fio(fio: str) -> str:
    """Иванов Иван Иванович → Иванов И.И."""
    parts = fio.strip().split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    return fio


def load_protocol_data(protocol_id: int) -> dict:
    """Читает все данные протокола из базы. Организация/отделение — из Settings."""
    from db import get_settings_with_defaults
    settings = get_settings_with_defaults()

    with get_connection() as conn:
        cur = conn.cursor()

        # Основные данные
        cur.execute("""
            SELECT
                p.ProtocolID, p.Nomer, p.ProtocolDate, p.Vozrast,
                p.Adres, p.Istor, p.Anamnez, p.ProtocolText,
                p.Biopsia, p.Tsitologia, p.Gistologia, p.Lecheb,
                p.Otdelenie AS OtdeleniePodr,
                pat.FIO AS PatsientFIO, pat.Pol AS PatsientPol, pat.Karta AS PatsientKarta,
                it.Issledovanie AS IssledovanieName,
                a.ApparatName AS ApparatName,
                p.Anestezia
            FROM Protocol p
                LEFT JOIN Patsient pat ON p.PatsientID = pat.PatsientID
                LEFT JOIN IssledovanieType it ON p.IssledovanieID = it.IssledovanieID
                LEFT JOIN Apparat a ON p.ApparatID = a.ApparatID
            WHERE p.ProtocolID = ?
        """, [protocol_id])
        row = cur.fetchone()
        if not row:
            return {}
        cols = [d[0] for d in cur.description]
        data = dict(zip(cols, row))

        # --- Организация/Отделение/Заведующий — только из Settings ---
        data["OrganizatsiaName"] = settings.get("organizatsia_name", "")
        data["OtdelenieName"] = settings.get("otdelenie_name", "")
        data["Zaveduyushiy"] = settings.get("otdelenie_head", "")

        # Заключения
        cur.execute("""
            SELECT ZakluchenieText FROM Zakluchenie
            WHERE ProtocolID = ? ORDER BY ZakluchenieOrder
        """, [protocol_id])
        zakl = [r[0] for r in cur.fetchall() if r[0]]
        data["Zakluchenie"] = ". ".join(z.strip().rstrip(".") for z in zakl) + ("." if zakl else "")

        # Врачи
        cur.execute("""
            SELECT vt.FIO FROM Vrach v
                LEFT JOIN VrachType vt ON v.VrachTypeID = vt.VrachTypeID
            WHERE v.ProtocolID = ? ORDER BY v.VrachOrder
        """, [protocol_id])
        vrachi = [r[0] for r in cur.fetchall() if r[0]]
        data["Vrachi"] = vrachi

        return data


def build_html(data: dict) -> str:
    """Собирает HTML по данным протокола."""
    if not data:
        return "<html><body><h1>Протокол не найден</h1></body></html>"

    # ФИО врачей — через запятую, но с сокращением «Мельник Д.М.»
    vrachi_html = "<br>".join(
        _format_fio(v) for v in data.get("Vrachi", [])
    ) or ""

    # Флаги
    def flag(v):
        return _value(v) if v else ""

    date_str = format_date_ru(data.get("ProtocolDate"))

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Протокол № {_value(data.get('Nomer'))}</title>
<style>
    body {{
        font-family: 'Times New Roman', serif;
        font-size: 12pt;
        margin: 40px 60px;
        line-height: 1.35;
    }}
    .header {{
        text-align: center;
        margin-bottom: 24px;
    }}
    .header .org {{ font-weight: bold; }}
    .header .otd {{ font-weight: bold; margin-top: 12px; }}
    .header .nomer {{ margin-top: 12px; }}
    .header .date {{ }}
    .header .issledovanie {{
        font-weight: bold;
        margin-top: 18px;
        text-align: center;
    }}
    .row {{ margin: 4px 0; }}
    .row .label {{ display: inline-block; min-width: 200px; vertical-align: top; }}
    .row .value {{ display: inline-block; }}
    .flags-table {{
        width: 100%;
        margin-top: 8px;
        border-collapse: collapse;
    }}
    .flags-table td {{ padding: 2px 4px; vertical-align: top; }}
    .protocol-text {{
        margin-top: 16px;
        text-align: justify;
        white-space: pre-wrap;
    }}
    .zakluchenie {{
        margin-top: 14px;
    }}
    .signature {{
        margin-top: 50px;
        text-align: right;
    }}
    .signature .vrach {{ display: inline-block; }}
    u {{ text-decoration: underline; }}
</style>
</head>
<body>

<div class="header">
    <div class="org">{_value(data.get('OrganizatsiaName'))}</div>
    <div class="otd">{_value(data.get('OtdelenieName'))}</div>
    <div class="nomer">Протокол № {_value(data.get('Nomer'))}</div>
    <div class="date">{date_str}</div>
    <div class="issledovanie">{_value(data.get('IssledovanieName'))}</div>
</div>

<div class="row">
    <span class="label">Ф.И.О. больного <u>{_value(data.get('PatsientFIO'))}</u></span>
    <span>Пол {_value(data.get('PatsientPol'))}</span>
</div>
<div class="row">
    <span class="label">Возраст {_value(data.get('Vozrast'))} лет (год)</span>
</div>
<div class="row">
    <span class="label">Адрес: <u>{_value(data.get('Adres'))}</u></span>
</div>
<div class="row">
    <span class="label">Амб. карта № <u>{_value(data.get('PatsientKarta'))}</u></span>
    <span>История болезни № <u>{_value(data.get('Istor'))}</u></span>
</div>
<div class="row">
    <span class="label">Отделение <u>{_value(data.get('OtdeleniePodr'))}</u></span>
</div>
<div class="row">
    <span class="label">Анамнез: <u>{_value(data.get('Anamnez'))}</u></span>
</div>
<div class="row">
    <span class="label">Модель аппарата <u>{_value(data.get('ApparatName'))}</u></span>
</div>
<div class="row">
    <span class="label">Анестезия <u>{_value(data.get('Anestezia'))}</u></span>
</div>

<table class="flags-table">
    <tr>
        <td>Биопсия <u>{flag(data.get('Biopsia'))}</u></td>
        <td>Цитология <u>{flag(data.get('Tsitologia'))}</u></td>
        <td>Гистология <u>{flag(data.get('Gistologia'))}</u></td>
    </tr>
    <tr>
        <td colspan="3">Лечебно-диагностическая <u>{flag(data.get('Lecheb'))}</u></td>
    </tr>
</table>

<div class="protocol-text">{_value(data.get('ProtocolText'))}</div>

<div class="zakluchenie">Заключение: <u>{_value(data.get('Zakluchenie'))}</u></div>

<div class="signature">
    <div class="vrach">Врач {vrachi_html}</div>
    <div class="zaved">Зав. отделением {_value(data.get('Zaveduyushiy'))}</div>
</div>

</body>
</html>"""
    return html


def open_in_browser(html: str, filename: str = None):
    """Сохраняет HTML во временный файл и открывает в браузере."""
    if not filename:
        filename = f"protocol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path = os.path.join(tempfile.gettempdir(), filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    webbrowser.open("file:///" + path.replace("\\", "/"))
    return path


def open_in_word(html: str, filename: str = None):
    """Сохраняет HTML с расширением .doc и открывает в Word."""
    if not filename:
        filename = f"protocol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.doc"
    path = os.path.join(tempfile.gettempdir(), filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    os.startfile(path)
    return path


def print_protocol(protocol_id: int):
    """Основная функция: собрать данные и открыть в нужном формате."""
    # Читаем настройку формата из БД
    try:
        from db import get_setting
        fmt = get_setting("format_protocol", "html")
    except Exception:
        fmt = "html"

    data = load_protocol_data(protocol_id)
    html = build_html(data)

    if fmt == "rtf":
        return open_in_word(html, f"protocol_{protocol_id}.doc")
    else:
        return open_in_browser(html, f"protocol_{protocol_id}.html")