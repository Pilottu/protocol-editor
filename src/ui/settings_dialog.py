import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QCheckBox, QRadioButton,
    QGroupBox, QFormLayout, QGridLayout, QListWidget,
    QComboBox, QSpinBox, QMessageBox, QTreeWidget, QTreeWidgetItem,
)
from PyQt6.QtCore import Qt

sys.path.insert(0, r"E:\Prog\Piton\src")
from db import (
    get_references, get_connection,
    get_settings_with_defaults, save_settings,
)
from config import load_config, save_config
from ui.database_tab import DatabaseTab


class FilterTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        letters_widget = QWidget()
        letters_grid = QGridLayout(letters_widget)
        letters = "А Б В Г Д Е Ж З И Й К Л М Н О П Р С Т У Ф Х Ц Ч Ш Щ Ъ Ы Ь Э Ю Я".split()
        row, col = 0, 0
        for letter in letters:
            btn = QPushButton(letter)
            btn.setFixedSize(40, 30)
            letters_grid.addWidget(btn, row, col)
            col += 1
            if col >= 17:
                col = 0
                row += 1
        btn_all = QPushButton("Все")
        btn_all.setFixedSize(50, 30)
        letters_grid.addWidget(btn_all, row, col)
        layout.addWidget(letters_widget)

        self.chk_only_current = QCheckBox("Ограничиться текущим исследованием")
        self.chk_only_unprinted = QCheckBox("Ограничиться неотпечатанными")
        layout.addWidget(self.chk_only_current)
        layout.addWidget(self.chk_only_unprinted)

        btn_apply = QPushButton("Применить")
        btn_apply.setFixedWidth(120)
        layout.addWidget(btn_apply, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()


class OtdelenieTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.otdelenie_edit = QLineEdit()
        self.zaved_edit = QLineEdit()
        form.addRow("Отделение", self.otdelenie_edit)
        form.addRow("Заведующий", self.zaved_edit)
        layout.addLayout(form)
        btn_save = QPushButton("Сохранить изменения")
        btn_save.setFixedWidth(180)
        layout.addWidget(btn_save, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        self._load()

    def _load(self):
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT TOP 1 OtdelenieName, OtdelenieHead FROM Otdelenie")
                row = cur.fetchone()
                if row:
                    self.otdelenie_edit.setText(row[0] or "")
                    self.zaved_edit.setText(row[1] or "")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить данные отделения:\n{e}")


class DocumentTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.zagolovok_edit = QLineEdit()
        form.addRow("Заголовок", self.zagolovok_edit)
        layout.addLayout(form)

        formats_row = QHBoxLayout()
        proto_group = QGroupBox("Формат протокола")
        proto_layout = QVBoxLayout(proto_group)
        self.rb_proto_html = QRadioButton("Internet Explorer")
        self.rb_proto_rtf = QRadioButton("Microsoft Word")
        self.rb_proto_html.setChecked(True)
        proto_layout.addWidget(self.rb_proto_html)
        proto_layout.addWidget(self.rb_proto_rtf)
        formats_row.addWidget(proto_group)
        layout.addLayout(formats_row)

        page_group = QGroupBox("Параметры страницы (для Internet Explorer)")
        page_layout = QVBoxLayout(page_group)
        kolont_group = QGroupBox("Колонтитулы")
        kolont_layout = QFormLayout(kolont_group)
        self.verh_kolont = QLineEdit("&w&bPage &p of &P")
        self.nizh_kolont = QLineEdit("&u&b&d")
        kolont_layout.addRow("Верхний колонтитул", self.verh_kolont)
        kolont_layout.addRow("Нижний колонтитул", self.nizh_kolont)
        page_layout.addWidget(kolont_group)

        orient_fields = QHBoxLayout()
        orient_group = QGroupBox("Ориентация")
        orient_layout = QVBoxLayout(orient_group)
        self.rb_knizh = QRadioButton("Книжная")
        self.rb_albom = QRadioButton("Альбомная")
        self.rb_knizh.setChecked(True)
        orient_layout.addWidget(self.rb_knizh)
        orient_layout.addWidget(self.rb_albom)
        orient_fields.addWidget(orient_group)

        fields_group = QGroupBox("Поля (мм)")
        fields_layout = QGridLayout(fields_group)
        self.field_left = QLineEdit("19.05")
        self.field_right = QLineEdit("19.05")
        self.field_top = QLineEdit("19.05")
        self.field_bottom = QLineEdit("19.05")
        fields_layout.addWidget(QLabel("Левое"), 0, 0)
        fields_layout.addWidget(self.field_left, 0, 1)
        fields_layout.addWidget(QLabel("Правое"), 0, 2)
        fields_layout.addWidget(self.field_right, 0, 3)
        fields_layout.addWidget(QLabel("Верхнее"), 1, 0)
        fields_layout.addWidget(self.field_top, 1, 1)
        fields_layout.addWidget(QLabel("Нижнее"), 1, 2)
        fields_layout.addWidget(self.field_bottom, 1, 3)
        orient_fields.addWidget(fields_group)
        page_layout.addLayout(orient_fields)
        layout.addWidget(page_group)

        btn_row = QHBoxLayout()
        btn_default = QPushButton("По умолчанию")
        btn_apply = QPushButton("Применить")
        btn_row.addStretch()
        btn_row.addWidget(btn_default)
        btn_row.addWidget(btn_apply)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

        self._load_from_db()

    def _load_from_db(self):
        """Загружает настройки из БД."""
        s = get_settings_with_defaults()

        self.zagolovok_edit.setText(s.get("document_title", ""))

        if s.get("format_protocol") == "html":
            self.rb_proto_html.setChecked(True)
        else:
            self.rb_proto_rtf.setChecked(True)

        self.verh_kolont.setText(s.get("kartoteka_header", "&w&bPage &p of &P"))
        self.nizh_kolont.setText(s.get("kartoteka_footer", "&u&b&d"))

        if s.get("kartoteka_orientation") == "portrait":
            self.rb_knizh.setChecked(True)
        else:
            self.rb_albom.setChecked(True)

        self.field_left.setText(s.get("page_margin_left", "19.05"))
        self.field_right.setText(s.get("page_margin_right", "19.05"))
        self.field_top.setText(s.get("page_margin_top", "19.05"))
        self.field_bottom.setText(s.get("page_margin_bottom", "19.05"))


class ProtocolEditorTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        styles_group = QGroupBox("Стили")
        styles_layout = QVBoxLayout(styles_group)
        self.rb_list = QRadioButton("Лист")
        self.rb_two_pages = QRadioButton("Две страницы")
        self.rb_two_pages.setChecked(True)
        styles_layout.addWidget(self.rb_list)
        styles_layout.addWidget(self.rb_two_pages)
        layout.addWidget(styles_group)
        layout.addStretch()


class ReportSettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form_group = QGroupBox("Выберите форму отчета")
        form_layout = QVBoxLayout(form_group)
        self.rb_nagruzka = QRadioButton("Нагрузка")
        self.rb_nozologia = QRadioButton("Гистология")
        self.rb_nagruzka.setChecked(True)
        form_layout.addWidget(self.rb_nagruzka)
        form_layout.addWidget(self.rb_nozologia)
        layout.addWidget(form_group)

        period_group = QGroupBox("Выберите периодичность отчета")
        period_layout = QVBoxLayout(period_group)
        self.rb_month = QRadioButton("Месячный")
        self.rb_year = QRadioButton("Годовой")
        self.rb_month.setChecked(True)
        period_layout.addWidget(self.rb_month)
        period_layout.addWidget(self.rb_year)
        layout.addWidget(period_group)

        date_form = QFormLayout()
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2000, 2100)
        self.year_spin.setValue(2026)
        self.month_combo = QComboBox()
        self.month_combo.addItems([
            "январь", "февраль", "март", "апрель", "май", "июнь",
            "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"
        ])
        self.month_combo.setCurrentIndex(9)
        date_form.addRow("Отчетный год", self.year_spin)
        date_form.addRow("Отчетный месяц", self.month_combo)
        layout.addLayout(date_form)

        layout.addStretch()


class ReferencesTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        org_row = QHBoxLayout()
        org_row.addWidget(QLabel("Организация"))
        self.org_edit = QLineEdit()
        org_row.addWidget(self.org_edit, stretch=1)
        btn_org_change = QPushButton("Изменить")
        org_row.addWidget(btn_org_change)
        layout.addLayout(org_row)

        otd_row = QHBoxLayout()
        otd_row.addWidget(QLabel("Отделение"))
        self.otd_edit = QLineEdit()
        otd_row.addWidget(self.otd_edit, stretch=1)
        btn_otd_change = QPushButton("Изменить")
        otd_row.addWidget(btn_otd_change)
        layout.addLayout(otd_row)

        grp_group = QGroupBox("Группы пациентов")
        grp_layout = QHBoxLayout(grp_group)
        grp_left = QVBoxLayout()
        self.grp_edit = QLineEdit()
        self.grp_list = QListWidget()
        grp_left.addWidget(self.grp_edit)
        grp_left.addWidget(self.grp_list)
        grp_layout.addLayout(grp_left, stretch=1)
        grp_btn_layout = QVBoxLayout()
        btn_grp_add = QPushButton("Добавить")
        btn_grp_change = QPushButton("Изменить")
        grp_btn_layout.addWidget(btn_grp_add)
        grp_btn_layout.addWidget(btn_grp_change)
        grp_btn_layout.addStretch()
        grp_layout.addLayout(grp_btn_layout)
        layout.addWidget(grp_group)

        iss_group = QGroupBox("Исследования")
        iss_layout = QHBoxLayout(iss_group)
        self.iss_tree = QTreeWidget()
        self.iss_tree.setHeaderLabels(["Исследования"])
        iss_layout.addWidget(self.iss_tree, stretch=1)

        iss_form_widget = QWidget()
        iss_form = QVBoxLayout(iss_form_widget)
        iss_name_row = QHBoxLayout()
        iss_name_row.addWidget(QLabel("Исследование"))
        self.iss_name_edit = QLineEdit()
        iss_name_row.addWidget(self.iss_name_edit, stretch=1)
        iss_form.addLayout(iss_name_row)

        cel_group = QGroupBox("Цель исследования")
        cel_layout = QVBoxLayout(cel_group)
        self.chk_lecheb = QCheckBox("Лечебно-диагностическая")
        self.chk_sanats = QCheckBox("Санация")
        self.chk_intub = QCheckBox("Интубация")
        self.chk_smiv = QCheckBox("Смыв")
        self.chk_phmetr = QCheckBox("PH-метрия")
        for chk in [self.chk_lecheb, self.chk_sanats, self.chk_intub,
                    self.chk_smiv, self.chk_phmetr]:
            cel_layout.addWidget(chk)
        iss_form.addWidget(cel_group)

        bio_group = QGroupBox("Биопсия")
        bio_layout = QVBoxLayout(bio_group)
        self.chk_tsitologia = QCheckBox("Цитология")
        self.chk_gistologia = QCheckBox("Гистология")
        bio_layout.addWidget(self.chk_tsitologia)
        bio_layout.addWidget(self.chk_gistologia)
        iss_form.addWidget(bio_group)

        rep_group = QGroupBox("Отчеты")
        rep_layout = QVBoxLayout(rep_group)
        self.chk_show_in_report = QCheckBox("Показывать в отчете о нагрузке")
        order_row = QHBoxLayout()
        order_row.addWidget(QLabel("Порядок в отчете"))
        self.order_spin = QSpinBox()
        self.order_spin.setRange(0, 9999)
        order_row.addWidget(self.order_spin)
        rep_layout.addWidget(self.chk_show_in_report)
        rep_layout.addLayout(order_row)
        iss_form.addWidget(rep_group)

        iss_btn_row = QHBoxLayout()
        btn_iss_change = QPushButton("Изменить")
        btn_iss_add = QPushButton("Добавить")
        iss_btn_row.addStretch()
        iss_btn_row.addWidget(btn_iss_change)
        iss_btn_row.addWidget(btn_iss_add)
        iss_btn_row.addStretch()
        iss_form.addLayout(iss_btn_row)

        iss_layout.addWidget(iss_form_widget, stretch=1)
        layout.addWidget(iss_group, stretch=1)
        self._load()

    def _load(self):
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT TOP 1 OrganName FROM Organizatsia")
                row = cur.fetchone()
                if row:
                    self.org_edit.setText(row[0] or "")
                cur.execute("SELECT TOP 1 OtdelenieName FROM Otdelenie")
                row = cur.fetchone()
                if row:
                    self.otd_edit.setText(row[0] or "")
                cur.execute("SELECT PatsientGroupName FROM PatsientGroup ORDER BY PatsientGroupName")
                self.grp_list.clear()
                for row in cur.fetchall():
                    self.grp_list.addItem(row[0] or "")
                cur.execute(
                    "SELECT IssledovanieGroupID, IssledovanieGroupName FROM IssledovanieGroup ORDER BY IssledovanieGroupID"
                )
                groups = cur.fetchall()
                cur.execute(
                    "SELECT IssledovanieID, Issledovanie, IssledovanieGroupID FROM IssledovanieType ORDER BY Issledovanie"
                )
                issleds = cur.fetchall()
                self.iss_tree.clear()
                group_items = {}
                for gid, gname in groups:
                    item = QTreeWidgetItem([gname])
                    item.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "id": gid})
                    self.iss_tree.addTopLevelItem(item)
                    group_items[gid] = item
                for iid, iname, gid in issleds:
                    item = QTreeWidgetItem([iname])
                    item.setData(0, Qt.ItemDataRole.UserRole, {"type": "issled", "id": iid})
                    if gid in group_items:
                        group_items[gid].addChild(item)
                    else:
                        self.iss_tree.addTopLevelItem(item)
                self.iss_tree.expandAll()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить справочники:\n{e}")


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(950, 620)   # ← уменьшено

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(OtdelenieTab(), "Данные отделения")
        self.tabs.addTab(DocumentTab(), "Документ")
        self.tabs.addTab(ProtocolEditorTab(), "Редактор протокола")
        self.tabs.addTab(ReportSettingsTab(), "Настройки отчета")
        self.tabs.addTab(ReferencesTab(), "Справочники")
        self.tabs.addTab(DatabaseTab(), "База данных")
        layout.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_save = QPushButton("Сохранить настройки")
        btn_save.clicked.connect(self.on_save_settings)
        btn_row.addWidget(btn_save)
        btn_cancel = QPushButton("Отменить")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def on_save_settings(self):
        """Собирает настройки со всех вкладок и сохраняет в БД."""
        data = {}

        # Вкладка «Данные отделения» (индекс 0)
        tab = self.tabs.widget(0)   # OtdelenieTab
        if hasattr(tab, "otdelenie_edit"):
            data["otdelenie_name"] = tab.otdelenie_edit.text()
        if hasattr(tab, "zaved_edit"):
            data["otdelenie_head"] = tab.zaved_edit.text()

        # Вкладка «Документ» (индекс 1)
        tab = self.tabs.widget(1)   # DocumentTab
        if hasattr(tab, "zagolovok_edit"):
            data["document_title"] = tab.zagolovok_edit.text()
        if hasattr(tab, "rb_proto_html") and hasattr(tab, "rb_proto_rtf"):
            data["format_protocol"] = "html" if tab.rb_proto_html.isChecked() else "rtf"
        if hasattr(tab, "verh_kolont") and hasattr(tab, "nizh_kolont"):
            data["kartoteka_header"] = tab.verh_kolont.text()
            data["kartoteka_footer"] = tab.nizh_kolont.text()
        if hasattr(tab, "rb_knizh") and hasattr(tab, "rb_albom"):
            data["kartoteka_orientation"] = "portrait" if tab.rb_knizh.isChecked() else "landscape"
        if hasattr(tab, "field_left"):
            data["page_margin_left"] = tab.field_left.text()
            data["page_margin_right"] = tab.field_right.text()
            data["page_margin_top"] = tab.field_top.text()
            data["page_margin_bottom"] = tab.field_bottom.text()

        # Вкладка «Редактор протокола» (индекс 2)
        tab = self.tabs.widget(2)   # ProtocolEditorTab
        if hasattr(tab, "rb_list") and hasattr(tab, "rb_two_pages"):
            data["protocol_style"] = "list" if tab.rb_list.isChecked() else "two_pages"

        # Сохраняем в БД
        ok = save_settings(data)
        if ok:
            QMessageBox.information(self, "Сохранено", "Настройки сохранены.")
            self.accept()
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить настройки.")
            
if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dlg = SettingsDialog()
    dlg.show()
    sys.exit(app.exec())