import sys
import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTreeView, QTabWidget, QFormLayout, QLineEdit,
    QComboBox, QDateEdit, QTextEdit, QPushButton, QLabel,
    QToolBar, QMessageBox, QListWidget, QListWidgetItem,
    QGroupBox, QCheckBox, QInputDialog, QTimeEdit,
    QSpinBox
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction
from PyQt6.QtCore import Qt, QDate, QTime


sys.path.insert(0, r"E:\Prog\Piton\src")
from db import (
    get_references,
    get_tree_data,
    get_protocol_full,
    get_zakluchenia,
    get_vrachi,
    get_vrach_types,
    get_anestezia_types,
    get_distinct_zakluchenia,
    get_next_protocol_nomer,
    reload_config,
    update_patsient,
    insert_patsient,
    update_protocol,
    insert_protocol,
    delete_protocol,
    insert_zakluchenie,
    update_zakluchenie,
    delete_zakluchenie,
    insert_vrach,
    delete_vrach,
    insert_vrach_type_if_missing,
    update_vrach_type,
    get_connection,
)


class MainWindow(QMainWindow):
    def on_filter_changed(self, _index):
        """При смене исследования — перезагружаем дерево."""
        self._load_tree()
    def _recalc_vozrast(self, _date=None):
        """Пересчитывает возраст по дате рождения и дате протокола."""
        birth = self.datebirth_edit.date()
        proto = self.date_edit.date()
        if birth.year() == 1980 and birth.month() == 1 and birth.day() == 1:
            return  # дефолт — не считаем
        years = proto.year() - birth.year()
        if (proto.month(), proto.day()) < (birth.month(), birth.day()):
            years -= 1
        if years < 0:
            years = 0
        self.vozrast_edit.setText(str(years))    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Редактор протоколов (Python)")
        self.resize(1600, 900)

        self.references = {}
        self.current_protocol_id = None
        self.current_patient_id = None
        self.current_napravlenie_id = None
        self.is_connected = False

        self._build_toolbar()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

                # --- Левая панель: дерево ---
        self.tree = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["Исследование / Пациент / Протокол"])
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(False)
        self.tree.clicked.connect(self.on_tree_click)
        splitter.addWidget(self.tree)

        # --- Правая панель ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Верхняя часть формы
        top_form = QFormLayout()
        self.issledovanie_combo = QComboBox()
        self.fio_edit = QLineEdit()
        self.pol_combo = QComboBox()
        self.pol_combo.addItems(["", "М", "Ж"])
        self.karta_edit = QLineEdit()
        self.gruppa_combo = QComboBox()
        top_form.addRow("Исследование", self.issledovanie_combo)
        top_form.addRow("Ф.И.О.", self.fio_edit)
        top_form.addRow("Пол", self.pol_combo)
        top_form.addRow("Амб. карта №", self.karta_edit)
        top_form.addRow("Группа", self.gruppa_combo)
        right_layout.addLayout(top_form)

        self.fio_edit.textChanged.connect(self.on_fio_changed)
        self.issledovanie_combo.currentIndexChanged.connect(self.on_filter_changed)

        # --- Вкладки ---
        self.tabs = QTabWidget()

        # Вкладка «Шапка»
        shapka_widget = self._build_shapka_tab()
        self.tabs.addTab(shapka_widget, "Шапка")

        # Вкладка «Текст»
        text_widget = self._build_text_tab()
        self.tabs.addTab(text_widget, "Текст")

        # Вкладка «Рез. патологогистологического исслед.»
        patolog_widget = self._build_patolog_tab()
        self.tabs.addTab(patolog_widget, "Рез. патологогистологического исслед.")
        self.patolog_tab_index = self.tabs.count() - 1
        self.tabs.setTabVisible(self.patolog_tab_index, False)

        right_layout.addWidget(self.tabs, stretch=1)

        # --- Общие кнопки под табами (для всех вкладок) ---
        proto_btn_layout = QHBoxLayout()
        self.btn_save_proto = QPushButton("Сохранить изменения")
        self.btn_add_proto = QPushButton("Добавить протокол")
        self.btn_del_proto = QPushButton("Удалить протокол")
        for b in [self.btn_save_proto, self.btn_add_proto, self.btn_del_proto]:
            proto_btn_layout.addWidget(b)
        right_layout.addLayout(proto_btn_layout)

        self.btn_save_proto.clicked.connect(self.on_save_changes)
        self.btn_add_proto.clicked.connect(self.on_add_protocol)
        self.btn_del_proto.clicked.connect(self.on_delete_protocol)

        splitter.addWidget(right_panel)
        splitter.setSizes([500, 1100])
        layout.addWidget(splitter)

        # Статус-бар
        self.status_label = QLabel("Проверка соединения...")
        self.statusBar().addPermanentWidget(self.status_label)

        self._try_connect()
        self._update_ui_state()

    # =========================================================
    #  Построение вкладок
    # =========================================================
    def _build_shapka_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        # Организация, Отделение, Исследование — убраны (#4)
        self.nomer_edit = QLineEdit()
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        # Ф.И.О. → Дата рождения (#5)
        self.datebirth_edit = QDateEdit()
        self.datebirth_edit.setCalendarPopup(True)
        self.datebirth_edit.setDate(QDate(1980, 1, 1))
        self.vozrast_edit = QLineEdit()
        self.vozrast_edit.setReadOnly(True)          # возраст только для чтения
        self.pol_shapka_edit = QLineEdit()
        self.adres_edit = QLineEdit()
        self.karta_shapka_edit = QLineEdit()
        self.istor_edit = QLineEdit()
        # Отделение (подраздел.) → Направление оформлено (#6)
        self.napravlenie_edit = QLineEdit()          # editable
        self.anamnez_edit = QLineEdit()
        self.apparat_combo = QComboBox()
        self.apparat_combo.setEditable(True)         # editable, как Анестезия (#7)
        self.anestezia_combo = QComboBox()

        form.addRow("Протокол №", self.nomer_edit)
        form.addRow("Дата", self.date_edit)
        form.addRow("Дата рождения", self.datebirth_edit)       # (#5)
        form.addRow("Возраст", self.vozrast_edit)                # (#5)
        form.addRow("Пол", self.pol_shapka_edit)
        form.addRow("Адрес", self.adres_edit)
        form.addRow("Амб. карта №", self.karta_shapka_edit)
        form.addRow("История болезни №", self.istor_edit)
        form.addRow("Направление оформлено", self.napravlenie_edit)  # (#6)
        form.addRow("Анамнез", self.anamnez_edit)
        form.addRow("Модель аппарата", self.apparat_combo)       # (#7)
        form.addRow("Анестезия", self.anestezia_combo)

        self.nomer_edit.textChanged.connect(self.on_nomer_changed)

        flags_layout = QHBoxLayout()
        self.chk_biopsia = QCheckBox("Биопсия")
        self.chk_tsitologia = QCheckBox("Цитология")
        self.chk_gistologia = QCheckBox("Гистология")
        self.chk_sanats = QCheckBox("Санационная")
        self.chk_lecheb = QCheckBox("Лечебно-диагностическая")
        self.chk_intub = QCheckBox("Интубация")
        self.chk_phmetr = QCheckBox("PH-метрия")
        self.chk_smiv = QCheckBox("Бронх. смыв")
        for chk in [self.chk_biopsia, self.chk_tsitologia, self.chk_gistologia,
                    self.chk_sanats, self.chk_lecheb, self.chk_intub,
                    self.chk_phmetr, self.chk_smiv]:
            flags_layout.addWidget(chk)
        form.addRow("Флаги", flags_layout)

        # Показ вкладки «Рез. патологогистологического исслед.» по галкам
        for chk in (self.chk_biopsia, self.chk_tsitologia, self.chk_gistologia):
            chk.stateChanged.connect(self._on_biopsia_flags_changed)

        # Пересчёт возраста при смене даты рождения
        self.datebirth_edit.dateChanged.connect(self._recalc_vozrast)
        # И при смене даты протокола (возраст на дату протокола)
        self.date_edit.dateChanged.connect(self._recalc_vozrast)
        
        return w

    def _build_text_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self.protocol_text = QTextEdit()
        self.protocol_text.setPlaceholderText("Текст протокола...")
        layout.addWidget(QLabel("Текст протокола:"))
        layout.addWidget(self.protocol_text, stretch=3)

        # Заключения (как блок «Врач» — с combo для выбора из справочника)
        zakl_group = QGroupBox("Заключение")
        zakl_layout = QHBoxLayout(zakl_group)
        self.zakl_list = QListWidget()
        zakl_btn_layout = QVBoxLayout()
        # Combo для выбора существующего заключения
        self.zakl_combo = QComboBox()
        self.zakl_combo.setEditable(True)
        zakl_btn_layout.addWidget(self.zakl_combo)
        self.btn_zakl_add = QPushButton("Добавить")
        self.btn_zakl_edit = QPushButton("Изменить")
        self.btn_zakl_del = QPushButton("Удалить")
        for b in [self.btn_zakl_add, self.btn_zakl_edit, self.btn_zakl_del]:
            b.setFixedWidth(400)          # ← фиксированная ширина
            zakl_btn_layout.addWidget(b)
        zakl_btn_layout.addStretch()
        zakl_layout.addWidget(self.zakl_list, stretch=1)
        zakl_layout.addLayout(zakl_btn_layout)
        layout.addWidget(zakl_group, stretch=1)

        # Врачи
        vrach_group = QGroupBox("Врач")
        vrach_layout = QHBoxLayout(vrach_group)
        self.vrach_list = QListWidget()
        vrach_btn_layout = QVBoxLayout()
        self.vrach_combo = QComboBox()
        self.vrach_combo.setEditable(True)
        self.btn_vrach_add = QPushButton("Добавить")
        self.btn_vrach_edit = QPushButton("Изменить")
        self.btn_vrach_del = QPushButton("Удалить")
        vrach_btn_layout.addWidget(self.vrach_combo)
        for b in [self.btn_vrach_add, self.btn_vrach_edit, self.btn_vrach_del]:
            b.setFixedWidth(400)          # ← та же ширина
            vrach_btn_layout.addWidget(b)
        vrach_btn_layout.addStretch()
        vrach_layout.addWidget(self.vrach_list, stretch=1)
        vrach_layout.addLayout(vrach_btn_layout)
        layout.addWidget(vrach_group, stretch=1)

        self.btn_zakl_add.clicked.connect(self.on_zakl_add)
        self.btn_zakl_edit.clicked.connect(self.on_zakl_edit)
        self.btn_zakl_del.clicked.connect(self.on_zakl_del)

        self.btn_vrach_add.clicked.connect(self.on_vrach_add)
        self.btn_vrach_edit.clicked.connect(self.on_vrach_edit)
        self.btn_vrach_del.clicked.connect(self.on_vrach_del)

        return w

    def _build_patolog_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.patolog_nomer = QLineEdit()
        self.patolog_result_nomer = QLineEdit()
        self.patolog_date_in = QDateEdit()
        self.patolog_date_in.setCalendarPopup(True)
        self.patolog_date_in.setDate(QDate.currentDate())
        self.patolog_time_in = QTimeEdit()
        self.patolog_time_in.setDisplayFormat("HH:mm:ss")
        self.patolog_time_in.setTime(QTime(0, 0, 0))
        self.patolog_biopsia_diag = QLineEdit()
        self.patolog_biopsia_sroch = QLineEdit()
        self.patolog_oper_material = QLineEdit()
        self.patolog_kusochki1 = QLineEdit()
        self.patolog_kusochki2 = QLineEdit()
        self.patolog_methodika = QLineEdit()
        self.patolog_opisanie = QTextEdit()
        self.patolog_zakluchenie = QTextEdit()
        self.patolog_kod = QLineEdit()
        self.patolog_date_result = QDateEdit()
        self.patolog_date_result.setCalendarPopup(True)
        self.patolog_date_result.setDate(QDate.currentDate())
        self.patolog_patologoanatom = QLineEdit()
        self.patolog_laborant = QLineEdit()

        form.addRow("Номер", self.patolog_nomer)
        form.addRow("ResultNomer", self.patolog_result_nomer)
        form.addRow("Дата поступления", self.patolog_date_in)
        form.addRow("Часы поступления", self.patolog_time_in)
        form.addRow("Биопсия диагностическая", self.patolog_biopsia_diag)
        form.addRow("Биопсия срочная", self.patolog_biopsia_sroch)
        form.addRow("Операционный материал", self.patolog_oper_material)
        form.addRow("Количество кусочков (1)", self.patolog_kusochki1)
        form.addRow("Количество кусочков (2)", self.patolog_kusochki2)
        form.addRow("Методика окраски", self.patolog_methodika)
        form.addRow("Описание", self.patolog_opisanie)
        form.addRow("Заключение", self.patolog_zakluchenie)
        form.addRow("Код", self.patolog_kod)
        form.addRow("Дата результата", self.patolog_date_result)
        form.addRow("Патологоанатом", self.patolog_patologoanatom)
        form.addRow("Лаборант", self.patolog_laborant)

        return w

    # =========================================================
    #  Показ/скрытие вкладки по флагам биопсии
    # =========================================================
    def _on_biopsia_flags_changed(self, _state=None):
        any_flag = (self.chk_biopsia.isChecked() or
                    self.chk_tsitologia.isChecked() or
                    self.chk_gistologia.isChecked())
        self.tabs.setTabVisible(self.patolog_tab_index, any_flag)

    # =========================================================
    #  Панель инструментов
    # =========================================================
    def _build_toolbar(self):
        tb = QToolBar("Основная")
        self.addToolBar(tb)

        actions = [
            ("Настройки", self.on_settings),
            ("Отчёт", self.on_report),
            ("Снимки", self.on_snapshots),
            ("Печать", self.on_print),
            ("Шаблоны", self.on_templates),
            ("Редактор шаблонов", self.on_templates_edit),
        ]
        for name, handler in actions:
            act = QAction(name, self)
            act.triggered.connect(handler)
            tb.addAction(act)

    # =========================================================
    #  Подключение
    # =========================================================
    def _try_connect(self):
        try:
            self.references = get_references()
            self.is_connected = True
            self.status_label.setText("✅ Подключено к базе")
            self.status_label.setStyleSheet("color: green;")

            # Создаём таблицу Settings, если её нет
            from db import ensure_settings_table
            ensure_settings_table()

            self._load_references()
            self._load_tree()
        except Exception as e:
            self.is_connected = False
            self.status_label.setText("❌ Нет соединения с базой")
            self.status_label.setStyleSheet("color: red;")
            self._clear_ui()
            QMessageBox.warning(
                self, "Нет соединения",
                f"Не удалось подключиться к базе данных:\n{e}\n\n"
                f"Откройте «Настройки» → «База данных»."
            )

    def _clear_ui(self):
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["Исследование / Пациент / Протокол"])
        self.issledovanie_combo.clear()
        self.gruppa_combo.clear()
        self.apparat_combo.clear()
        self.vrach_combo.clear()
        self.anestezia_combo.clear()
        self.zakl_list.clear()
        self.vrach_list.clear()
        self.protocol_text.clear()

    def on_reconnect(self):
        reload_config()
        self._try_connect()

    # =========================================================
    #  Справочники
    # =========================================================
    def _load_references(self):
        refs = self.references

        self.issledovanie_combo.clear()
        self.issledovanie_combo.addItem("", None)
        for id_, name in refs.get("IssledovanieType", []):
            self.issledovanie_combo.addItem(name, id_)

        self.gruppa_combo.clear()
        self.gruppa_combo.addItem("", None)
        for id_, name in refs.get("PatsientGroup", []):
            self.gruppa_combo.addItem(name, id_)

        self.apparat_combo.clear()
        self.apparat_combo.addItem("", None)
        for id_, name in refs.get("Apparat", []):
            self.apparat_combo.addItem(name, id_)

        # otdelenie_podr_combo удалён — поле «Направление оформлено» стало QLineEdit

        current_vrach = self.vrach_combo.currentText() if hasattr(self, "vrach_combo") else ""
        self.vrach_combo.clear()
        self.vrach_combo.setEditable(True)
        self.vrach_combo.addItem("", None)
        try:
            for id_, fio in get_vrach_types():
                self.vrach_combo.addItem(fio, id_)
        except Exception:
            pass
        if current_vrach:
            self.vrach_combo.setCurrentText(current_vrach)

        current_anest = self.anestezia_combo.currentText() if hasattr(self, "anestezia_combo") else ""
        self.anestezia_combo.clear()
        self.anestezia_combo.setEditable(True)
        self.anestezia_combo.addItem("", None)
        try:
            for name in get_anestezia_types():
                self.anestezia_combo.addItem(name, name)
        except Exception:
            pass
        if current_anest:
            self.anestezia_combo.setCurrentText(current_anest)

        # Справочник заключений
        current_zakl = self.zakl_combo.currentText() if hasattr(self, "zakl_combo") else ""
        self.zakl_combo.clear()
        self.zakl_combo.setEditable(True)
        self.zakl_combo.addItem("", None)
        try:
            for text in get_distinct_zakluchenia():
                self.zakl_combo.addItem(text, text)
        except Exception:
            pass
        if current_zakl:
            self.zakl_combo.setCurrentText(current_zakl)

    # =========================================================
    #  Дерево
    # =========================================================
    def _load_tree(self):
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["Исследование / Пациент / Протокол"])

        try:
            data = get_tree_data()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить дерево:\n{e}")
            return

         # Фильтр по исследованию (из правого комбо)
        filter_iss_id = self.issledovanie_combo.currentData()

        for iss in data:
            # Если выбрано конкретное исследование — фильтруем
            if filter_iss_id and iss["issledovanie_id"] != filter_iss_id:
                continue

            iss_item = QStandardItem(iss["issledovanie_name"])
            iss_item.setEditable(False)
            iss_item.setData(
                {"type": "issledovanie", "id": iss["issledovanie_id"]},
                Qt.ItemDataRole.UserRole,
            )
            for pat in iss["patients"]:
                pat_item = QStandardItem(pat["fio"])
                pat_item.setEditable(False)
                pat_item.setData(
                    {"type": "patsient", "id": pat["patsient_id"]},
                    Qt.ItemDataRole.UserRole,
                )
                for proto in pat["protocols"]:
                    label = f'{proto["nomer"]} [{proto["year"]}]'
                    proto_item = QStandardItem(label)
                    proto_item.setEditable(False)
                    proto_item.setData(
                        {"type": "protocol", "id": proto["protocol_id"],
                         "nomer": proto["nomer"], "year": proto["year"]},
                        Qt.ItemDataRole.UserRole,
                    )
                    pat_item.appendRow(proto_item)
                iss_item.appendRow(pat_item)
            self.tree_model.appendRow(iss_item)

        # Разворачиваем только первый уровень (исследования)
        self.tree.expandToDepth(0)

        self.status_label.setText(
            f"✅ Подключено. Исследований: {self.tree_model.rowCount()}"
        )
        self.status_label.setStyleSheet("color: green;")

    def _reload_tree(self):
        current = self.current_protocol_id
        self._load_tree()
        if current:
            self._select_protocol_in_tree(current)

    def _select_protocol_in_tree(self, protocol_id: int):
        for i in range(self.tree_model.rowCount()):
            iss_item = self.tree_model.item(i)
            for j in range(iss_item.rowCount()):
                pat_item = iss_item.child(j)
                for k in range(pat_item.rowCount()):
                    proto_item = pat_item.child(k)
                    data = proto_item.data(Qt.ItemDataRole.UserRole)
                    if data and data.get("id") == protocol_id:
                        idx = self.tree_model.indexFromItem(proto_item)
                        self.tree.setCurrentIndex(idx)
                        self.tree.scrollTo(idx)
                        return

    # =========================================================
    #  Живой поиск
    # =========================================================
    def on_fio_changed(self, text: str):
        self._update_ui_state()
        text = text.strip().lower()
        if not text or not self.is_connected:
            return
        for i in range(self.tree_model.rowCount()):
            iss_item = self.tree_model.item(i)
            for j in range(iss_item.rowCount()):
                pat_item = iss_item.child(j)
                if pat_item.text().lower().startswith(text):
                    idx = self.tree_model.indexFromItem(pat_item)
                    self.tree.setCurrentIndex(idx)
                    self.tree.scrollTo(idx)
                    self.tree.expand(idx.parent())
                    return

    def on_nomer_changed(self, text: str):
        text = text.strip()
        if not text or not self.is_connected:
            return
        for i in range(self.tree_model.rowCount()):
            iss_item = self.tree_model.item(i)
            for j in range(iss_item.rowCount()):
                pat_item = iss_item.child(j)
                for k in range(pat_item.rowCount()):
                    proto_item = pat_item.child(k)
                    data = proto_item.data(Qt.ItemDataRole.UserRole)
                    if data and str(data.get("nomer", "")).startswith(text):
                        idx = self.tree_model.indexFromItem(proto_item)
                        self.tree.setCurrentIndex(idx)
                        self.tree.scrollTo(idx)
                        return

    def _set_fio_silent(self, text: str):
        self.fio_edit.blockSignals(True)
        self.fio_edit.setText(text or "")
        self.fio_edit.blockSignals(False)
        self._update_ui_state()

    def _set_nomer_silent(self, text: str):
        self.nomer_edit.blockSignals(True)
        self.nomer_edit.setText(text or "")
        self.nomer_edit.blockSignals(False)

    # =========================================================
    #  Обработчики дерева
    # =========================================================
    def on_tree_click(self, index):
        if not self.is_connected:
            return
        item = self.tree_model.itemFromIndex(index)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data.get("type") == "protocol":
            # Клик на протокол — загружаем
            self.current_protocol_id = data["id"]
            self._load_protocol(data["id"])
            self._update_ui_state()

        elif data.get("type") == "patsient":
            # Клик на пациента — разворачиваем его протоколы
            self.current_patient_id = data["id"]
            self.current_protocol_id = None

            # Сворачиваем всех, кроме этого
            for i in range(self.tree_model.rowCount()):
                iss_item = self.tree_model.item(i)
                if iss_item is item.parent():
                    # Разворачиваем этого пациента
                    self.tree.expand(index)
                else:
                    # Сворачиваем остальных пациентов
                    for j in range(iss_item.rowCount()):
                        pat_item = iss_item.child(j)
                        if pat_item is not item:
                            self.tree.collapse(self.tree_model.indexFromItem(pat_item))

            # Автоподстановка исследования из родителя
            parent_item = item.parent()
            if parent_item:
                parent_data = parent_item.data(Qt.ItemDataRole.UserRole)
                if parent_data and parent_data.get("type") == "issledovanie":
                    iss_id = parent_data.get("id")
                    for i in range(self.issledovanie_combo.count()):
                        if self.issledovanie_combo.itemData(i) == iss_id:
                            self.issledovanie_combo.setCurrentIndex(i)
                            break
            self._update_ui_state()

        elif data.get("type") == "issledovanie":
            # Клик на исследование — разворачиваем его, остальные сворачиваем
            for i in range(self.tree_model.rowCount()):
                iss_item = self.tree_model.item(i)
                iss_index = self.tree_model.indexFromItem(iss_item)
                if iss_item is item:
                    self.tree.expand(iss_index)
                else:
                    self.tree.collapse(iss_index)
            self.statusBar().showMessage(f"Выбрано исследование: {item.text()}")

    def _load_protocol(self, protocol_id: int):
        try:
            proto = get_protocol_full(protocol_id)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить протокол:\n{e}")
            return

        if not proto:
            QMessageBox.warning(self, "Ошибка", f"Протокол {protocol_id} не найден")
            return

        self._set_fio_silent(proto.get("PatsientFIO") or "")
        pol = proto.get("PatsientPol") or ""
        idx = self.pol_combo.findText(pol)
        self.pol_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.karta_edit.setText(proto.get("PatsientKarta") or "")

        grp_id = proto.get("PatsientGroupID")
        if grp_id:
            for i in range(self.gruppa_combo.count()):
                if self.gruppa_combo.itemData(i) == grp_id:
                    self.gruppa_combo.setCurrentIndex(i)
                    break

        iss_id = proto.get("IssledovanieID")
        if iss_id:
            for i in range(self.issledovanie_combo.count()):
                if self.issledovanie_combo.itemData(i) == iss_id:
                    self.issledovanie_combo.setCurrentIndex(i)
                    break

        self._set_nomer_silent(str(proto.get("Nomer") or ""))
        if proto.get("ProtocolDate"):
            d = proto["ProtocolDate"]
            self.date_edit.setDate(QDate(d.year, d.month, d.day))

        # Дата рождения — из Patsient.Date_Rozhd
        if proto.get("Date_Rozhd"):
            d = proto["Date_Rozhd"]
            self.datebirth_edit.setDate(QDate(d.year, d.month, d.day))
        else:
            self.datebirth_edit.setDate(QDate(1980, 1, 1))

        # Возраст — из Protocol.Vozrast (но пересчитаем заново)
        self.vozrast_edit.setText(str(proto.get("Vozrast") or ""))
        self.pol_shapka_edit.setText(proto.get("PatsientPol") or "")
        self.adres_edit.setText(proto.get("Adres") or "")
        self.karta_shapka_edit.setText(proto.get("PatsientKarta") or "")
        self.istor_edit.setText(str(proto.get("Istor") or ""))
        self.anamnez_edit.setText(proto.get("Anamnez") or "")

        # Направление оформлено — из Protocol.Istochnik_naprav
        self.napravlenie_edit.setText(proto.get("Istochnik_naprav") or "")

        # Модель аппарата — editable combo
        app_id = proto.get("ApparatID")
        if app_id:
            for i in range(self.apparat_combo.count()):
                if self.apparat_combo.itemData(i) == app_id:
                    self.apparat_combo.setCurrentIndex(i)
                    break
        else:
            self.apparat_combo.setCurrentText("")

        anest = proto.get("Anestezia") or ""
        self.anestezia_combo.setCurrentText(anest)

        # Пересчитаем возраст по дате рождения и дате протокола
        self._recalc_vozrast()

        self.chk_biopsia.setChecked(proto.get("Biopsia") == "да")
        self.chk_tsitologia.setChecked(proto.get("Tsitologia") == "да")
        self.chk_gistologia.setChecked(proto.get("Gistologia") == "да")
        self.chk_sanats.setChecked(proto.get("Sanats") == "да")
        self.chk_lecheb.setChecked(proto.get("Lecheb") == "да")
        self.chk_intub.setChecked(proto.get("Intybastia") == "да")
        self.chk_phmetr.setChecked(proto.get("PHMetr") == "да")
        self.chk_smiv.setChecked(proto.get("Smiv") == "да")

        self._on_biopsia_flags_changed()

        self.protocol_text.setPlainText(proto.get("ProtocolText") or "")

        self.zakl_list.clear()
        try:
            for z in get_zakluchenia(protocol_id):
                item = QListWidgetItem(z["ZakluchenieText"] or "")
                item.setData(Qt.ItemDataRole.UserRole, z)
                self.zakl_list.addItem(item)
        except Exception:
            pass

        self.vrach_list.clear()
        try:
            for v in get_vrachi(protocol_id):
                item = QListWidgetItem(v.get("VrachFIO") or "")
                item.setData(Qt.ItemDataRole.UserRole, v)
                self.vrach_list.addItem(item)
        except Exception:
            pass

        self.current_patient_id = proto.get("PatsientID")

        # Загружаем направление, если есть
        self._load_napravlenie(protocol_id)

    # =========================================================
    #  Направление (патологогистология)
    # =========================================================
    def _load_napravlenie(self, protocol_id: int):
        """Загружает первую запись Napravlenie для протокола (если есть)."""
        self.current_napravlenie_id = None
        # Очистка полей
        for w in [self.patolog_nomer, self.patolog_result_nomer,
                  self.patolog_biopsia_diag, self.patolog_biopsia_sroch,
                  self.patolog_oper_material, self.patolog_kusochki1,
                  self.patolog_kusochki2, self.patolog_methodika,
                  self.patolog_kod, self.patolog_patologoanatom,
                  self.patolog_laborant]:
            w.clear()
        self.patolog_opisanie.clear()
        self.patolog_zakluchenie.clear()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT TOP 1 * FROM Napravlenie WHERE ProtocolID = ? ORDER BY NapravlenieID",
                    [protocol_id]
                )
                row = cur.fetchone()
                if not row:
                    return
                cols = [d[0] for d in cur.description]
                data = dict(zip(cols, row))
        except Exception:
            return

        self.current_napravlenie_id = data.get("NapravlenieID")
        self.patolog_nomer.setText(str(data.get("NapravlenieID") or ""))
        self.patolog_result_nomer.setText(str(data.get("ResultNomer") or ""))
        if data.get("InDate"):
            d = data["InDate"]
            self.patolog_date_in.setDate(QDate(d.year, d.month, d.day))
        self.patolog_biopsia_diag.setText(str(data.get("BiopsiaDiadnostich") or ""))
        self.patolog_biopsia_sroch.setText(str(data.get("BiopsiaSrochnaya") or ""))
        self.patolog_oper_material.setText(str(data.get("OperatsMaterial") or ""))
        self.patolog_kusochki1.setText(str(data.get("Kusochki1") or ""))
        self.patolog_kusochki2.setText(str(data.get("Kusochki2") or ""))
        self.patolog_methodika.setText(str(data.get("Methodika") or ""))
        self.patolog_opisanie.setPlainText(str(data.get("ResultText") or ""))
        self.patolog_zakluchenie.setPlainText(str(data.get("ZakluchenieText") or ""))
        self.patolog_kod.setText(str(data.get("Kod") or ""))
        if data.get("ResultDate"):
            d = data["ResultDate"]
            self.patolog_date_result.setDate(QDate(d.year, d.month, d.day))
        self.patolog_patologoanatom.setText(str(data.get("FIOPatolAnatom") or ""))
        self.patolog_laborant.setText(str(data.get("FIOLaborant") or ""))

    def _save_napravlenie(self, protocol_id: int, issledovanie_id: int):
        """Сохраняет (обновляет или создаёт) направление."""
        # Если вкладка не видима и полей нет — не трогаем
        if not self.tabs.isTabVisible(self.patolog_tab_index):
            return
        if (not self.patolog_result_nomer.text().strip() and
                not self.patolog_opisanie.toPlainText().strip() and
                not self.patolog_zakluchenie.toPlainText().strip() and
                not self.patolog_kod.text().strip()):
            return

        d_in = self.patolog_date_in.date()
        date_in = datetime.datetime(d_in.year(), d_in.month(), d_in.day())
        t_in = self.patolog_time_in.time()
        time_in = datetime.datetime(1899, 12, 30, t_in.hour(), t_in.minute(), t_in.second())

        d_res = self.patolog_date_result.date()
        date_res = datetime.datetime(d_res.year(), d_res.month(), d_res.day())

        data = {
            "ProtocolID": protocol_id,
            "ResultNomer": self.patolog_result_nomer.text().strip(),
            "InDate": date_in,
            "InTime": time_in,
            "ResultDate": date_res,
            "ResultText": self.patolog_opisanie.toPlainText(),
            "ZakluchenieText": self.patolog_zakluchenie.toPlainText(),
            "BiopsiaDiadnostich": self.patolog_biopsia_diag.text().strip(),
            "BiopsiaSrochnaya": self.patolog_biopsia_sroch.text().strip(),
            "OperatsMaterial": self.patolog_oper_material.text().strip(),
            "Kusochki1": self.patolog_kusochki1.text().strip(),
            "Kusochki2": self.patolog_kusochki2.text().strip(),
            "Methodika": self.patolog_methodika.text().strip(),
            "Kod": self.patolog_kod.text().strip(),
            "FIOPatolAnatom": self.patolog_patologoanatom.text().strip(),
            "FIOLaborant": self.patolog_laborant.text().strip(),
            "OrganizatsiaID": 1,
            "IssledovanieID": issledovanie_id,
        }

        with get_connection() as conn:
            cur = conn.cursor()
            if self.current_napravlenie_id:
                cur.execute("""
                    UPDATE Napravlenie SET
                        ResultNomer=?, InDate=?, InTime=?, ResultDate=?, ResultText=?,
                        ZakluchenieText=?, BiopsiaDiadnostich=?, BiopsiaSrochnaya=?,
                        OperatsMaterial=?, Kusochki1=?, Kusochki2=?, Methodika=?,
                        Kod=?, FIOPatolAnatom=?, FIOLaborant=?, IssledovanieID=?
                    WHERE NapravlenieID=?
                """, [
                    data["ResultNomer"], data["InDate"], data["InTime"], data["ResultDate"],
                    data["ResultText"], data["ZakluchenieText"], data["BiopsiaDiadnostich"],
                    data["BiopsiaSrochnaya"], data["OperatsMaterial"], data["Kusochki1"],
                    data["Kusochki2"], data["Methodika"], data["Kod"], data["FIOPatolAnatom"],
                    data["FIOLaborant"], data["IssledovanieID"], self.current_napravlenie_id
                ])
            else:
                cur.execute("""
                    INSERT INTO Napravlenie
                        (NapravlenieTypeID, ProtocolID, ResultNomer, InDate, InTime,
                         ResultDate, ResultText, ZakluchenieText, BiopsiaDiadnostich,
                         BiopsiaSrochnaya, OperatsMaterial, Kusochki1, Kusochki2,
                         Methodika, Kod, FIOPatolAnatom, FIOLaborant,
                         OrganizatsiaID, IssledovanieID)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    0, protocol_id, data["ResultNomer"], data["InDate"], data["InTime"],
                    data["ResultDate"], data["ResultText"], data["ZakluchenieText"],
                    data["BiopsiaDiadnostich"], data["BiopsiaSrochnaya"],
                    data["OperatsMaterial"], data["Kusochki1"], data["Kusochki2"],
                    data["Methodika"], data["Kod"], data["FIOPatolAnatom"],
                    data["FIOLaborant"], data["OrganizatsiaID"], data["IssledovanieID"]
                ])
                cur.execute("SELECT @@IDENTITY")
                self.current_napravlenie_id = int(cur.fetchone()[0])
            conn.commit()

    # =========================================================
    #  Состояние UI
    # =========================================================
    def _update_ui_state(self):
        has_fio = bool(self.fio_edit.text().strip())
        has_protocol = self.current_protocol_id is not None

        self.btn_save_proto.setEnabled(has_fio)
        self.btn_add_proto.setEnabled(has_fio)
        self.btn_del_proto.setEnabled(has_protocol)

    # =========================================================
    #  Вспомогательные методы
    # =========================================================
    @staticmethod
    def _checkbox_to_str(chk) -> str:
        return "да" if chk.isChecked() else "нет"

    def _current_patsient_data(self) -> dict:
        grp_id = self.gruppa_combo.currentData()
        d = self.datebirth_edit.date()
        date_birth = datetime.datetime(d.year(), d.month(), d.day())
        return {
            "FIO": self.fio_edit.text().strip(),
            "Pol": self.pol_combo.currentText(),
            "Karta": self.karta_edit.text().strip() or "нет",
            "PatsientGroupID": grp_id if grp_id else 1,
            "OrganizatsiaID": 1,
            "Date_Rozhd": date_birth,
        }

    def _current_protocol_data(self, patsient_id: int) -> dict:
        iss_id = self.issledovanie_combo.currentData()
        app_id = self.apparat_combo.currentData()
        otd_id = None   # поле удалено

        d = self.date_edit.date()
        protocol_date = datetime.datetime(d.year(), d.month(), d.day())

        return {
            "PatsientID": patsient_id,
            "Vozrast": int(self.vozrast_edit.text()) if self.vozrast_edit.text().isdigit() else None,
            "OrganizatsiaID": 1,
            "Nomer": self.nomer_edit.text().strip(),
            "ProtocolDate": protocol_date,
            "Anestezia": self.anestezia_combo.currentText().strip(),
            "ProtocolText": self.protocol_text.toPlainText(),
            "Diagnos": "",
            "OtdelenieID": otd_id,
            "Adres": self.adres_edit.text().strip(),
            "Istor": self.istor_edit.text().strip(),
            "ApparatID": app_id,
            "Otdelenie": "",
            "Istochnik_naprav": self.napravlenie_edit.text().strip(),
            "Anamnez": self.anamnez_edit.text().strip(),
            "Biopsia": self._checkbox_to_str(self.chk_biopsia),
            "IssledovanieID": iss_id,
            "Year": protocol_date.year,
            "Tsitologia": self._checkbox_to_str(self.chk_tsitologia),
            "Gistologia": self._checkbox_to_str(self.chk_gistologia),
            "Lecheb": self._checkbox_to_str(self.chk_lecheb),
            "Sanats": self._checkbox_to_str(self.chk_sanats),
            "Intybastia": self._checkbox_to_str(self.chk_intub),
            "PHMetr": self._checkbox_to_str(self.chk_phmetr),
            "Smiv": self._checkbox_to_str(self.chk_smiv),
            "State": 1,
        }

    def _find_or_create_patsient(self, fio: str) -> int:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT PatsientID FROM Patsient WHERE FIO = ?", [fio])
            row = cur.fetchone()
            if row:
                return int(row[0])
        return insert_patsient(self._current_patsient_data())

    # =========================================================
    #  Сохранение / добавление / удаление
    # =========================================================
    def on_save_changes(self):
        fio = self.fio_edit.text().strip()
        if not fio:
            QMessageBox.warning(self, "Нет ФИО", "Введите Ф.И.О. пациента.")
            return

        # Случай A: протокол выбран → обновляем
        if self.current_protocol_id:
            try:
                patsient_id = self._find_or_create_patsient(fio)
                self.current_patient_id = patsient_id
                update_patsient(patsient_id, self._current_patsient_data())
                update_protocol(self.current_protocol_id,
                                self._current_protocol_data(patsient_id))

                iss_id = self.issledovanie_combo.currentData()
                self._save_zakluchenia(self.current_protocol_id, iss_id)
                self._save_vrachi(self.current_protocol_id, iss_id)
                self._save_napravlenie(self.current_protocol_id, iss_id)

                QMessageBox.information(self, "Сохранено", "Изменения сохранены.")
                self._reload_tree()
                self._select_protocol_in_tree(self.current_protocol_id)
                self._update_ui_state()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
            return

        # Случай B/C: протокол не выбран → спрашиваем
        ans = QMessageBox.question(
            self, "Сохранить протокол?",
            "Протокол не выбран. Сохранить введённые данные как новый протокол?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.on_add_protocol()

    def on_add_protocol(self):
        fio = self.fio_edit.text().strip()
        if not fio:
            QMessageBox.warning(self, "Нет ФИО", "Введите Ф.И.О. пациента.")
            return

        iss_id = self.issledovanie_combo.currentData()
        if not iss_id:
            QMessageBox.warning(self, "Нет исследования", "Выберите исследование.")
            return

        try:
            patsient_id = self._find_or_create_patsient(fio)
            self.current_patient_id = patsient_id

            nomer = str(get_next_protocol_nomer(iss_id))
            self._set_nomer_silent(nomer)

            data = self._current_protocol_data(patsient_id)
            new_id = insert_protocol(data)
            self.current_protocol_id = new_id

            # Сохраняем врачей, заключения, направление
            self._save_zakluchenia(new_id, iss_id)
            self._save_vrachi(new_id, iss_id)
            self._save_napravlenie(new_id, iss_id)

            QMessageBox.information(self, "Создан", f"Протокол № {nomer} создан.")
            self._reload_tree()
            self._select_protocol_in_tree(new_id)
            self._update_ui_state()
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать протокол:\n{e}")

    def on_delete_protocol(self):
        if not self.current_protocol_id:
            QMessageBox.warning(self, "Нет протокола", "Сначала выберите протокол.")
            return
        ans = QMessageBox.question(
            self, "Удалить протокол",
            "Удалить выбранный протокол со всеми врачами, заключениями и направлениями?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM Napravlenie WHERE ProtocolID = ?", [self.current_protocol_id])
                conn.commit()
            delete_protocol(self.current_protocol_id)
            self.current_protocol_id = None
            QMessageBox.information(self, "Удалено", "Протокол удалён.")
            self._reload_tree()
            self._update_ui_state()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n{e}")

    # =========================================================
    #  Заключения
    # =========================================================
    def on_zakl_add(self):
        if not self.current_protocol_id:
            QMessageBox.warning(self, "Нет протокола", "Сначала сохраните протокол.")
            return

        text = self.zakl_combo.currentText().strip()
        if not text:
            QMessageBox.warning(self, "Пусто", "Выберите или введите текст заключения.")
            return

        order = self.zakl_list.count() + 1
        iss_id = self.issledovanie_combo.currentData()
        try:
            insert_zakluchenie(self.current_protocol_id, text, order, iss_id)
            self._reload_zakluchenia()
            self.zakl_combo.setCurrentText("")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось добавить:\n{e}")

    def on_zakl_edit(self):
        item = self.zakl_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Ничего не выбрано", "Выберите заключение в списке.")
            return
        z = item.data(Qt.ItemDataRole.UserRole)
        texts = get_distinct_zakluchenia()
        text, ok = QInputDialog.getItem(
            self, "Изменить заключение", "Текст:", texts, 0, True
        )
        if not ok or not text.strip():
            return
        try:
            update_zakluchenie(z["ZakluchenieID"], text.strip(), z["ZakluchenieOrder"])
            self._reload_zakluchenia()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось изменить:\n{e}")

    def on_zakl_del(self):
        item = self.zakl_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Ничего не выбрано", "Выберите заключение в списке.")
            return
        z = item.data(Qt.ItemDataRole.UserRole)
        ans = QMessageBox.question(
            self, "Удалить заключение",
            f"Удалить:\n{z['ZakluchenieText']}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_zakluchenie(z["ZakluchenieID"])
            self._reload_zakluchenia()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n{e}")

    def _reload_zakluchenia(self):
        if not self.current_protocol_id:
            return
        self.zakl_list.clear()
        for z in get_zakluchenia(self.current_protocol_id):
            item = QListWidgetItem(z["ZakluchenieText"] or "")
            item.setData(Qt.ItemDataRole.UserRole, z)
            self.zakl_list.addItem(item)

    def _save_zakluchenia(self, protocol_id: int, issledovanie_id: int):
        for i in range(self.zakl_list.count()):
            item = self.zakl_list.item(i)
            text = item.text()
            z = item.data(Qt.ItemDataRole.UserRole)
            if z and z.get("ZakluchenieID"):
                continue
            insert_zakluchenie(protocol_id, text, i + 1, issledovanie_id)

    # =========================================================
    #  Врачи
    # =========================================================
    def on_vrach_add(self):
        if not self.current_protocol_id:
            QMessageBox.warning(self, "Нет протокола", "Сначала сохраните протокол.")
            return

        fio = self.vrach_combo.currentText().strip()
        if not fio:
            QMessageBox.warning(self, "Пусто", "Введите ФИО врача.")
            return

        iss_id = self.issledovanie_combo.currentData()
        try:
            vrach_type_id = insert_vrach_type_if_missing(fio)
            order = self.vrach_list.count() + 1
            insert_vrach(self.current_protocol_id, vrach_type_id, order, iss_id)
            self._reload_vrachi()
            self._load_references()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось добавить:\n{e}")

    def on_vrach_edit(self):
        item = self.vrach_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Ничего не выбрано", "Выберите врача в списке.")
            return
        v = item.data(Qt.ItemDataRole.UserRole)
        fio, ok = QInputDialog.getText(
            self, "Изменить врача", "ФИО:", text=v.get("VrachFIO", "")
        )
        if not ok or not fio.strip():
            return
        try:
            update_vrach_type(v["VrachTypeID"], fio.strip())
            self._reload_vrachi()
            self._load_references()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось изменить:\n{e}")

    def on_vrach_del(self):
        item = self.vrach_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Ничего не выбрано", "Выберите врача в списке.")
            return
        v = item.data(Qt.ItemDataRole.UserRole)
        ans = QMessageBox.question(
            self, "Удалить врача",
            f"Убрать врача из протокола:\n{v.get('VrachFIO', '')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_vrach(v["VrachID"])
            self._reload_vrachi()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n{e}")

    def _reload_vrachi(self):
        if not self.current_protocol_id:
            return
        self.vrach_list.clear()
        for v in get_vrachi(self.current_protocol_id):
            item = QListWidgetItem(v.get("VrachFIO") or "")
            item.setData(Qt.ItemDataRole.UserRole, v)
            self.vrach_list.addItem(item)

    def _save_vrachi(self, protocol_id: int, issledovanie_id: int):
        for i in range(self.vrach_list.count()):
            item = self.vrach_list.item(i)
            fio = item.text()
            v = item.data(Qt.ItemDataRole.UserRole)
            if v and v.get("VrachID"):
                continue
            vrach_type_id = insert_vrach_type_if_missing(fio)
            insert_vrach(protocol_id, vrach_type_id, i + 1, issledovanie_id)

    # =========================================================
    #  Заглушки
    # =========================================================
    def on_settings(self):
        try:
            from ui.settings_dialog import SettingsDialog
            dlg = SettingsDialog(self)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть настройки:\n{e}")

    def on_find_patient(self):
        QMessageBox.information(self, "Поиск", "Сложный поиск (в разработке)")

    def on_report(self):
        """Диалог выбора отчёта и периода, запуск."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
        from PyQt6.QtCore import QDate
        from reports import run_report
        from db import get_setting, set_setting

        dlg = QDialog(self)
        dlg.setWindowTitle("Настройки отчёта")
        dlg.resize(400, 260)

        layout = QVBoxLayout(dlg)

        # Форма отчёта
        form_group = QGroupBox("Выберите форму отчёта")
        form_layout = QVBoxLayout(form_group)
        rb_nagruzka = QRadioButton("Нагрузка")
        rb_gistologia = QRadioButton("Гистология")
        form_layout.addWidget(rb_nagruzka)
        form_layout.addWidget(rb_gistologia)
        layout.addWidget(form_group)

        # Периодичность
        period_group = QGroupBox("Выберите периодичность")
        period_layout = QVBoxLayout(period_group)
        rb_month = QRadioButton("Месячный")
        rb_year = QRadioButton("Годовой")
        period_layout.addWidget(rb_month)
        period_layout.addWidget(rb_year)
        layout.addWidget(period_group)

        # Год / месяц
        date_form = QFormLayout()
        year_spin = QSpinBox()
        year_spin.setRange(2000, 2100)
        year_spin.setValue(QDate.currentDate().year())
        month_combo = QComboBox()
        month_combo.addItems([
            "январь", "февраль", "март", "апрель", "май", "июнь",
            "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"
        ])
        month_combo.setCurrentIndex(QDate.currentDate().month() - 1)
        date_form.addRow("Отчётный год", year_spin)
        date_form.addRow("Отчётный месяц", month_combo)
        layout.addLayout(date_form)

        # Загружаем сохранённые настройки
        try:
            saved_form = get_setting("report_form", "nagruzka")
            saved_period = get_setting("report_period", "month")
            if saved_form == "gistologia":
                rb_gistologia.setChecked(True)
            else:
                rb_nagruzka.setChecked(True)
            if saved_period == "year":
                rb_year.setChecked(True)
            else:
                rb_month.setChecked(True)
        except Exception:
            rb_nagruzka.setChecked(True)
            rb_month.setChecked(True)

        # Кнопки
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Сохраняем настройки
        form = "gistologia" if rb_gistologia.isChecked() else "nagruzka"
        period = "year" if rb_year.isChecked() else "month"
        try:
            set_setting("report_form", form)
            set_setting("report_period", period)
        except Exception:
            pass

        year = year_spin.value()
        month = month_combo.currentIndex() + 1 if period == "month" else None

        try:
            path = run_report(form, year, month)
            self.statusBar().showMessage(f"Отчёт сохранён: {path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сформировать отчёт:\n{e}")

    def on_snapshots(self):
        QMessageBox.information(self, "Снимки", "Модуль снимков (в разработке)")

    def on_check(self):
        QMessageBox.information(self, "Проверка", "Проверка протоколов (в разработке)")

    def on_print(self):
        if not self.current_protocol_id:
            QMessageBox.warning(self, "Нет протокола", "Выберите протокол в дереве.")
            return
        try:
            from print_protocol import print_protocol
            path = print_protocol(self.current_protocol_id)
            self.statusBar().showMessage(f"Протокол открыт: {path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка печати", f"Не удалось сформировать протокол:\n{e}")

    def on_templates(self):
        QMessageBox.information(self, "Шаблоны", "Список шаблонов (в разработке)")

    def on_templates_edit(self):
        QMessageBox.information(self, "Редактор шаблонов", "Редактор шаблонов (в разработке)")


def run():
    app = QApplication(sys.argv)

    # Глобальный перехват исключений
    def excepthook(exc_type, exc_value, exc_tb):
        import traceback
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print("[FATAL]", tb)
        try:
            QMessageBox.critical(None, "Критическая ошибка", tb)
        except Exception:
            pass

    sys.excepthook = excepthook

    w = MainWindow()
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()