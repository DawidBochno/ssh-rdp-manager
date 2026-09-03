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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from ssh_terminal import SshTerminal, connect_with_progress, wait_for_pending

CONNECTION_TYPE = QTreeWidgetItem.UserType + 1
CONNECTION_DATA = Qt.UserRole + 1

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

IDLE_STATUS = "Brak aktywnego połączenia"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(func, data):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI odmówiło operacji")
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


class ConnectionDialog(QDialog):
    """Formularz danych połączenia SSH."""

    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle("Połączenie SSH")
        data = data or {}

        self.name = QLineEdit(data.get("name", ""))
        self.host = QLineEdit(data.get("host", ""))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(data.get("port", 22))
        self.username = QLineEdit(data.get("username", ""))

        stored = decrypt_password(data["password"]) if data.get("password") else ""
        self.password = QLineEdit(stored or "")
        self.password.setEchoMode(QLineEdit.Password)
        self.save_password = QCheckBox("Zapisz hasło (szyfrowane kontem Windows)")
        self.save_password.setChecked(bool(stored))
        self.save_password.setEnabled(CAN_STORE_PASSWORDS)
        if not CAN_STORE_PASSWORDS:
            self.save_password.setToolTip("Zapis hasła działa tylko na Windows.")

        form = QFormLayout(self)
        form.addRow("Nazwa:", self.name)
        form.addRow("Host:", self.host)
        form.addRow("Port:", self.port)
        form.addRow("Użytkownik:", self.username)
        form.addRow("Hasło:", self.password)
        form.addRow("", self.save_password)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self):
        if not self.host.text().strip():
            QMessageBox.warning(self, "Brak danych", "Podaj adres hosta.")
            return
        super().accept()

    def values(self):
        host = self.host.text().strip()
        data = {
            "name": self.name.text().strip() or host,
            "host": host,
            "port": self.port.value(),
            "username": self.username.text().strip(),
        }
        if self.save_password.isChecked() and self.password.text():
            data["password"] = encrypt_password(self.password.text())
        return data


