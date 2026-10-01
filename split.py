"""Podział ekranu: 2–4 terminale SSH obok siebie + pisanie do wszystkich naraz.

Do siatki trafia sam `SshTerminal`, nie `SessionTab` — zakładka sesji zostaje
w `QTabWidget`, tylko schowana (`setTabVisible`). Dzięki temu wszystko, co
chodzi po zakładkach (dashboard, „na wielu serwerach”, zamykanie okna,
brak duplikatów po `origin`), działa bez zmian. „Rozdziel” oddaje terminale
z powrotem do ich `SessionTab.splitter`.

Pisanie do wszystkich: terminal emituje `sent` z każdego udanego
`send_text()`, siatka powtarza to samo przez `send_text()` pozostałych —
czyli przez tę samą bramkę co klawiatura, więc tryb tylko do odczytu
dalej obowiązuje każdy terminal osobno.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from i18n import t

MAX_PANES = 4


def grid_rows(count):
    """Ile terminali w kolejnych wierszach: 2 -> [2], 3 -> [2, 1], 4 -> [2, 2]."""
    return [min(2, count - i) for i in range(0, count, 2)]


class SplitTab(QWidget):
    """Zakładka z siatką terminali. `sessions` = otwarte `SessionTab` (SSH)."""

    focus_changed = Signal()  # inny terminal w siatce dostał fokus — pasek statystyk

    def __init__(self, sessions, release_callback=None):
        super().__init__()
        self.sessions = list(sessions)
        self._release_callback = release_callback
        self._forwarding = False
        self._focused = self.sessions[0]
        self._frames = {}

        self.broadcast = QCheckBox(t("split_broadcast"))
        self.broadcast.setToolTip(t("split_broadcast_tip"))
        self.broadcast.toggled.connect(self._show_broadcast)
        unsplit = QPushButton(t("split_release"))
        unsplit.clicked.connect(self.release)
        top = QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 0)
        top.addWidget(self.broadcast)
        top.addStretch()
        top.addWidget(unsplit)

        rows = QSplitter(Qt.Vertical)
        queue = list(self.sessions)
        for count in grid_rows(len(queue)):
            row = QSplitter(Qt.Horizontal)
            for _ in range(count):
                row.addWidget(self._pane(queue.pop(0)))
            rows.addWidget(row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(rows, 1)

        for session in self.sessions:
            session.split = self
            session.terminal.sent.connect(self._on_sent)
        QApplication.instance().focusChanged.connect(self._on_focus)
        self._show_broadcast(False)

    def _pane(self, session):
        """Ramka: nazwa sesji nad terminalem. Terminal przechodzi tu z SessionTab."""
        frame = QFrame()
        frame.setObjectName("pane")
        box = QVBoxLayout(frame)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(1)
        box.addWidget(QLabel(getattr(session, "tab_name", "")))
        box.addWidget(session.terminal, 1)
        session.terminal.show()
        self._frames[session] = frame
        return frame

    def _show_broadcast(self, on):
        # Czerwona ramka = wszystko, co piszesz, idzie na każdy z tych serwerów.
        style = "QFrame#pane { border: 2px solid #e74c3c; }" if on else ""
        for frame in self._frames.values():
            frame.setStyleSheet(style)

    def _on_sent(self, data):
        if self._forwarding or not self.broadcast.isChecked():
            return
        source = self.sender()
        self._forwarding = True  # inaczej każdy powtórzony `send_text` wracałby tu znowu
        try:
            for session in self.sessions:
                if session.terminal is not source:
                    session.terminal.send_text(data)
        finally:
            self._forwarding = False

    def _on_focus(self, _old, new):
        for session in self.sessions:
            if new is session.terminal and session is not self._focused:
                self._focused = session
                self.focus_changed.emit()

    def focused_session(self):
        """Sesja ostatnio klikniętego terminala — na nią idą Programy, SFTP, statystyki."""
        return self._focused

    def release(self):
        """„Rozdziel”: terminale wracają do swoich zakładek, siatka znika."""
        if not self.sessions:
            return
        QApplication.instance().focusChanged.disconnect(self._on_focus)
        for session in self.sessions:
            session.terminal.sent.disconnect(self._on_sent)
            session.split = None
            session.splitter.insertWidget(1, session.terminal)
        released, self.sessions = self.sessions, []
        if self._release_callback:
            self._release_callback(self, released)


class SplitPicker(QDialog):
    """Wybór 2–4 otwartych sesji SSH do siatki."""

    def __init__(self, parent, sessions, current=None):
        super().__init__(parent)
        self.setWindowTitle(t("split_title"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("split_pick", MAX_PANES)))
        self.list = QListWidget()
        for session in sessions:
            item = QListWidgetItem(session.tab_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if session is current else Qt.Unchecked)
            item.setData(Qt.UserRole, session)
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self):
        return [
            self.list.item(i).data(Qt.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        ]

    def accept(self):
        if not 2 <= len(self.chosen()) <= MAX_PANES:
            QMessageBox.warning(self, t("split_title"), t("split_pick", MAX_PANES))
            return
        super().accept()


def selftest():
    from ssh_terminal import OfflineTerminal

    assert grid_rows(2) == [2]
    assert grid_rows(3) == [2, 1]
    assert grid_rows(4) == [2, 2]

    app = QApplication.instance() or QApplication([])

    class _Session(QWidget):
        """To, czego siatka używa z SessionTab: terminal w splitterze, nazwa, `split`."""

        def __init__(self, name):
            super().__init__()
            self.tab_name, self.split = name, None
            self.terminal = OfflineTerminal()
            self.splitter = QSplitter(self)
            self.splitter.addWidget(QWidget())  # miejsce panelu SFTP
            self.splitter.addWidget(self.terminal)

    a, b, c = _Session("a"), _Session("b"), _Session("c")
    c.terminal.read_only = True
    released = []
    grid = SplitTab([a, b, c], lambda g, s: released.append(s))
    assert a.split is grid and a.terminal.parent() is not a.splitter, "terminal ma być w siatce"

    a.terminal.send_text("ls\r")
    assert b.terminal.channel.sent == [], "bez przełącznika nic nie idzie dalej"
    grid.broadcast.setChecked(True)
    a.terminal.send_text("uptime\r")
    assert a.terminal.channel.sent == ["ls\r", "uptime\r"], "źródło dostało dwa razy (pętla)"
    assert b.terminal.channel.sent == ["uptime\r"], b.terminal.channel.sent
    assert c.terminal.channel.sent == [], "tylko do odczytu nie może dostać tekstu z siatki"

    grid.release()
    assert released == [[a, b, c]] and a.split is None
    assert a.splitter.widget(1) is a.terminal, "terminal ma wrócić na swoje miejsce"
    a.terminal.send_text("x")
    assert b.terminal.channel.sent == ["uptime\r"], "po rozdzieleniu nic nie powiela"
    grid.release()  # drugi raz = nic
    assert len(released) == 1
    del app
    print("split selftest OK")


if __name__ == "__main__":
    selftest()
