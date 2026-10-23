"""Dziennik audytu i odtwarzanie nagranych sesji (ROADMAP #25).

Dziennik: `logs/audit.jsonl`, jedna linia JSON na zdarzenie — kto (konto
Windows), kiedy, gdzie (nazwa i host połączenia), co: `connect`, `command`,
`disconnect`. Polecenia zbiera `SshTerminal._audit_input` (przez `send_text`,
więc też siatka, makra i pole polecenia). Opt-in w Ustawieniach → Bezpieczeństwo,
bo polecenia potrafią zawierać sekrety (jak historia powłoki). Plik nie wygasa.

Nagrania: `.cast` (asciicast v2) obok zapisu sesji `.log` — pisze je
`SshTerminal.start_log`, gdy włączony „zapis sesji”. `ReplayDialog` odtwarza
je w prawdziwym terminalu (`OfflineTerminal`), więc htop i vim wyglądają jak na żywo.
"""

import getpass
import json
import time

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import i18n
from i18n import t
from ssh_terminal import SESSION_LOG_DIR, OfflineTerminal

AUDIT_FILE = SESSION_LOG_DIR / "audit.jsonl"
MAX_SHOWN = 5000  # najnowsze wpisy w oknie; plik ma wszystkie
IDLE_LIMIT = 2.0  # przy odtwarzaniu dłuższa cisza skraca się do tylu sekund
SPEEDS = (1, 2, 4, 8)


def enabled():
    return i18n.settings().value("audit_log", False, type=bool)


def write(event, conn, host, text="", path=None):
    """Dopisuje zdarzenie; błąd zapisu nie może przeszkodzić w pracy."""
    if not enabled():
        return
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "user": getpass.getuser(),
        "conn": conn, "host": host, "event": event, "text": text,
    }
    path = path or AUDIT_FILE
    try:
        path.parent.mkdir(exist_ok=True)
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(path=None):
    """Wpisy dziennika od najstarszego; uszkodzone linie pomijane."""
    entries = []
    try:
        with open(path or AUDIT_FILE, encoding="utf-8") as file:
            for line in file:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except OSError:
        pass
    return entries


def matches(entry, needle):
    """Filtr okna: fragment tekstu w dowolnym polu, bez wielkości liter."""
    needle = needle.strip().lower()
    return not needle or any(needle in str(value).lower() for value in entry.values())


def read_cast(path):
    """(nagłówek, [(sekunda, tekst)]) z pliku asciicast v2; ValueError = nie nagranie."""
    with open(path, encoding="utf-8") as file:
        header = json.loads(file.readline())
        if not isinstance(header, dict) or header.get("version") != 2:
            raise ValueError("not asciicast v2")
        events = []
        for line in file:
            try:
                at, kind, text = json.loads(line)
            except ValueError:
                continue  # urwana ostatnia linia po awarii programu
            if kind == "o":
                events.append((float(at), text))
    return header, events


def delays(events, idle_limit=IDLE_LIMIT):
    """Odstęp przed każdym zdarzeniem, długie przerwy skrócone do `idle_limit`."""
    result, previous = [], 0.0
    for at, _ in events:
        result.append(min(max(at - previous, 0.0), idle_limit))
        previous = at
    return result


def _clock(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class AuditDialog(QDialog):
    def __init__(self, parent, path=None):
        super().__init__(parent)
        self.setWindowTitle(t("audit_title"))
        self.resize(900, 520)
        self.entries = list(reversed(read(path)))[:MAX_SHOWN]  # najnowsze na górze
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(t("audit_filter"))
        self.filter.textChanged.connect(self._fill)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            t("audit_col_time"), t("audit_col_user"), t("audit_col_conn"),
            t("audit_col_host"), t("audit_col_event"), t("audit_col_text"),
        ])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        replay = QPushButton(t("audit_replay"))
        replay.clicked.connect(self._replay)
        folder = QPushButton(t("audit_open_folder"))
        folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(SESSION_LOG_DIR)))
        )
        buttons = QHBoxLayout()
        buttons.addWidget(replay)
        buttons.addWidget(folder)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        if not enabled():
            hint = QLabel(t("audit_disabled"))
            hint.setWordWrap(True)
            layout.addWidget(hint)
        layout.addWidget(self.filter)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self._fill()

    def _fill(self):
        shown = [e for e in self.entries if matches(e, self.filter.text())]
        self.table.setRowCount(len(shown))
        for row, entry in enumerate(shown):
            event = entry.get("event", "")
            values = (
                entry.get("time", ""), entry.get("user", ""), entry.get("conn", ""),
                entry.get("host", ""), t(f"audit_event_{event}") if event in
                ("connect", "disconnect", "command") else event, entry.get("text", ""),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))

    def _replay(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t("audit_replay"), str(SESSION_LOG_DIR), "asciicast (*.cast)"
        )
        if path:
            open_replay(self, path)


