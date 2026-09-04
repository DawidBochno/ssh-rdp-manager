"""Szkielet aplikacji desktopowej do zarządzania połączeniami SSH/RDP.

Lewa strona: drzewo katalogów z grupami i połączeniami.
Prawa strona: zakładki, jedna na każde otwarte połączenie.
"""
import base64
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabBar,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

import i18n
from i18n import t
from rdp import RDP_PORT, open_rdp
from servers import SERVERS
from ssh_terminal import (
    SCRIPTS,
    SessionTab,
    TerminalHighlighter,
    connect_with_progress,
    run_script,
    wait_for_pending,
)

CONNECTION_TYPE = QTreeWidgetItem.UserType + 1
CONNECTION_DATA = Qt.UserRole + 1
COLOR_DATA = Qt.UserRole + 3
GROUP_ICON = "📁"

# Zapisujemy obok skryptu; .gitignore trzyma ten plik poza repozytorium.
# Hasła NIE trafiają tutaj — celowo, plik jest zwykłym tekstem.
CONFIG_FILE = Path(__file__).with_name("connections.json")

# Ikona (emoji) jako sposób odróżnienia elementów; trzymana osobno od nazwy.
ICON_DATA = Qt.UserRole + 2
ICONS = ["📁", "🗂️", "🖥️", "🐧", "🪟",
         "🌐", "🗄️", "🔒", "⭐", "🔥",
         "🧪", "⚙️"]

# Hasła szyfrujemy DPAPI: klucz jest przypisany do konta Windows,
# więc plik skopiowany na inny komputer jest bezużyteczny.
CAN_STORE_PASSWORDS = sys.platform == "win32"

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(func, data):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI refused the operation")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt_password(text):
    """Szyfruje hasło dla bieżącego konta Windows; zwraca tekst do JSON-a."""
    blob = _dpapi(ctypes.windll.crypt32.CryptProtectData, text.encode("utf-8"))
    return base64.b64encode(blob).decode("ascii")


def decrypt_password(stored):
    """Odwrotność `encrypt_password`. Cudze lub uszkodzone dane = None."""
    try:
        blob = _dpapi(ctypes.windll.crypt32.CryptUnprotectData, base64.b64decode(stored))
    except (OSError, ValueError):
        return None
    return blob.decode("utf-8")


SSH_PORT = 22


class ConnectionDialog(QDialog):
    """Formularz danych połączenia — SSH albo RDP."""

    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        data = data or {}

        self.protocol = QComboBox()
        self.protocol.addItem("SSH", "ssh")
        self.protocol.addItem("RDP", "rdp")
        self.protocol.setCurrentIndex(
            max(0, self.protocol.findData(data.get("protocol", "ssh")))
        )

        self.name = QLineEdit(data.get("name", ""))
        self.host = QLineEdit(data.get("host", ""))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(data.get("port", self._default_port()))
        self.username = QLineEdit(data.get("username", ""))

        # Klucz prywatny dotyczy tylko SSH — przy RDP wiersz się chowa.
        self.key_file = QLineEdit(data.get("key_file", ""))
        browse = QPushButton(t("btn_browse"))
        browse.clicked.connect(self._pick_key_file)
        key_box = QWidget()
        key_layout = QHBoxLayout(key_box)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_file)
        key_layout.addWidget(browse)

        stored = decrypt_password(data["password"]) if data.get("password") else ""
        self.password = QLineEdit(stored or "")
        self.password.setEchoMode(QLineEdit.Password)
        self.save_password = QCheckBox(t("chk_save_password"))
        self.save_password.setChecked(bool(stored))
        self.save_password.setEnabled(CAN_STORE_PASSWORDS)
        if not CAN_STORE_PASSWORDS:
            self.save_password.setToolTip(t("tip_save_password_windows_only"))

        form = QFormLayout(self)
        form.addRow(t("fld_protocol"), self.protocol)
        form.addRow(t("fld_name"), self.name)
        form.addRow(t("fld_host"), self.host)
        form.addRow(t("fld_port"), self.port)
        form.addRow(t("fld_user"), self.username)
        form.addRow(t("fld_password"), self.password)
        self._key_row = form.rowCount()
        form.addRow(t("fld_key_file"), key_box)
        form.addRow("", self.save_password)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._form = form
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self._protocol_changed()

    # --- protokół -----------------------------------------------------------

    def _default_port(self):
        return RDP_PORT if self.protocol.currentData() == "rdp" else SSH_PORT

    def _protocol_changed(self):
        """Tytuł, domyślny port i widoczność pola klucza idą za protokołem."""
        is_rdp = self.protocol.currentData() == "rdp"
        self.setWindowTitle(t("dlg_rdp_connection") if is_rdp else t("dlg_ssh_connection"))
        # Port zmieniamy tylko wtedy, gdy stoi na domyślnym dla drugiego
        # protokołu — ręcznie wpisanego numeru nie wolno nadpisać.
        other_default = SSH_PORT if is_rdp else RDP_PORT
        if self.port.value() == other_default:
            self.port.setValue(self._default_port())
        self._form.setRowVisible(self._key_row, not is_rdp)

    def _pick_key_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t("dlg_key_file"), "", t("filter_all_files")
        )
        if path:
            self.key_file.setText(path)

    def accept(self):
        if not self.host.text().strip():
            QMessageBox.warning(self, t("err_missing_data_title"), t("err_missing_host"))
            return
        super().accept()

    def values(self):
        host = self.host.text().strip()
        protocol = self.protocol.currentData()
        data = {
            "name": self.name.text().strip() or host,
            "host": host,
            "port": self.port.value(),
            "username": self.username.text().strip(),
            "protocol": protocol,
        }
        if protocol == "ssh" and self.key_file.text().strip():
            data["key_file"] = self.key_file.text().strip()
        if self.save_password.isChecked() and self.password.text():
            data["password"] = encrypt_password(self.password.text())
        return data


