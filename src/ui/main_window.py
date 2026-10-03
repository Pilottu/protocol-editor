import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTreeView, QTabWidget, QFormLayout, QLineEdit,
    QComboBox, QDateEdit, QTextEdit, QPushButton, QLabel,
    QToolBar, QMessageBox, QListWidget, QListWidgetItem,
    QGroupBox, QCheckBox
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction
from PyQt6.QtCore import Qt, QDate

sys.path.insert(0, r"E:\Prog\Piton\src")
from db import (
    get_references,
    get_tree_data,
    get_protocol_full,
    get_zakluchenia,
    get_vrachi,
    get_vrach_types,
    get_anestezia_types,
    reload_config,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Редактор протоколов (Python)")
        self.resize(1600, 900)

        # --- Данные ---
        self.references = {}
        self.current_protocol_id = None
        self.current_patient_id = None
        self.is_connected = False

        # --- Панель инструментов ---
        self._build_toolbar()

        # --- Центральный виджет ---
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

        # Верхняя часть
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

        # Кнопки управления
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Сохранить изменения")
        self.btn_add = QPushButton("Добавить")
        self.btn_new_protocol = QPushButton("Новый протокол")
        self.btn_find = QPushButton("Найти")
        self.btn_delete = QPushButton("Удалить")
        for b in [self.btn_save, self.btn_add, self.btn_new_protocol,
                  self.btn_find, self.btn_delete]:
            btn_layout.addWidget(b)
        right_layout.addLayout(btn_layout)

        # Вкладки Шапка / Текст
        self.tabs = QTabWidget()

        # --- Вкладка "Шапка" ---
        shapka_widget = QWidget()
        shapka_layout = QFormLayout(shapka_widget)

        self.organizatsia_edit = QLineEdit()
        self.otdelenie_edit = QLineEdit()
        self.issledovanie_edit = QLineEdit()
        self.nomer_edit = QLineEdit()
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.fio_shapka_edit = QLineEdit()
        self.vozrast_edit = QLineEdit()
        self.pol_shapka_edit = QLineEdit()
        self.adres_edit = QLineEdit()
        self.karta_shapka_edit = QLineEdit()
        self.istor_edit = QLineEdit()
        self.otdelenie_podr_combo = QComboBox()
        self.anamnez_edit = QLineEdit()
        self.apparat_combo = QComboBox()
        self.anestezia_combo = QComboBox()

        shapka_layout.addRow("Организация", self.organizatsia_edit)
        shapka_layout.addRow("Отделение организации", self.otdelenie_edit)
        shapka_layout.addRow("Исследование", self.issledovanie_edit)
        shapka_layout.addRow("Протокол №", self.nomer_edit)
        shapka_layout.addRow("Дата", self.date_edit)
        shapka_layout.addRow("Ф.И.О.", self.fio_shapka_edit)
        shapka_layout.addRow("Возраст", self.vozrast_edit)
        shapka_layout.addRow("Пол", self.pol_shapka_edit)
        shapka_layout.addRow("Адрес", self.adres_edit)
        shapka_layout.addRow("Амб. карта №", self.karta_shapka_edit)
        shapka_layout.addRow("История болезни №", self.istor_edit)
        shapka_layout.addRow("Отделение (подраздел.)", self.otdelenie_podr_combo)
        shapka_layout.addRow("Анамнез", self.anamnez_edit)
        shapka_layout.addRow("Модель аппарата", self.apparat_combo)
        shapka_layout.addRow("Анестезия", self.anestezia_combo)

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
        shapka_layout.addRow("Флаги", flags_layout)

        self.tabs.addTab(shapka_widget, "Шапка")

        # --- Вкладка "Текст" ---
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)

        self.protocol_text = QTextEdit()
        self.protocol_text.setPlaceholderText("Текст протокола...")
        text_layout.addWidget(QLabel("Текст протокола:"))
        text_layout.addWidget(self.protocol_text, stretch=3)

        zakl_group = QGroupBox("Заключение")
        zakl_layout = QHBoxLayout(zakl_group)
        self.zakl_list = QListWidget()
        zakl_btn_layout = QVBoxLayout()
        self.btn_zakl_add = QPushButton("Добавить")
        self.btn_zakl_edit = QPushButton("Изменить")
        self.btn_zakl_del = QPushButton("Удалить")
        for b in [self.btn_zakl_add, self.btn_zakl_edit, self.btn_zakl_del]:
            zakl_btn_layout.addWidget(b)
        zakl_btn_layout.addStretch()
        zakl_layout.addWidget(self.zakl_list, stretch=1)
        zakl_layout.addLayout(zakl_btn_layout)
        text_layout.addWidget(zakl_group, stretch=1)

        vrach_group = QGroupBox("Врач")
        vrach_layout = QHBoxLayout(vrach_group)
        self.vrach_list = QListWidget()
        vrach_btn_layout = QVBoxLayout()
        self.vrach_combo = QComboBox()
        self.btn_vrach_add = QPushButton("Добавить")
        self.btn_vrach_edit = QPushButton("Изменить")
        self.btn_vrach_del = QPushButton("Удалить")
        vrach_btn_layout.addWidget(self.vrach_combo)
        for b in [self.btn_vrach_add, self.btn_vrach_edit, self.btn_vrach_del]:
            vrach_btn_layout.addWidget(b)
        vrach_btn_layout.addStretch()
        vrach_layout.addWidget(self.vrach_list, stretch=1)
        vrach_layout.addLayout(vrach_btn_layout)
        text_layout.addWidget(vrach_group, stretch=1)

        proto_btn_layout = QHBoxLayout()
        self.btn_save_proto = QPushButton("Сохранить изменения")
        self.btn_add_proto = QPushButton("Добавить протокол")
        self.btn_del_proto = QPushButton("Удалить протокол")
        for b in [self.btn_save_proto, self.btn_add_proto, self.btn_del_proto]:
            proto_btn_layout.addWidget(b)
        text_layout.addLayout(proto_btn_layout)

        self.tabs.addTab(text_widget, "Текст")

        right_layout.addWidget(self.tabs, stretch=1)
        splitter.addWidget(right_panel)

        splitter.setSizes([500, 1100])
        layout.addWidget(splitter)

        # --- Статус-бар ---
        self.status_label = QLabel("Проверка соединения...")
        self.statusBar().addPermanentWidget(self.status_label)

        # --- Пробуем подключиться к базе (не критично для запуска) ---
        self._try_connect()

    # --- Панель инструментов ---
    def _build_toolbar(self):
        tb = QToolBar("Основная")
        self.addToolBar(tb)

        actions = [
            ("Настройки", self.on_settings),
            ("Переподключиться", self.on_reconnect),
            ("Отчёт", self.on_report),
            ("Снимки", self.on_snapshots),
            ("Проверить", self.on_check),
            ("Печать", self.on_print),
            ("Шаблоны", self.on_templates),
            ("Редактор шаблонов", self.on_templates_edit),
        ]
        for name, handler in actions:
            act = QAction(name, self)
            act.triggered.connect(handler)
            tb.addAction(act)

    # --- Подключение ---
    def _try_connect(self):
        """Пытается подключиться к базе. Если не удаётся — программа продолжает работать."""
        try:
            self.references = get_references()
            self.is_connected = True
            self.status_label.setText("✅ Подключено к базе")
            self.status_label.setStyleSheet("color: green;")
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
                f"Откройте «Настройки» → «База данных», "
                f"проверьте параметры и сохраните."
            )

    def _clear_ui(self):
        """Очищает UI, когда нет соединения."""
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["Исследование / Пациент / Протокол"])
        self.issledovanie_combo.clear()
        self.gruppa_combo.clear()
        self.apparat_combo.clear()
        self.otdelenie_podr_combo.clear()
        self.vrach_combo.clear()
        self.anestezia_combo.clear()
        self.zakl_list.clear()
        self.vrach_list.clear()
        self.protocol_text.clear()

    def on_reconnect(self):
        """Кнопка «Переподключиться» на панели инструментов."""
        reload_config()
        self._try_connect()

    # --- Загрузка справочников ---
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

        self.otdelenie_podr_combo.clear()
        self.otdelenie_podr_combo.addItem("", None)
        for id_, name in refs.get("Otdelenie", []):
            self.otdelenie_podr_combo.addItem(name, id_)

        self.vrach_combo.clear()
        self.vrach_combo.addItem("", None)
        try:
            for id_, fio in get_vrach_types():
                self.vrach_combo.addItem(fio, id_)
        except Exception:
            pass

        self.anestezia_combo.clear()
        self.anestezia_combo.addItem("", None)
        try:
            for name in get_anestezia_types():
                self.anestezia_combo.addItem(name, name)
        except Exception:
            pass

    # --- Загрузка дерева ---
    def _load_tree(self):
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["Исследование / Пациент / Протокол"])

        try:
            data = get_tree_data()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить дерево:\n{e}")
            return

        for iss in data:
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

        self.tree.expandAll()
        self.status_label.setText(
            f"✅ Подключено. Исследований: {self.tree_model.rowCount()}"
        )
        self.status_label.setStyleSheet("color: green;")

    # --- Обработчики ---
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
            self.current_protocol_id = data["id"]
            self.statusBar().showMessage(f"Выбран протокол: {item.text()}")
            self._load_protocol(data["id"])
        elif data.get("type") == "patsient":
            self.statusBar().showMessage(f"Выбран пациент: {item.text()}")
        elif data.get("type") == "issledovanie":
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

        # Верхняя часть
        self.fio_edit.setText(proto.get("PatsientFIO") or "")
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

        # Шапка
        self.organizatsia_edit.setText(proto.get("OrganizatsiaName") or "")
        self.otdelenie_edit.setText(proto.get("OtdelenieName") or "")
        self.issledovanie_edit.setText(proto.get("IssledovanieName") or "")
        self.nomer_edit.setText(str(proto.get("Nomer") or ""))
        if proto.get("ProtocolDate"):
            d = proto["ProtocolDate"]
            self.date_edit.setDate(QDate(d.year, d.month, d.day))
        self.fio_shapka_edit.setText(proto.get("PatsientFIO") or "")
        self.vozrast_edit.setText(str(proto.get("Vozrast") or ""))
        self.pol_shapka_edit.setText(proto.get("PatsientPol") or "")
        self.adres_edit.setText(proto.get("Adres") or "")
        self.karta_shapka_edit.setText(proto.get("PatsientKarta") or "")
        self.istor_edit.setText(str(proto.get("Istor") or ""))
        self.anamnez_edit.setText(proto.get("Anamnez") or "")

        app_id = proto.get("ApparatID")
        if app_id:
            for i in range(self.apparat_combo.count()):
                if self.apparat_combo.itemData(i) == app_id:
                    self.apparat_combo.setCurrentIndex(i)
                    break

        anest = proto.get("Anestezia") or ""
        idx = self.anestezia_combo.findText(anest)
        self.anestezia_combo.setCurrentIndex(idx if idx >= 0 else 0)

        otd_id = proto.get("OtdelenieID")
        if otd_id:
            for i in range(self.otdelenie_podr_combo.count()):
                if self.otdelenie_podr_combo.itemData(i) == otd_id:
                    self.otdelenie_podr_combo.setCurrentIndex(i)
                    break

        # Флаги
        self.chk_biopsia.setChecked(proto.get("Biopsia") == "да")
        self.chk_tsitologia.setChecked(proto.get("Tsitologia") == "да")
        self.chk_gistologia.setChecked(proto.get("Gistologia") == "да")
        self.chk_sanats.setChecked(proto.get("Sanats") == "да")
        self.chk_lecheb.setChecked(proto.get("Lecheb") == "да")
        self.chk_intub.setChecked(proto.get("Intybastia") == "да")
        self.chk_phmetr.setChecked(proto.get("PHMetr") == "да")
        self.chk_smiv.setChecked(proto.get("Smiv") == "да")

        # Текст
        self.protocol_text.setPlainText(proto.get("ProtocolText") or "")

        # Заключения
        self.zakl_list.clear()
        try:
            for z in get_zakluchenia(protocol_id):
                item = QListWidgetItem(z["ZakluchenieText"] or "")
                item.setData(Qt.ItemDataRole.UserRole, z)
                self.zakl_list.addItem(item)
        except Exception:
            pass

        # Врачи
        self.vrach_list.clear()
        try:
            for v in get_vrachi(protocol_id):
                item = QListWidgetItem(v.get("VrachFIO") or "")
                item.setData(Qt.ItemDataRole.UserRole, v)
                self.vrach_list.addItem(item)
        except Exception:
            pass

    # --- Кнопки панели инструментов ---
    def on_settings(self):
        try:
            from ui.settings_dialog import SettingsDialog
            dlg = SettingsDialog(self)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть настройки:\n{e}")

    def on_report(self):
        QMessageBox.information(self, "Отчёт", "Формирование отчёта (в разработке)")

    def on_snapshots(self):
        QMessageBox.information(self, "Снимки", "Модуль снимков (в разработке)")

    def on_check(self):
        QMessageBox.information(self, "Проверка", "Проверка протоколов (в разработке)")

    def on_print(self):
        QMessageBox.information(self, "Печать", "Печать протокола (в разработке)")

    def on_templates(self):
        QMessageBox.information(self, "Шаблоны", "Список шаблонов (в разработке)")

    def on_templates_edit(self):
        QMessageBox.information(self, "Редактор шаблонов", "Редактор шаблонов (в разработке)")


def run():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()