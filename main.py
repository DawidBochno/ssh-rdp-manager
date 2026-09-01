"""Szkielet aplikacji desktopowej do zarządzania połączeniami SSH/RDP.

Lewa strona: drzewo katalogów z grupami i połączeniami.
Prawa strona: zakładki, jedna na każde otwarte połączenie.
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
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

from ssh_terminal import SshTerminal

CONNECTION_TYPE = QTreeWidgetItem.UserType + 1
CONNECTION_DATA = Qt.UserRole + 1


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

        form = QFormLayout(self)
        form.addRow("Nazwa:", self.name)
        form.addRow("Host:", self.host)
        form.addRow("Port:", self.port)
        form.addRow("Użytkownik:", self.username)

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
        return {
            "name": self.name.text().strip() or host,
            "host": host,
            "port": self.port.value(),
            "username": self.username.text().strip(),
        }


class ConnectionTree(QTreeWidget):
    """Drzewo grup i połączeń z menu kontekstowym do zarządzania nimi."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        root = QTreeWidgetItem(["Wszystkie połączenia"])
        self.addTopLevelItem(root)
        root.setExpanded(True)

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("Nowa grupa", lambda: self._add_group(item))
        menu.addAction("Nowe połączenie", lambda: self._add_connection(item))
        if item is not None:
            menu.addSeparator()
            menu.addAction("Usuń", lambda: self._remove_item(item))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _add_group(self, parent_item):
        name, ok = QInputDialog.getText(self, "Nowa grupa", "Nazwa grupy:")
        if not ok or not name:
            return
        parent_item = parent_item or self.topLevelItem(0)
        QTreeWidgetItem(parent_item, [name])
        parent_item.setExpanded(True)

    def _add_connection(self, parent_item):
        dialog = ConnectionDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.values()
        parent_item = parent_item or self.topLevelItem(0)
        item = QTreeWidgetItem(parent_item, [data["name"]], CONNECTION_TYPE)
        item.setData(0, CONNECTION_DATA, data)
        item.setToolTip(0, f"{data['username']}@{data['host']}:{data['port']}")
        parent_item.setExpanded(True)

    def _remove_item(self, item):
        parent = item.parent()
        if parent is None:
            return
        parent.removeChild(item)


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

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 750])

        self.setCentralWidget(splitter)

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

        # Hasło pytamy przy każdym połączeniu i nigdzie go nie zapisujemy.
        # Puste = logowanie kluczem z agenta lub ~/.ssh.
        password, ok = QInputDialog.getText(
            self,
            "Uwierzytelnianie",
            f"Hasło dla {conn['username']}@{conn['host']}\n(puste = klucz SSH):",
            QLineEdit.Password,
        )
        if not ok:
            return

        try:
            terminal = SshTerminal(
                conn["host"], conn["port"], conn["username"], password, self
            )
        except Exception as error:
            QMessageBox.critical(
                self, "Błąd połączenia", f"Nie udało się połączyć:\n\n{error}"
            )
            return

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
        super().closeEvent(event)


def selftest():
    """Sprawdza logikę drzewa i zakładek bez łączenia się po sieci."""
    import ssh_terminal
    from PySide6.QtWidgets import QWidget

    app = QApplication.instance() or QApplication([])
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