def open_replay(parent, path):
    try:
        header, events = read_cast(path)
    except (OSError, ValueError):
        QMessageBox.warning(parent, t("replay_title"), t("replay_bad_file"))
        return None
    dialog = ReplayDialog(parent, path, header, events)
    dialog.show()
    return dialog


class ReplayDialog(QDialog):
    """Odtwarzacz nagrania: zdarzenia po kolei przez `QTimer`, z pauzą i prędkością."""

    def __init__(self, parent, path, header, events):
        super().__init__(parent)
        self.setWindowTitle(t("replay_title") + f" — {path}")
        self.resize(900, 560)
        self.events = events
        self.delays = delays(events)
        self.total = sum(self.delays)
        self.index = 0
        self.played = 0.0
        self.terminal = OfflineTerminal()
        self.terminal.read_only = True  # klawisze nie idą nigdzie, ale na wszelki wypadek
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._step)
        self.play = QPushButton(t("replay_pause"))
        self.play.clicked.connect(self._toggle)
        self.speed = QComboBox()
        self.speed.addItems([f"{s}×" for s in SPEEDS])
        self.position = QLabel()
        controls = QHBoxLayout()
        controls.addWidget(self.play)
        controls.addWidget(QLabel(t("replay_speed")))
        controls.addWidget(self.speed)
        controls.addStretch()
        controls.addWidget(self.position)
        layout = QVBoxLayout(self)
        layout.addWidget(self.terminal)
        layout.addLayout(controls)
        self._show_position()
        self._schedule()

    def _schedule(self):
        if self.index < len(self.events):
            speed = SPEEDS[self.speed.currentIndex()]
            self.timer.start(int(self.delays[self.index] * 1000 / speed))
        else:
            self.play.setEnabled(False)  # koniec; od początku = otworzyć jeszcze raz

    def _step(self):
        self.terminal._append(self.events[self.index][1])
        self.played += self.delays[self.index]
        self.index += 1
        self._show_position()
        self._schedule()

    def _toggle(self):
        if self.timer.isActive():
            self.timer.stop()
            self.play.setText(t("replay_play"))
        else:
            self.play.setText(t("replay_pause"))
            self._schedule()

    def _show_position(self):
        self.position.setText(f"{_clock(self.played)} / {_clock(self.total)}")

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


def selftest():
    import tempfile
    from pathlib import Path

    from PySide6.QtWidgets import QApplication

    from ssh_terminal import cast_header, cast_line

    QApplication.instance() or QApplication([])
    stored = i18n.settings().value("audit_log")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "logs" / "audit.jsonl"
            i18n.settings().setValue("audit_log", False)
            write("connect", "web", "10.0.0.1", path=log)
            assert not log.exists(), "wyłączony dziennik coś zapisał"
            i18n.settings().setValue("audit_log", True)
            write("connect", "web", "10.0.0.1", path=log)
            write("command", "web", "10.0.0.1", "ls -la", path=log)
            with open(log, "a", encoding="utf-8") as file:
                file.write('{"urwane\n')
            entries = read(log)
            assert [e["event"] for e in entries] == ["connect", "command"], entries
            assert entries[1]["text"] == "ls -la" and entries[0]["user"]
            assert matches(entries[1], "LS -") and not matches(entries[1], "rm")
            assert matches(entries[0], "  ")

            dialog = AuditDialog(None, log)
            assert dialog.table.rowCount() == 2
            assert dialog.table.item(0, 5).text() == "ls -la", "najnowsze na górze"
            dialog.filter.setText("10.0.0.1")
            assert dialog.table.rowCount() == 2
            dialog.filter.setText("ls")
            assert dialog.table.rowCount() == 1

            cast = Path(tmp) / "web.cast"
            cast.write_text(
                cast_header(80, 24, 0) + cast_line(0.5, "$ ") + cast_line(10.0, "\x1b[1mok\x1b[0m\r\n")
                + '[11.0, "o", "ur', encoding="utf-8",
            )
            header, events = read_cast(cast)
            assert header["width"] == 80 and events == [(0.5, "$ "), (10.0, "\x1b[1mok\x1b[0m\r\n")]
            assert delays(events) == [0.5, IDLE_LIMIT], "długa cisza skrócona"
            player = open_replay(None, cast)
            player.timer.stop()
            player._step()
            player._step()
            assert "ok" in player.terminal.toPlainText() and "[1m" not in player.terminal.toPlainText()
            assert not player.play.isEnabled() and player.position.text() == "0:02 / 0:02"
            player.close()
            bad = Path(tmp) / "zly.cast"
            bad.write_text("{}", encoding="utf-8")
            warned = []
            original = QMessageBox.warning
            QMessageBox.warning = lambda *args: warned.append(args)
            try:
                assert open_replay(None, bad) is None and warned
            finally:
                QMessageBox.warning = original
    finally:
        if stored is None:
            i18n.settings().remove("audit_log")
        else:
            i18n.settings().setValue("audit_log", stored)
    print("audit selftest OK")


if __name__ == "__main__":
    selftest()
