"""
Отчёты для программы «Редактор протоколов».

Два отчёта:
  1. Нагрузка  — количество протоколов по исследованиям и группам пациентов.
  2. Гистология — количество биопсий/гистологий/цитологий + топ-20 заключений.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_connection


MONTHS_RU = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"
]


def _period_dates(year: int, month: int = None) -> tuple:
    """Возвращает (start_date, end_date) для периода."""
    if month:
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
    return start, end


def _period_label(year: int, month: int = None) -> str:
    """За 2024 г. / За ноябрь 2024 г."""
    if month:
        return f"За {MONTHS_RU[month]} {year} г."
    return f"За {year} г."


# =========================================================
#  Отчёт 1: Нагрузка
# =========================================================

def build_nagruzka_report(year: int, month: int = None) -> str:
    """
    Отчёт по нагрузке: исследование × группа пациентов.
    """
    start, end = _period_dates(year, month)

    sql = """
        SELECT
            it.Issledovanie AS IssledovanieName,
            ISNULL(pg.PatsientGroupName, '—') AS GroupName,
            COUNT(*) AS Cnt
        FROM Protocol p
            INNER JOIN IssledovanieType it ON p.IssledovanieID = it.IssledovanieID
            LEFT JOIN Patsient pat ON p.PatsientID = pat.PatsientID
            LEFT JOIN PatsientGroup pg ON pat.PatsientGroupID = pg.PatsientGroupID
        WHERE p.ProtocolDate >= ? AND p.ProtocolDate < ?
        GROUP BY it.Issledovanie, pg.PatsientGroupName
        ORDER BY it.Issledovanie, pg.PatsientGroupName
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, [start, end])
        rows = cur.fetchall()

    # Собираем: {исследование: {группа: count}}
    data = {}
    all_groups = set()
    for iss, grp, cnt in rows:
        data.setdefault(iss, {})
        data[iss][grp] = cnt
        all_groups.add(grp)

    # Порядок групп: ОМС, ДМС, платные, остальные
    preferred = ["ОМС", "ДМС", "платные услуги"]
    groups = [g for g in preferred if g in all_groups]
    groups += sorted(g for g in all_groups if g not in preferred)

    # Итоги по группам
    totals_by_group = {g: sum(d.get(g, 0) for d in data.values()) for g in groups}
    grand_total = sum(totals_by_group.values())

    # Строим HTML
    header_cells = "".join(f"<th>{g}</th>" for g in groups)
    body_rows = []
    for iss in sorted(data.keys()):
        row = f"<tr><td class='left'>{iss}</td>"
        for g in groups:
            cnt = data[iss].get(g, 0)
            row += f"<td>{cnt if cnt else '—'}</td>"
        total = sum(data[iss].values())
        row += f"<td><b>{total}</b></td></tr>"
        body_rows.append(row)

    total_cells = "".join(f"<td><b>{totals_by_group[g]}</b></td>" for g in groups)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Нагрузка {year}</title>