class ConnectionTree(QTreeWidget):
    """Drzewo grup i połączeń z menu kontekstowym do zarządzania nimi."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(False)
        self.setHeaderLabel(t("tree_header"))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Przenoszenie elementów myszą wewnątrz drzewa.
        self.setDragDropMode(QTreeWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

        root = QTreeWidgetItem([t("tree_root")])
        self.addTopLevelItem(root)
        # Korzenia nie da się przeciągnąć — wszystko ma zostać pod nim.
        root.setFlags(root.flags() & ~Qt.ItemIsDragEnabled)
        root.setExpanded(True)
        self.load()

    # --- nazwa i ikona ------------------------------------------------------

    @staticmethod
    def item_name(item):
        """Nazwa bez doklejonej ikony — to, co trafia do pliku."""
        icon = item.data(0, ICON_DATA) or ""
        text = item.text(0)
        return text[len(icon):].strip() if icon and text.startswith(icon) else text

    @staticmethod
    def set_label(item, name, icon=""):
        item.setData(0, ICON_DATA, icon)
        item.setText(0, f"{icon} {name}" if icon else name)

    # --- zapis i odczyt drzewa ---------------------------------------------

    def _serialize(self, item):
        node = {"name": self.item_name(item)}
        if item.data(0, ICON_DATA):
            node["icon"] = item.data(0, ICON_DATA)
        if item.data(0, COLOR_DATA):
            node["color"] = item.data(0, COLOR_DATA)
        if item.type() == CONNECTION_TYPE:
            node["connection"] = item.data(0, CONNECTION_DATA)
        else:
            node["children"] = [
                self._serialize(item.child(i)) for i in range(item.childCount())
            ]
        return node

    def nodes(self):
        """Całe drzewo jako lista słowników — to samo, co ląduje w pliku."""
        root = self.topLevelItem(0)
        return [self._serialize(root.child(i)) for i in range(root.childCount())]

    def save(self):
        """Zrzuca całe drzewo do JSON. Wołane po każdej zmianie."""
        nodes = self.nodes()
        try:
            CONFIG_FILE.write_text(
                json.dumps(nodes, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as error:
            QMessageBox.warning(
                self, t("err_save_title"), t("err_save_body", error)
            )

    def _build(self, parent, node):
        name = str(node.get("name", t("unnamed")))
        default_icon = "" if "connection" in node else GROUP_ICON
        if "connection" in node:
            item = QTreeWidgetItem(parent, [], CONNECTION_TYPE)
            self._apply_connection(item, node["connection"])
        else:
            item = QTreeWidgetItem(parent, [])
            for child in node.get("children", []):
                self._build(item, child)
            item.setExpanded(True)
        self.set_label(item, name, str(node.get("icon", "") or default_icon))
        if node.get("color"):
            self.set_color(item, str(node["color"]))

    def load(self):
        if not CONFIG_FILE.exists():
            return
        try:
            nodes = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            # Uszkodzonego pliku nie nadpisujemy w ciszy — odkładamy kopię,
            # żeby dało się odzyskać wpisy ręcznie.
            backup = CONFIG_FILE.with_suffix(".json.bak")
            try:
                CONFIG_FILE.replace(backup)
            except OSError:
                backup = None
            QMessageBox.warning(
                self,
                t("err_load_title"),
                t("err_load_body", error)
                + (t("err_load_backup", backup) if backup else ""),
            )
            return

        root = self.topLevelItem(0)
        for node in nodes:
            self._build(root, node)

    # --- eksport i import ---------------------------------------------------

    def export_to(self, path):
        """Ten sam format co connections.json — plik da się wprost podmienić."""
        Path(path).write_text(
            json.dumps(self.nodes(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def import_from(self, path, replace):
        """Wczytuje drzewo z pliku: `replace` zastępuje wszystko, inaczej dopisuje.

        Zwraca liczbę wczytanych gałęzi najwyższego poziomu.
        """
        nodes = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(nodes, list):
            raise ValueError(t("err_not_a_list"))
        root = self.topLevelItem(0)
        if replace:
            root.takeChildren()
        for node in nodes:
            self._build(root, node)
        root.setExpanded(True)
        self.save()
        return len(nodes)

    def _drop_allowed(self, item, on_item):
        # Upuszczenie w pustym miejscu zrobiłoby element najwyższego poziomu,
        # obok korzenia — wtedy `save()` by go zgubił.
        # Połączenie nie jest grupą, więc nic nie może pod nie wejść.
        if item is None:
            return False
        return not (on_item and item.type() == CONNECTION_TYPE)

    def dropEvent(self, event):
        on_item = self.dropIndicatorPosition() == QTreeWidget.OnItem
        if not self._drop_allowed(self.itemAt(event.position().toPoint()), on_item):
            event.ignore()
            return
        super().dropEvent(event)
        self.save()

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        menu = QMenu(self)
        menu.addAction(t("menu_new_group"), lambda: self._add_group(item))
        menu.addAction(t("menu_new_connection"), lambda: self._add_connection(item))
        if item is not None and item is not self.topLevelItem(0):
            menu.addSeparator()
            if item.type() == CONNECTION_TYPE:
                menu.addAction(t("menu_edit_connection"), lambda: self._edit_connection(item))
            else:
                menu.addAction(t("menu_rename"), lambda: self._rename_group(item))
            menu.addAction(t("menu_icon"), lambda: self._pick_icon(item))
            menu.addAction(t("menu_color"), lambda: self._pick_color(item))
            if item.data(0, COLOR_DATA):
                menu.addAction(t("menu_no_color"), lambda: self._clear_color(item))
            menu.addSeparator()
            menu.addAction(t("menu_delete"), lambda: self._remove_item(item))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _apply_connection(self, item, data):
        """Wpisuje dane połączenia do elementu drzewa (etykieta, tooltip, dane)."""
        item.setData(0, CONNECTION_DATA, data)
        item.setToolTip(
            0,
            f"{data.get('username', '')}@{data.get('host', '')}:{data.get('port', 22)}"
            + (t("tip_password_saved") if data.get("password") else ""),
        )
        self.set_label(item, data["name"], item.data(0, ICON_DATA) or "")

    def _edit_connection(self, item):
        dialog = ConnectionDialog(self, item.data(0, CONNECTION_DATA))
        if dialog.exec() != QDialog.Accepted:
            return
        self._apply_connection(item, dialog.values())
        self.save()

    def _rename_group(self, item):
        name, ok = QInputDialog.getText(
            self, t("dlg_rename_title"), t("dlg_group_name"), text=self.item_name(item)
        )
        if not ok or not name:
            return
        self.set_label(item, name, item.data(0, ICON_DATA) or "")
        self.save()

    @staticmethod
    def set_color(item, color):
        """Kolor tekstu elementu i wszystkiego, co pod nim (pusty = domyślny)."""
        item.setData(0, COLOR_DATA, color or None)
        brush = QBrush(QColor(color)) if color else QBrush()
        item.setForeground(0, brush)
        for i in range(item.childCount()):
            ConnectionTree.set_color(item.child(i), color)

    def _pick_color(self, item):
        current = QColor(item.data(0, COLOR_DATA) or "#ffffff")
        color = QColorDialog.getColor(current, self, t("dlg_group_color"))
        if not color.isValid():
            return
        self.set_color(item, color.name())
        self.save()

    def _clear_color(self, item):
        self.set_color(item, "")
        self.save()

    def _pick_icon(self, item):
        choices = ICONS + [t("icon_none")]
        current = item.data(0, ICON_DATA) or ""
        icon, ok = QInputDialog.getItem(
            self,
            t("dlg_icon_title"),
            t("dlg_icon_prompt"),
            choices,
            choices.index(current) if current in choices else len(choices) - 1,
            False,
        )
        if not ok:
            return
        self.set_label(item, self.item_name(item), "" if icon == choices[-1] else icon)
        self.save()

    def _add_group(self, parent_item):
        name, ok = QInputDialog.getText(self, t("menu_new_group"), t("dlg_group_name"))
        if not ok or not name:
            return
        parent_item = parent_item or self.topLevelItem(0)
        self.set_label(QTreeWidgetItem(parent_item, []), name, GROUP_ICON)
        parent_item.setExpanded(True)
        self.save()

    def _add_connection(self, parent_item):
        dialog = ConnectionDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.values()
        parent_item = parent_item or self.topLevelItem(0)
        item = QTreeWidgetItem(parent_item, [], CONNECTION_TYPE)
        self._apply_connection(item, data)
        parent_item.setExpanded(True)
        self.save()

    def _remove_item(self, item):
        parent = item.parent()
        if parent is None:
            return
        if item.childCount() and QMessageBox.question(
            self,
            t("confirm_delete_group_title"),
            t("confirm_delete_group_body", self.item_name(item), item.childCount()),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        parent.removeChild(item)
        self.save()


class HomeTab(QWidget):
    """Pulpit startowy — pierwsza, niezamykalna zakładka (wzorem MobaXterm)."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.addStretch()

        title = QLabel(t("app_title"))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel(t("home_subtitle"))
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        buttons = QHBoxLayout()
        buttons.addStretch()
        quick_btn = QPushButton(t("home_quick_btn"))
        quick_btn.clicked.connect(main_window._quick_connect)
        buttons.addWidget(quick_btn)
        saved_btn = QPushButton(t("home_saved_btn"))
        saved_btn.clicked.connect(lambda: main_window.tree._add_connection(None))
        buttons.addWidget(saved_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        # Wyszukiwarka zapisanych połączeń — po nazwie, hoście i użytkowniku.
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("home_search_placeholder"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self._open_first)
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.itemActivated.connect(self._open_item)
        self.results.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.results)
        layout.addStretch()
        self.refresh()

    def showEvent(self, event):
        # Lista mogła się zmienić, gdy Home był schowany.
        super().showEvent(event)
        self.refresh()

    def connections(self):
        """Wszystkie zapisane połączenia z drzewa, płasko."""
        found = []
        it = QTreeWidgetItemIterator(self.main_window.tree)
        while it.value():
            item = it.value()
            if item.type() == CONNECTION_TYPE:
                found.append(item.data(0, CONNECTION_DATA) or {})
            it += 1
        return found

    @staticmethod
    def matches(data, query):
        query = query.strip().lower()
        if not query:
            return True
        haystack = " ".join(str(data.get(k, "")) for k in ("name", "host", "username"))
        return query in haystack.lower()

    def refresh(self):
        self.results.clear()
        for data in self.connections():
            if not self.matches(data, self.search.text()):
                continue
            label = f"{data.get('name', '')} — {data.get('username', '')}@{data.get('host', '')}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, data)
            self.results.addItem(item)

    def _open_item(self, item):
        self.main_window._open_connection_tab(item.data(Qt.UserRole))

    def _open_first(self):
        if self.results.count():
            self._open_item(self.results.item(0))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(t("app_title"))
        self.resize(1000, 650)

        self.tree = ConnectionTree()
        self.tree.itemDoubleClicked.connect(self._on_item_activated)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._show_current_stats)
        self.tabs.tabBarClicked.connect(self._on_tab_bar_clicked)

        # "Home": pulpit startowy, zawsze pierwsza zakładka, bez przycisku zamknięcia.
        home_index = self.tabs.addTab(HomeTab(self), t("tab_home"))
        self.tabs.tabBar().setTabButton(home_index, QTabBar.RightSide, None)

        # "+" jako ostatnia zakładka w pasku (jak nowa karta w przeglądarce) —
        # nie jak zwykła zakładka: klik ma otwierać dialog, a nie stawać się aktywny.
        self._plus_tab = QWidget()
        plus_index = self.tabs.addTab(self._plus_tab, "+")
        self.tabs.tabBar().setTabButton(plus_index, QTabBar.RightSide, None)
        self.tabs.setTabToolTip(plus_index, t("dlg_quick_title"))

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 750])

        self.setCentralWidget(splitter)
        self.splitter = splitter
        self._servers = {}  # uruchomione serwery wbudowane: etykieta -> obiekt
        self._build_menu()
        self._build_sidebar()
        # Dolny pasek: statystyki serwera z aktywnej zakładki.
        self.statusBar().showMessage(t("status_idle"))
        self._restore_layout()

    # --- układ okna między uruchomieniami ---------------------------------

    def _restore_layout(self):
        """Rozmiar okna i podział splittera z poprzedniej sesji."""
        stored = i18n.settings()
        geometry = stored.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        sizes = stored.value("splitter")
        if sizes:
            # QSettings oddaje listę tekstów, nie liczb.
            self.splitter.setSizes([int(value) for value in sizes])

    def _save_layout(self):
        stored = i18n.settings()
        stored.setValue("geometry", self.saveGeometry())
        stored.setValue("splitter", self.splitter.sizes())

    def _build_menu(self):
        """Pasek menu u góry (wzorem MobaXterm), z akcjami znanymi już z menu drzewa."""
        menu = self.menuBar()

        connection_menu = menu.addMenu(t("menu_connection"))
        connection_menu.addAction(t("menu_new_group_dots"), lambda: self.tree._add_group(self.tree.currentItem()))
        connection_menu.addAction(t("menu_new_connection_dots"), lambda: self.tree._add_connection(self.tree.currentItem()))
        connection_menu.addSeparator()
        connection_menu.addAction(t("menu_export"), self._export_connections)
        connection_menu.addAction(t("menu_import"), self._import_connections)
        connection_menu.addSeparator()
        connection_menu.addAction(t("menu_quit"), self.close)

        view_menu = menu.addMenu(t("menu_view"))
        self.toggle_tree_action = QAction(t("menu_connection_list"), self, checkable=True, checked=True)
        self.toggle_tree_action.toggled.connect(self.tree.setVisible)
        view_menu.addAction(self.toggle_tree_action)
        highlight_action = QAction(
            t("menu_highlighting"), self, checkable=True, checked=TerminalHighlighter.enabled
        )
        highlight_action.toggled.connect(self._toggle_highlighting)
        view_menu.addAction(highlight_action)

        # Wybór języka: zapis idzie do QSettings, okno czyta go przy starcie.
        language_menu = view_menu.addMenu(t("menu_language"))
        language_group = QActionGroup(self)  # kropka przy jednym języku, nie przy obu
        for code, name in i18n.LANGUAGES.items():
            action = QAction(name, self, checkable=True, checked=code == i18n.language())
            action.triggered.connect(lambda _checked, c=code: self._set_language(c))
            language_group.addAction(action)
            language_menu.addAction(action)

        # „Serwery wbudowane" — daemony po naszej stronie, wzorem MobaXterm.
        servers_menu = menu.addMenu(t("menu_servers"))
        for spec in SERVERS:
            action = QAction(t(spec["label"]), self, checkable=True)
            action.triggered.connect(lambda _checked, s=spec, a=action: self._toggle_server(s, a))
            servers_menu.addAction(action)
        servers_menu.addSeparator()
        servers_menu.addAction(t("menu_stop_all"), self._stop_servers)

        scripts_menu = menu.addMenu(t("menu_scripts"))
        for script in SCRIPTS:
            scripts_menu.addAction(t(script["label"]), lambda s=script: self._run_script(s))

        help_menu = menu.addMenu(t("menu_help"))
        help_menu.addAction(t("menu_about"), self._show_about)

    def _set_language(self, code):
        """Zapisuje wybór; przebudowa całego okna zabiłaby otwarte sesje SSH."""
        i18n.save(code)
        QMessageBox.information(self, t("menu_language"), t("lang_restart"))

    def _export_connections(self):
        path, _ = QFileDialog.getSaveFileName(
            self, t("dlg_export_title"), t("export_default_name"), t("json_filter")
        )
        if not path:
            return
        try:
            self.tree.export_to(path)
        except OSError as error:
            QMessageBox.warning(self, t("export_short"), t("err_export", error))
            return
        QMessageBox.information(
            self,
            t("export_short"),
            t("export_done", path),
        )

    def _import_connections(self):
        path, _ = QFileDialog.getOpenFileName(self, t("dlg_import_title"), "", t("json_filter"))
        if not path:
            return
        answer = QMessageBox.question(
            self,
            t("import_short"),
            t("import_question"),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.No,
        )
        if answer == QMessageBox.Cancel:
            return
        try:
            count = self.tree.import_from(path, answer == QMessageBox.Yes)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, t("import_short"), t("err_import", error))
            return
        QMessageBox.information(self, t("import_short"), t("import_done", count))

    def _toggle_highlighting(self, on):
        """Kolorowanie tekstu w terminalu — wspólne dla wszystkich zakładek."""
        TerminalHighlighter.enabled = on
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, SessionTab):
                widget.terminal.highlighter.rehighlight()

    def _toggle_server(self, spec, action):
        """Menu działa jak przełącznik: uruchom / zatrzymaj wybrany daemon."""
        running = self._servers.pop(spec["label"], None)
        if running:
            running.stop()
            action.setChecked(False)
            self.statusBar().showMessage(t("srv_stopped_one", running.label), 5000)
            return

        directory = QFileDialog.getExistingDirectory(self, t("srv_dir_prompt"))
        if not directory:
            action.setChecked(False)
            return
        port, ok = QInputDialog.getInt(
            self, t(spec["label"]), t("fld_port"), spec["port"], 1, 65535
        )
        if not ok:
            action.setChecked(False)
            return
        try:
            server = spec["cls"](directory, port)
        except OSError as error:
            action.setChecked(False)
            QMessageBox.warning(
                self,
                t(spec["label"]),
                t("srv_start_error", port, error),
            )
            return
        self._servers[spec["label"]] = server
        action.setChecked(True)
        QMessageBox.information(
            self,
            t(spec["label"]),
            t("srv_running", server.url, directory),
        )

    def _stop_servers(self):
        for server in self._servers.values():
            server.stop()
        self._servers.clear()
        for action in self.menuBar().findChildren(QAction):
            if action.isCheckable() and action.text() in [t(s["label"]) for s in SERVERS]:
                action.setChecked(False)
        self.statusBar().showMessage(t("srv_all_stopped"), 5000)

    def _run_script(self, script):
        session = self.tabs.currentWidget()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("scripts_short"), t("scripts_need_session"))
            return
        run_script(self, session.terminal.client, script)

    def _build_sidebar(self):
        """Pionowy pasek ikon po lewej, wzorem MobaXterm (Sessions/Tools/…)."""
        sidebar = QToolBar(t("sidebar"))
        sidebar.setMovable(False)
        sidebar.setFloatable(False)
        sidebar.setOrientation(Qt.Vertical)
        sidebar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        sidebar.addAction(self.toggle_tree_action)
        self.addToolBar(Qt.LeftToolBarArea, sidebar)

    def _show_about(self):
        QMessageBox.information(
            self, t("about_title"), t("about_body")
        )

    def _show_stats(self, widget, text):
        """Pasek pokazuje tylko serwer, którego zakładka jest na wierzchu."""
        if widget is self.tabs.currentWidget():
            self.statusBar().showMessage(text)

    def _show_current_stats(self, _index=None):
        widget = self.tabs.currentWidget()
        self.statusBar().showMessage(getattr(widget, "last_stats", "") or t("status_idle"))

    def _on_item_activated(self, item, _column):
        if item.type() != CONNECTION_TYPE:
            return
        self._open_connection_tab(item.data(0, CONNECTION_DATA))

    def _open_connection_tab(self, conn):
        name = conn["name"]
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)
                return

        password = decrypt_password(conn["password"]) if conn.get("password") else None

        # RDP nie pyta nas o hasło: bez zapisanego kontrolka poprosi sama.
        if conn.get("protocol", "ssh") == "rdp":
            self._open_rdp_tab(conn, password)
            return

        # Zapisane hasło odszyfrowujemy, w przeciwnym razie pytamy.
        # Puste = logowanie kluczem z agenta lub ~/.ssh.
        if password is None:
            password, ok = QInputDialog.getText(
                self,
                t("dlg_auth_title"),
                t("dlg_auth_body", conn["username"], conn["host"]),
                QLineEdit.Password,
            )
            if not ok:
                return

        self._connect_and_add_tab(conn, password)

    def _quick_connect(self):
        """Połączenie „na szybko” z przycisku + — nie trafia do drzewa/pliku."""
        dialog = ConnectionDialog(self)
        dialog.setWindowTitle(t("dlg_quick_title"))
        dialog.save_password.setVisible(False)
        if dialog.exec() != QDialog.Accepted:
            return
        conn = dialog.values()
        conn.pop("password", None)  # tymczasowe połączenie nic nie zapisuje
        password = dialog.password.text() or None
        if conn["protocol"] == "rdp":
            self._open_rdp_tab(conn, password)
            return
        self._connect_and_add_tab(conn, password)

    def _open_rdp_tab(self, conn, password):
        """None = sesja poszła do osobnego mstsc albo się nie udała."""
        tab = open_rdp(self, conn, password)
        if tab is None:
            return
        tab.session_ended.connect(lambda text, w=tab: self._show_stats(w, text))
        self._add_tab(tab, conn["name"])

    def _connect_and_add_tab(self, conn, password):
        # Okno postępu; None = anulowano lub błąd (komunikat już się pokazał).
        terminal = connect_with_progress(
            self, conn["host"], conn["port"], conn["username"], password,
            conn.get("key_file"),
        )
        if terminal is None:
            return

        session = SessionTab(terminal)
        session.terminal.stats_changed.connect(
            lambda text, w=session: self._show_stats(w, text)
        )
        self._add_tab(session, conn["name"])
        session.terminal.setFocus()

    def _add_tab(self, widget, name):
        """Nowa karta wchodzi PRZED "+", żeby "+" zawsze zostawało ostatnie."""
        index = self.tabs.insertTab(self.tabs.count() - 1, widget, name)
        self.tabs.setCurrentIndex(index)
        return index

    def _on_tab_bar_clicked(self, index):
        """"+" nie jest zwykłą zakładką — klik otwiera dialog, a nie ją aktywuje."""
        if self.tabs.widget(index) is not self._plus_tab:
            return
        self.tabs.setCurrentIndex(index - 1)  # "+" jest zawsze ostatnie
        self._quick_connect()

    def _close_tab(self, index):
        widget = self.tabs.widget(index)
        if index == 0 or widget is self._plus_tab:
            return  # "Home" i "+" nie mają przycisku zamknięcia, ale na wszelki wypadek
        self.tabs.removeTab(index)
        if hasattr(widget, "close_session"):  # SessionTab albo RdpTab
            widget.close_session()
        widget.deleteLater()

    def closeEvent(self, event):
        self._save_layout()
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if hasattr(widget, "close_session"):
                widget.close_session()
        self._stop_servers()
        wait_for_pending()  # anulowane łączenia; inaczej Qt wywala proces
        super().closeEvent(event)