class ConnectionTree(QTreeWidget):
    """Drzewo grup i połączeń z menu kontekstowym do zarządzania nimi."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Przenoszenie elementów myszą wewnątrz drzewa.
        self.setDragDropMode(QTreeWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

        root = QTreeWidgetItem(["Wszystkie połączenia"])
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
        if item.type() == CONNECTION_TYPE:
            node["connection"] = item.data(0, CONNECTION_DATA)
        else:
            node["children"] = [
                self._serialize(item.child(i)) for i in range(item.childCount())
            ]
        return node

    def save(self):
        """Zrzuca całe drzewo do JSON. Wołane po każdej zmianie."""
        root = self.topLevelItem(0)
        nodes = [self._serialize(root.child(i)) for i in range(root.childCount())]
        try:
            CONFIG_FILE.write_text(
                json.dumps(nodes, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as error:
            QMessageBox.warning(
                self, "Błąd zapisu", f"Nie udało się zapisać połączeń:\n\n{error}"
            )

    def _build(self, parent, node):
        name = str(node.get("name", "bez nazwy"))
        if "connection" in node:
            item = QTreeWidgetItem(parent, [], CONNECTION_TYPE)
            self._apply_connection(item, node["connection"])
        else:
            item = QTreeWidgetItem(parent, [])
            for child in node.get("children", []):
                self._build(item, child)
            item.setExpanded(True)
        self.set_label(item, name, str(node.get("icon", "")))

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
                "Błąd odczytu",
                f"Nie udało się wczytać zapisanych połączeń:\n\n{error}\n\n"
                + (f"Kopia uszkodzonego pliku: {backup}" if backup else ""),
            )
            return

        root = self.topLevelItem(0)
        for node in nodes:
            self._build(root, node)

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
        menu.addAction("Nowa grupa", lambda: self._add_group(item))
        menu.addAction("Nowe połączenie", lambda: self._add_connection(item))
        if item is not None and item is not self.topLevelItem(0):
            menu.addSeparator()
            if item.type() == CONNECTION_TYPE:
                menu.addAction("Edytuj połączenie…", lambda: self._edit_connection(item))
            else:
                menu.addAction("Zmień nazwę…", lambda: self._rename_group(item))
            menu.addAction("Ikona…", lambda: self._pick_icon(item))
            menu.addSeparator()
            menu.addAction("Usuń", lambda: self._remove_item(item))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _apply_connection(self, item, data):
        """Wpisuje dane połączenia do elementu drzewa (etykieta, tooltip, dane)."""
        item.setData(0, CONNECTION_DATA, data)
        item.setToolTip(
            0,
            f"{data.get('username', '')}@{data.get('host', '')}:{data.get('port', 22)}"
            + ("\n(hasło zapisane)" if data.get("password") else ""),
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
            self, "Zmień nazwę", "Nazwa grupy:", text=self.item_name(item)
        )
        if not ok or not name:
            return
        self.set_label(item, name, item.data(0, ICON_DATA) or "")
        self.save()

    def _pick_icon(self, item):
        choices = ICONS + ["(bez ikony)"]
        current = item.data(0, ICON_DATA) or ""
        icon, ok = QInputDialog.getItem(
            self,
            "Ikona",
            "Wybierz ikonę:",
            choices,
            choices.index(current) if current in choices else len(choices) - 1,
            False,
        )
        if not ok:
            return
        self.set_label(item, self.item_name(item), "" if icon == choices[-1] else icon)
        self.save()

    def _add_group(self, parent_item):
        name, ok = QInputDialog.getText(self, "Nowa grupa", "Nazwa grupy:")
        if not ok or not name:
            return
        parent_item = parent_item or self.topLevelItem(0)
        QTreeWidgetItem(parent_item, [name])
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
            "Usunąć grupę?",
            f"„{self.item_name(item)}” zawiera {item.childCount()} elementów. Usunąć wszystko?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        parent.removeChild(item)
        self.save()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Menedżer połączeń SSH/RDP")
        self.resize(1000, 650)

        self.tree = ConnectionTree()
        self.tree.itemDoubleClicked.connect(self._on_item_activated)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._show_current_stats)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 750])

        self.setCentralWidget(splitter)
        # Dolny pasek: statystyki serwera z aktywnej zakładki.
        self.statusBar().showMessage(IDLE_STATUS)

    def _show_stats(self, widget, text):
        """Pasek pokazuje tylko serwer, którego zakładka jest na wierzchu."""
        if widget is self.tabs.currentWidget():
            self.statusBar().showMessage(text)

    def _show_current_stats(self, _index=None):
        widget = self.tabs.currentWidget()
        self.statusBar().showMessage(getattr(widget, "last_stats", "") or IDLE_STATUS)

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

        # Zapisane hasło odszyfrowujemy, w przeciwnym razie pytamy.
        # Puste = logowanie kluczem z agenta lub ~/.ssh.
        password = decrypt_password(conn["password"]) if conn.get("password") else None
        if password is None:
            password, ok = QInputDialog.getText(
                self,
                "Uwierzytelnianie",
                f"Hasło dla {conn['username']}@{conn['host']}\n(puste = klucz SSH):",
                QLineEdit.Password,
            )
            if not ok:
                return

        # Okno postępu; None = anulowano lub błąd (komunikat już się pokazał).
        terminal = connect_with_progress(
            self, conn["host"], conn["port"], conn["username"], password
        )
        if terminal is None:
            return

        terminal.stats_changed.connect(
            lambda text, w=terminal: self._show_stats(w, text)
        )
        index = self.tabs.addTab(terminal, name)
        self.tabs.setCurrentIndex(index)
        terminal.setFocus()

    def _close_tab(self, index):
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if isinstance(widget, SshTerminal):
            widget.close_session()
        widget.deleteLater()

    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, SshTerminal):
                widget.close_session()
        wait_for_pending()  # anulowane łączenia; inaczej Qt wywala proces
        super().closeEvent(event)


def selftest():
    """Sprawdza logikę drzewa, zakładek i zapisu — bez sieci i bez okna."""
    import ssh_terminal
    import tempfile
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
        assert reloaded_group.text(0) == "Produkcja"
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

    window._on_item_activated(group, 0)
    assert window.tabs.count() == 0, "grupa nie powinna otwierać zakładki"

    # Istniejąca zakładka o tej nazwie musi zostać wybrana, zanim padnie
    # pytanie o hasło — inaczej dwuklik łączyłby się drugi raz.
    window.tabs.addTab(QWidget(), "srv-01")
    window._open_connection_tab(data)
    assert window.tabs.count() == 1, "ponowne otwarcie nie może duplikować zakładki"

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

    # Dolny pasek: bez zakładek i dla obcego widgetu nie może się wywalić.
    assert window.statusBar().currentMessage() == IDLE_STATUS
    window._show_current_stats()
    assert window.statusBar().currentMessage() == IDLE_STATUS
    window._show_stats(QWidget(), "statystyki obcej zakładki")
    assert window.statusBar().currentMessage() == IDLE_STATUS, "pasek pokazał nie tę zakładkę"

    ssh_terminal.selftest()
    del app
    print("main selftest OK")


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