<style>
    body {{ font-family: 'Times New Roman', serif; font-size: 12pt; margin: 40px; }}
    h2 {{ text-align: center; margin: 5px 0; }}
    h3 {{ text-align: center; margin: 5px 0 20px 0; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
    th, td {{ border: 1px solid #000; padding: 4px 8px; text-align: center; }}
    th {{ background: #eee; }}
    td.left {{ text-align: left; }}
    .footer {{ margin-top: 40px; text-align: right; }}
</style>
</head>
<body>

<h2>Нагрузка отделения эндоскопии</h2>
<h3>{_period_label(year, month)}</h3>

<table>
    <thead>
        <tr>
            <th>Исследование</th>
            {header_cells}
            <th>Всего</th>
        </tr>
    </thead>
    <tbody>
        {''.join(body_rows)}
        <tr>
            <td class="left"><b>ИТОГО</b></td>
            {total_cells}
            <td><b>{grand_total}</b></td>
        </tr>
    </tbody>
</table>

<div class="footer">
    Зав. отделением ______________
</div>

</body>
</html>"""
    return html


# =========================================================
#  Отчёт 2: Гистология
# =========================================================

def build_gistologia_report(year: int, month: int = None, top_n: int = 20) -> str:
    """
    Отчёт по гистологии:
      - всего протоколов / с биопсией / с гистологией / с цитологией
      - топ-N заключений
    """
    start, end = _period_dates(year, month)

    with get_connection() as conn:
        cur = conn.cursor()

        # Общее количество
        cur.execute("""
            SELECT
                COUNT(*) AS Total,
                SUM(CASE WHEN p.Biopsia = 'да' THEN 1 ELSE 0 END) AS Biopsia,
                SUM(CASE WHEN p.Gistologia = 'да' THEN 1 ELSE 0 END) AS Gisto,
                SUM(CASE WHEN p.Tsitologia = 'да' THEN 1 ELSE 0 END) AS Tsito
            FROM Protocol p
            WHERE p.ProtocolDate >= ? AND p.ProtocolDate < ?
        """, [start, end])
        row = cur.fetchone()
        total, biopsia, gisto, tsito = row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0

        # Топ заключений (по протоколам с гистологией)
        cur.execute(f"""
            SELECT TOP {int(top_n)}
                z.ZakluchenieText,
                COUNT(*) AS Cnt
            FROM Zakluchenie z
                INNER JOIN Protocol p ON z.ProtocolID = p.ProtocolID
            WHERE p.ProtocolDate >= ? AND p.ProtocolDate < ?
              AND p.Gistologia = 'да'
              AND z.ZakluchenieText IS NOT NULL
              AND z.ZakluchenieText <> ''
            GROUP BY z.ZakluchenieText
            ORDER BY COUNT(*) DESC
        """, [start, end])
        top_rows = cur.fetchall()

    total_gisto = gisto if gisto else 1
    top_html = []
    for i, (text, cnt) in enumerate(top_rows, 1):
        pct = cnt * 100.0 / total_gisto
        top_html.append(
            f"<tr><td>{i}</td><td class='left'>{text}</td>"
            f"<td>{cnt}</td><td>{pct:.1f}%</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Гистология {year}</title>
<style>
    body {{ font-family: 'Times New Roman', serif; font-size: 12pt; margin: 40px; }}
    h2 {{ text-align: center; margin: 5px 0; }}
    h3 {{ text-align: center; margin: 5px 0 20px 0; }}
    .summary {{ margin: 20px 0; }}
    .summary div {{ margin: 4px 0; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
    th, td {{ border: 1px solid #000; padding: 4px 8px; text-align: center; }}
    th {{ background: #eee; }}
    td.left {{ text-align: left; }}
    .footer {{ margin-top: 40px; text-align: right; }}
</style>
</head>
<body>

<h2>Отчёт по гистологическим исследованиям</h2>
<h3>{_period_label(year, month)}</h3>

<div class="summary">
    <div><b>Всего протоколов:</b> {total}</div>
    <div><b>С биопсией:</b> {biopsia}</div>
    <div><b>С гистологией:</b> {gisto}</div>
    <div><b>С цитологией:</b> {tsito}</div>
</div>

<h3>Топ-{top_n} выявленных заключений (гистология)</h3>
<table>
    <thead>
        <tr>
            <th>#</th>
            <th>Заключение</th>
            <th>Кол-во</th>
            <th>%</th>
        </tr>
    </thead>
    <tbody>
        {''.join(top_html) if top_html else '<tr><td colspan="4">Нет данных</td></tr>'}
    </tbody>
</table>

<div class="footer">
    Зав. отделением ______________
</div>

</body>
</html>"""
    return html


# =========================================================
#  Сохранение и открытие
# =========================================================

def save_and_open(html: str, filename: str):
    """Сохраняет HTML в data/reports/ и открывает в браузере."""
    import webbrowser
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    reports_dir = os.path.join(here, "data", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    webbrowser.open("file:///" + path.replace("\\", "/"))
    return path


def run_report(form: str, year: int, month: int = None):
    """
    Запускает отчёт.
    form = 'nagruzka' | 'gistologia'
    """
    if form == "gistologia":
        html = build_gistologia_report(year, month)
        suffix = f"{year}" + (f"_{month:02d}" if month else "")
        return save_and_open(html, f"gistologia_{suffix}.html")
    else:  # nagruzka
        html = build_nagruzka_report(year, month)
        suffix = f"{year}" + (f"_{month:02d}" if month else "")
        return save_and_open(html, f"nagruzka_{suffix}.html")