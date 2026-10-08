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

RDP też wchodzi do siatki: zamiast terminala jedzie kontrolka (`RdpTab.control`),
pisanie do wszystkich jej nie dotyczy (nie ma `send_text`).
"""

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QDrag
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


def pane_widget(session):
    """Co jedzie do siatki: terminal SSH albo kontrolka RDP."""
    terminal = getattr(session, "terminal", None)
    return terminal if terminal is not None else getattr(session, "control", None)


def can_split(widget):
    """Zakładka, którą da się wstawić do siatki (i jeszcze w żadnej nie jest)."""
    return (hasattr(widget, "split") and widget.split is None
            and pane_widget(widget) is not None)


def _return_pane(session):
    splitter = getattr(session, "splitter", None)  # SessionTab: SFTP | terminal
    if splitter is not None:
        splitter.insertWidget(1, session.terminal)
    else:
        session.layout().addWidget(pane_widget(session), 1)  # RDP, VNC, Telnet/COM


def grid_rows(count):
    """Ile terminali w kolejnych wierszach: 2 -> [2], 3 -> [2, 1], 4 -> [2, 2]."""
    return [min(2, count - i) for i in range(0, count, 2)]


class SplitTab(QWidget):
    """Zakładka z siatką terminali. `sessions` = otwarte `SessionTab`/`RdpTab`."""

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

        self.rows = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        for session in self.sessions:
            self._attach(session)
        self._build()
        QApplication.instance().focusChanged.connect(self._on_focus)

    def _attach(self, session):
        session.split = self
        if getattr(session, "terminal", None) is not None:
            session.terminal.sent.connect(self._on_sent)

    def _build(self):
        """Wiersze siatki od nowa — ramki już istniejących paneli tylko się przenoszą."""
        old, self.rows = self.rows, QSplitter(Qt.Vertical)
        queue = list(self.sessions)
        for count in grid_rows(len(queue)):
            row = QSplitter(Qt.Horizontal)
            for _ in range(count):
                session = queue.pop(0)
                row.addWidget(self._frames.get(session) or self._pane(session))
            self.rows.addWidget(row)
        if old is None:
            self.layout().addWidget(self.rows, 1)
        else:
            self.layout().replaceWidget(old, self.rows)
            old.deleteLater()
        self._show_broadcast(self.broadcast.isChecked())

    def add_session(self, session):
        """Dorzucenie zakładki do gotowej siatki (przeciągnięcie). False = siatka pełna."""
        if len(self.sessions) >= MAX_PANES or not can_split(session):
            return False
        self.sessions.append(session)
        self._attach(session)
        self._build()
        return True

    def _pane(self, session):
        """Ramka: nazwa sesji nad terminalem. Terminal przechodzi tu z SessionTab."""
        frame = QFrame()
        frame.setObjectName("pane")
        box = QVBoxLayout(frame)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(1)
        box.addWidget(QLabel(getattr(session, "tab_name", "")))
        widget = pane_widget(session)
        box.addWidget(widget, 1)
        widget.show()
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
                terminal = getattr(session, "terminal", None)
                if terminal is not None and terminal is not source:
                    terminal.send_text(data)
        finally:
            self._forwarding = False

    def _on_focus(self, _old, new):
        for session in self.sessions:
            pane = pane_widget(session)
            hit = new is not None and (new is pane or pane.isAncestorOf(new))
            if hit and session is not self._focused:
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
            if getattr(session, "terminal", None) is not None:
                session.terminal.sent.disconnect(self._on_sent)
            session.split = None
            _return_pane(session)
        released, self.sessions = self.sessions, []
        if self._release_callback:
            self._release_callback(self, released)


TAB_MIME = "application/x-sshrdp-tab"


class TabDragger(QObject):
    """Przeciąganie zakładki na inną w pasku: `on_drop(źródło, cel)` z indeksami.

    Filtr zdarzeń na `QTabBar` zamiast podklasy — pasek tworzy `QTabWidget`.
    Pasek nie jest przesuwalny (`setMovable`), więc przeciąganie nie gryzie się
    z przestawianiem kolejności.
    """

    def __init__(self, bar, on_drop):
        super().__init__(bar)
        self.bar, self.on_drop, self._press = bar, on_drop, None
        bar.setAcceptDrops(True)
        bar.installEventFilter(self)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._press = event.position().toPoint()
        elif kind == QEvent.MouseButtonRelease:
            self._press = None
        elif kind == QEvent.MouseMove and self._press is not None and event.buttons() & Qt.LeftButton:
            moved = (event.position().toPoint() - self._press).manhattanLength()
            if moved >= QApplication.startDragDistance():
                index, self._press = self.bar.tabAt(self._press), None
                if index >= 0:
                    mime = QMimeData()
                    mime.setData(TAB_MIME, str(index).encode())
                    drag = QDrag(self.bar)
                    drag.setMimeData(mime)
                    drag.exec(Qt.MoveAction)
                return True
        elif kind in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            if not event.mimeData().hasFormat(TAB_MIME):
                return False
            event.acceptProposedAction()
            if kind == QEvent.Drop:
                source = int(bytes(event.mimeData().data(TAB_MIME)).decode())
                target = self.bar.tabAt(event.position().toPoint())
                if target >= 0 and target != source:
                    # Po wyjściu z pętli `drag.exec` — zmiana zakładek w jej środku to proszenie się o kłopoty.
                    QTimer.singleShot(0, lambda: self.on_drop(source, target))
            return True
        return False


class SplitPicker(QDialog):
    """Wybór 2–4 otwartych sesji (SSH/RDP) do siatki."""

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

    # RDP: kontrolka zamiast terminala, bez pisania do wszystkich; dorzucanie
    # do gotowej siatki do MAX_PANES.
    class _Rdp(QWidget):
        def __init__(self):
            super().__init__()
            self.tab_name, self.split = "rdp", None
            self.control = QWidget()
            QVBoxLayout(self).addWidget(self.control)

    rdp, extra = _Rdp(), _Session("e")
    assert can_split(rdp) and not can_split(a), "a jest już w siatce"
    assert grid.add_session(rdp) and rdp.control.parent() is not rdp
    assert not grid.add_session(extra), "piąty panel nie wchodzi"
    a.terminal.send_text("df\r")  # rozsyłanie dalej działa, RDP pomijane
    assert b.terminal.channel.sent == ["uptime\r", "df\r"], b.terminal.channel.sent

    grid.release()
    assert rdp.control.parent() is rdp and rdp.split is None, "kontrolka RDP ma wrócić"
    assert released == [[a, b, c, rdp]] and a.split is None
    assert a.splitter.widget(1) is a.terminal, "terminal ma wrócić na swoje miejsce"
    a.terminal.send_text("x")
    assert b.terminal.channel.sent == ["uptime\r", "df\r"], "po rozdzieleniu nic nie powiela"
    grid.release()  # drugi raz = nic
    assert len(released) == 1
    del app
    print("split selftest OK")


if __name__ == "__main__":
    selftest()