def selftest():
    """Sprawdza logikę drzewa, zakładek i zapisu — bez sieci i bez okna."""
    import rdp
    import servers
    import ssh_terminal
    import tempfile

    i18n.use("en")  # testy sprawdzaja napisy domyslnego jezyka
    from PySide6.QtWidgets import QWidget

    global CONFIG_FILE
    app = QApplication.instance() or QApplication([])

    # Nie dotykaj prawdziwego pliku użytkownika.
    with tempfile.TemporaryDirectory() as tmp:
        CONFIG_FILE = Path(tmp) / "connections.json"

        tree = ConnectionTree()
        root = tree.topLevelItem(0)
        group = QTreeWidgetItem(root, ["Produkcja"])
        conn_data = {"name": "srv-01", "host": "10.0.0.1", "port": 2222, "username": "admin"}
        saved = QTreeWidgetItem(group, ["srv-01"], CONNECTION_TYPE)
        saved.setData(0, CONNECTION_DATA, conn_data)
        tree.save()
        assert CONFIG_FILE.exists(), "plik konfiguracji nie powstał"

        # Nowe drzewo = symulacja restartu aplikacji.
        reloaded = ConnectionTree()
        reloaded_root = reloaded.topLevelItem(0)
        assert reloaded_root.childCount() == 1, "grupa nie przetrwała restartu"
        reloaded_group = reloaded_root.child(0)
        assert reloaded.item_name(reloaded_group) == "Produkcja"
        # Grupa bez własnej ikony dostaje domyślny folder.
        assert reloaded_group.data(0, ICON_DATA) == GROUP_ICON, "brak ikony folderu"
        assert reloaded_group.childCount() == 1, "połączenie nie przetrwało restartu"
        reloaded_conn = reloaded_group.child(0)
        assert reloaded_conn.type() == CONNECTION_TYPE, "typ elementu zgubiony"
        assert reloaded_conn.data(0, CONNECTION_DATA) == conn_data, "dane połączenia zmienione"

        # Ikona jest doklejana do etykiety, ale w pliku nazwa zostaje czysta.
        ConnectionTree.set_label(group, "Produkcja", "🗂️")
        tree.save()
        with_icon = ConnectionTree()
        icon_group = with_icon.topLevelItem(0).child(0)
        assert with_icon.item_name(icon_group) == "Produkcja", "ikona zjadła nazwę"

        # Kolor grupy schodzi na wszystko, co w niej siedzi, i przeżywa restart.
        ConnectionTree.set_color(group, "#ff0000")
        assert saved.foreground(0).color().name() == "#ff0000", "kolor nie zszedł na dziecko"
        tree.save()
        colored_tree = ConnectionTree()
        colored = colored_tree.topLevelItem(0).child(0)
        assert colored.data(0, COLOR_DATA) == "#ff0000", "kolor nie przetrwał restartu"
        assert colored.child(0).foreground(0).color().name() == "#ff0000"
        ConnectionTree.set_color(group, "")
        assert saved.data(0, COLOR_DATA) is None, "czyszczenie koloru nie zeszło na dziecko"
        assert icon_group.data(0, ICON_DATA) == "🗂️", "ikona nie przetrwała restartu"
        assert icon_group.text(0).endswith("Produkcja")

        # Edycja połączenia: nowe dane muszą trafić do etykiety i do pliku.
        with_icon._apply_connection(
            icon_group.child(0),
            {"name": "srv-99", "host": "10.0.0.9", "port": 22, "username": "root"},
        )
        with_icon.save()
        after_edit = ConnectionTree()
        edited = after_edit.topLevelItem(0).child(0).child(0)
        assert edited.data(0, CONNECTION_DATA)["host"] == "10.0.0.9", "edycja nie zapisana"
        assert edited.text(0) == "srv-99"

        # Uszkodzony plik nie może wywalić aplikacji ani zniknąć bez śladu.
        CONFIG_FILE.write_text("{to nie jest json", encoding="utf-8")
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        broken = ConnectionTree()
        assert broken.topLevelItem(0).childCount() == 0
        assert CONFIG_FILE.with_suffix(".json.bak").exists(), "brak kopii uszkodzonego pliku"
        # Eksport i import: ten sam format co plik konfiguracyjny.
        export_file = Path(tmp) / "eksport.json"
        tree.export_to(export_file)
        assert json.loads(export_file.read_text(encoding="utf-8")) == tree.nodes()

        empty = ConnectionTree()
        empty.topLevelItem(0).takeChildren()
        assert empty.import_from(export_file, replace=True) == 1
        imported = empty.topLevelItem(0).child(0)
        assert empty.item_name(imported) == "Produkcja"
        assert imported.child(0).data(0, CONNECTION_DATA) == conn_data, "import zgubił dane"

        # Dopisanie (replace=False) nie kasuje tego, co już jest.
        empty.import_from(export_file, replace=False)
        assert empty.topLevelItem(0).childCount() == 2, "dopisywanie skasowało istniejące"

        try:
            empty.import_from(export_file.with_name("brak.json"), replace=True)
            raise AssertionError("brak pliku musi się zgłosić wyjątkiem")
        except OSError:
            pass

        print("persystencja: OK")

    # Celowo nieistniejąca ścieżka: reszta testu nie może ruszyć pliku użytkownika.
    CONFIG_FILE = Path(tempfile.gettempdir()) / "nie-istnieje-selftest.json"
    window = MainWindow()
    root = window.tree.topLevelItem(0)

    group = QTreeWidgetItem(root, ["Produkcja"])
    data = {"name": "srv-01", "host": "10.0.0.1", "port": 22, "username": "admin"}
    conn = QTreeWidgetItem(group, [data["name"]], CONNECTION_TYPE)
    conn.setData(0, CONNECTION_DATA, data)

    assert conn.type() == CONNECTION_TYPE
    assert group.type() != CONNECTION_TYPE
    assert conn.data(0, CONNECTION_DATA)["host"] == "10.0.0.1"

    # Wyszukiwarka na Home: dopasowanie po nazwie, hoście i użytkowniku.
    probe = {"name": "Serwer WWW", "host": "10.0.0.1", "username": "admin"}
    assert HomeTab.matches(probe, ""), "puste zapytanie ma przepuszczać wszystko"
    assert HomeTab.matches(probe, "www"), "brak dopasowania po nazwie"
    assert HomeTab.matches(probe, "10.0.0"), "brak dopasowania po hoście"
    assert HomeTab.matches(probe, "ADMIN"), "wyszukiwanie ma ignorować wielkość liter"
    assert not HomeTab.matches(probe, "baza"), "fałszywe dopasowanie"

    # "Home" (pierwsza) i "+" (ostatnia) to stałe zakładki bez przycisku zamknięcia.
    assert window.tabs.count() == 2, "startowe zakładki: Home i +"
    assert window.tabs.tabBar().tabButton(0, QTabBar.RightSide) is None, "Home nie może mieć X"
    assert window.tabs.tabBar().tabButton(1, QTabBar.RightSide) is None, "+ nie może mieć X"
    window._close_tab(0)
    window._close_tab(1)
    assert window.tabs.count() == 2, "Home i + nie mogą dać się zamknąć"

    # Klik w "+" ma otworzyć dialog (tu podmieniony), a nie zostać aktywną zakładką.
    quick_connect_calls = []
    window._quick_connect = lambda: quick_connect_calls.append(True)
    window.tabs.setCurrentIndex(0)
    window._on_tab_bar_clicked(1)  # "+" jest zawsze ostatnie
    assert quick_connect_calls == [True], "klik w + nie otworzył dialogu"
    assert window.tabs.currentIndex() == 0, "+ nie może zostać aktywną zakładką"

    window._on_item_activated(group, 0)
    assert window.tabs.count() == 2, "grupa nie powinna otwierać zakładki"

    # Istniejąca zakładka o tej nazwie musi zostać wybrana, zanim padnie
    # pytanie o hasło — inaczej dwuklik łączyłby się drugi raz.
    window.tabs.insertTab(window.tabs.count() - 1, QWidget(), "srv-01")
    window._open_connection_tab(data)
    assert window.tabs.count() == 3, "ponowne otwarcie nie może duplikować zakładki"
    assert window.tabs.widget(window.tabs.count() - 1) is window._plus_tab, "+ musi zostać ostatnie"

    # Przeciąganie: korzeń nie odjeżdża, połączenie nie przyjmuje dzieci.
    assert not root.flags() & Qt.ItemIsDragEnabled, "korzeń musi zostać na miejscu"
    assert not window.tree._drop_allowed(None, False), "pusty obszar to nie cel"
    assert not window.tree._drop_allowed(conn, True), "połączenie nie może być grupą"
    assert window.tree._drop_allowed(conn, False), "obok połączenia wolno"
    assert window.tree._drop_allowed(group, True), "do grupy wolno"

    # Sam ruch elementu między grupami musi przetrwać zapis i odczyt.
    inna = QTreeWidgetItem(root, ["Testy"])
    group.removeChild(conn)
    inna.addChild(conn)
    assert conn.parent() is inna and group.childCount() == 0

    # Szyfrowanie haseł: tylko Windows, wynik nie może być czytelny gołym okiem.
    if CAN_STORE_PASSWORDS:
        stored = encrypt_password("tajne hasło")
        assert "tajne" not in stored, "hasło leży w pliku otwartym tekstem"
        assert decrypt_password(stored) == "tajne hasło", "odszyfrowanie nie działa"
        assert decrypt_password("bmllIGRwYXBp") is None, "śmieci muszą dać None"
        print("szyfrowanie haseł: OK")

    # Menu i pasek boczny: akcja "Lista połączeń" musi realnie chować drzewo.
    assert window.menuBar().actions(), "brak paska menu"
    window.toggle_tree_action.setChecked(False)
    assert window.tree.isHidden(), "toggle w menu/pasku bocznym nie ukrywa drzewa"
    window.toggle_tree_action.setChecked(True)

    # Dolny pasek: bez zakładek i dla obcego widgetu nie może się wywalić.
    assert window.statusBar().currentMessage() == t("status_idle")
    window._show_current_stats()
    assert window.statusBar().currentMessage() == t("status_idle")
    window._show_stats(QWidget(), "statystyki obcej zakładki")
    assert window.statusBar().currentMessage() == t("status_idle"), "pasek pokazał nie tę zakładkę"

    # Wybór języka: menu i napisy muszą realnie się przełączać.
    assert window.tabs.tabText(0) == "🏠 Home", window.tabs.tabText(0)
    i18n.use("pl")
    assert ConnectionDialog().windowTitle() == "Połączenie SSH", "dialog nie idzie z i18n"
    i18n.use("en")
    assert ConnectionDialog().windowTitle() == "SSH connection"

    # Formularz: protokół steruje portem, tytułem i polem klucza.
    dialog = ConnectionDialog()
    assert dialog.values()["protocol"] == "ssh", "SSH ma zostać domyślne"
    assert dialog.port.value() == SSH_PORT
    dialog.protocol.setCurrentIndex(dialog.protocol.findData("rdp"))
    assert dialog.port.value() == RDP_PORT, "RDP ma swój port domyślny"
    assert dialog.values()["protocol"] == "rdp"
    assert dialog.windowTitle() == "RDP connection", dialog.windowTitle()

    # Ręcznie wpisanego portu przełącznik protokołu nie może nadpisać.
    custom = ConnectionDialog()
    custom.port.setValue(2222)
    custom.protocol.setCurrentIndex(custom.protocol.findData("rdp"))
    assert custom.port.value() == 2222, "własny port musi przeżyć zmianę protokołu"

    # Klucz prywatny zapisuje się tylko dla SSH.
    keyed = ConnectionDialog(data={"host": "h", "key_file": "C:/klucze/id_rsa"})
    assert keyed.values()["key_file"] == "C:/klucze/id_rsa"
    keyed.protocol.setCurrentIndex(keyed.protocol.findData("rdp"))
    assert "key_file" not in keyed.values(), "RDP nie używa klucza SSH"

    i18n.selftest()

    ssh_terminal.selftest()
    i18n.use("en")  # ssh_terminal.selftest() bawi się językiem
    rdp.selftest()
    servers.selftest()
    del app
    print("main selftest OK")


def main():
    app = QApplication(sys.argv)
    i18n.load()  # przed zbudowaniem okna — napisy czytane są raz
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
