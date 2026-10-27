"""Scenariusze na grupie serwerów (ROADMAP #34) — Ansible w wersji lekkiej.

Scenariusz = nazwa + kroki, jedno polecenie na linię (`QSettings["scenarios"]`,
JSON {nazwa: tekst}). Linia z `#` to komentarz, `-` na początku = błąd tego
kroku nie zatrzymuje reszty (jak w Makefile). Serwery idą równolegle, kroki na
serwerze po kolei; pierwszy błąd kończy ten serwer, pozostałe kroki „pominięte”.

Każdy krok to osobne `exec_command` (`multirun.run_command`) — `cd` nie przechodzi
do następnego kroku, stąd `cd /x && polecenie` w jednej linii. `sudo` tylko bez
hasła (`sudo -n`), bo `exec_command` nie ma terminala do pytania.
ponytail: tylko otwarte sesje i ręczne uruchomienie — harmonogram i serwery
z drzewa bez zakładki to drugi etap.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import i18n
import notify
from i18n import t
from multirun import MAX_PARALLEL, run_command
from ssh_terminal import install_find, terminal_font

OK, FAILED, SKIPPED, RUNNING = "ok", "failed", "skipped", "running"
COLORS = {OK: "#2e7d32", FAILED: "#c62828", SKIPPED: "#9e9e9e", RUNNING: "#1565c0"}
OUTPUT_ROLE = Qt.UserRole + 1


def parse_steps(text):
    """Tekst scenariusza -> [(polecenie, ignoruj_błąd)], bez pustych linii i komentarzy."""
    steps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ignore = line.startswith("-")
        command = line[1:].strip() if ignore else line
        if command:
            steps.append((command, ignore))
    return steps


def run_scenario(client, steps, report, stop=None, run=run_command):
    """Kroki po kolei na jednym serwerze; `report(i, ok, tekst)` po każdym.

    -> True, gdy wszystkie kroki przeszły (albo ich błąd był ignorowany).
    """
    for index, (command, ignore) in enumerate(steps):
        if stop is not None and stop.is_set():
            return False
        ok, text = run(client, command)
        report(index, ok, text)
        if not ok and not ignore:
            return False
    return True


def load_scenarios():
    try:
        found = json.loads(i18n.settings().value("scenarios", "{}") or "{}")
    except ValueError:
        return {}
    if not isinstance(found, dict):
        return {}
    return {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, str)}


def save_scenarios(found):
    i18n.settings().setValue("scenarios", json.dumps(found, ensure_ascii=False))


class _ScenarioRun(QThread):
    """Serwery równolegle, kroki po kolei. Sygnały lecą z wątków puli — Qt kolejkuje je do GUI."""

    step = Signal(int, int, bool, str)  # wiersz serwera, krok, ok, tekst
    server_done = Signal(int, bool)

    def __init__(self, targets, steps):
        super().__init__()
        self.targets = targets  # [(nazwa, client), ...]
        self.steps = steps
        self.stop = threading.Event()

    def _one(self, row, client):
        try:
            ok = run_scenario(
                client, self.steps, lambda i, ok, text: self.step.emit(row, i, ok, text), self.stop
            )
        except Exception as error:  # run_command łapie swoje błędy, to tylko asekuracja
            self.step.emit(row, 0, False, t("err_prefix", error))
            ok = False
        self.server_done.emit(row, ok)

    def run(self):
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(self.targets))) as pool:
            for row, (_, client) in enumerate(self.targets):
                pool.submit(self._one, row, client)


class ScenarioDialog(QDialog):
    def __init__(self, parent, targets):
        super().__init__(parent)
        self.setWindowTitle(t("scenario_title"))
        self.resize(1000, 680)
        self.worker = None
        self.scenarios = load_scenarios()
        self._running = []  # serwery bieżącego uruchomienia
        self._results = {}  # wiersz -> ok

        # --- góra: scenariusz i serwery ---
        self.choice = QComboBox()
        self.choice.currentTextChanged.connect(self._show_scenario)
        new = QPushButton(t("scenario_new"))
        new.clicked.connect(self._new)
        save = QPushButton(t("scenario_save"))
        save.clicked.connect(self._save)
        delete = QPushButton(t("scenario_delete"))
        delete.clicked.connect(self._delete)
        row = QHBoxLayout()
        row.addWidget(QLabel(t("scenario_label")))
        row.addWidget(self.choice, 1)
        row.addWidget(new)
        row.addWidget(save)
        row.addWidget(delete)

        self.editor = QPlainTextEdit()
        self.editor.setFont(terminal_font())
        self.editor.setPlaceholderText(t("scenario_hint"))
        self.servers = QListWidget()
        for name, client in targets:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, client)
            self.servers.addItem(item)
        servers_box = QWidget()
        servers_layout = QVBoxLayout(servers_box)
        servers_layout.setContentsMargins(0, 0, 0, 0)
        servers_layout.addWidget(QLabel(t("multirun_servers")))
        servers_layout.addWidget(self.servers)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.editor)
        top.addWidget(servers_box)
        top.setSizes([700, 300])

        # --- dół: tabela serwer × krok i wynik klikniętej komórki ---
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda r, c, *_: self._show_output(r, c))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(terminal_font())
        install_find(self.output)
        bottom = QSplitter(Qt.Vertical)
        bottom.addWidget(self.table)
        bottom.addWidget(self.output)

        main = QSplitter(Qt.Vertical)
        top_box = QWidget()
        top_layout = QVBoxLayout(top_box)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addLayout(row)
        top_layout.addWidget(top)
        main.addWidget(top_box)
        main.addWidget(bottom)
        main.setSizes([300, 380])

        self.status = QLabel("")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.run_button = buttons.addButton(t("scenario_run"), QDialogButtonBox.ActionRole)
        self.run_button.clicked.connect(self._run)
        self.stop_button = buttons.addButton(t("scenario_stop"), QDialogButtonBox.ActionRole)
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setEnabled(False)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(main)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        self._fill_choice()

    # --- zapisane scenariusze ---

    def _fill_choice(self, select=None):
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItems(sorted(self.scenarios))
        self.choice.blockSignals(False)
        if select:
            self.choice.setCurrentText(select)
        self._show_scenario(self.choice.currentText())

    def _show_scenario(self, name):
        self.editor.setPlainText(self.scenarios.get(name, ""))

    def _new(self):
        name, ok = QInputDialog.getText(self, t("scenario_title"), t("scenario_name"))
        name = name.strip()
        if ok and name:
            self.scenarios.setdefault(name, "")
            save_scenarios(self.scenarios)
            self._fill_choice(name)

    def _save(self):
        name = self.choice.currentText()
        if not name:
            self._new()  # pierwszy scenariusz: najpierw nazwa
            name = self.choice.currentText()
            if not name:
                return
        self.scenarios[name] = self.editor.toPlainText()
        save_scenarios(self.scenarios)
        self.status.setText(t("scenario_saved", name))

    def _delete(self):
        name = self.choice.currentText()
        if name and QMessageBox.question(
            self, t("scenario_title"), t("scenario_delete_ask", name)
        ) == QMessageBox.Yes:
            self.scenarios.pop(name, None)
            save_scenarios(self.scenarios)
            self._fill_choice()

    # --- uruchamianie ---

    def _checked(self):
        return [
            (item.text(), item.data(Qt.UserRole))
            for item in (self.servers.item(i) for i in range(self.servers.count()))
            if item.checkState() == Qt.Checked
        ]

    def _run(self):
        if self.worker is not None:
            return
        steps = parse_steps(self.editor.toPlainText())
        if not steps:
            self.status.setText(t("scenario_empty"))
            return
        targets = self._checked()
        if not targets:
            self.status.setText(t("multirun_no_targets"))
            return
        self._running, self._results = targets, {}
        self.table.clear()
        self.table.setRowCount(len(targets))
        self.table.setColumnCount(len(steps))
        self.table.setVerticalHeaderLabels([name for name, _ in targets])
        self.table.setHorizontalHeaderLabels(
            [f"{i + 1}. {command[:30]}" for i, (command, _) in enumerate(steps)]
        )
        for r in range(len(targets)):
            for c in range(len(steps)):
                self._set_cell(r, c, RUNNING if c == 0 else "", "")
        self.output.clear()
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._show_progress()
        self.worker = _ScenarioRun(targets, steps)
        self.worker.step.connect(self._on_step)
        self.worker.server_done.connect(self._on_server_done)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _set_cell(self, row, column, state, text):
        item = QTableWidgetItem(t(f"scenario_state_{state}") if state else "")
        if state:
            item.setForeground(QColor(COLORS[state]))
        item.setData(OUTPUT_ROLE, text)
        self.table.setItem(row, column, item)

    def _on_step(self, row, column, ok, text):
        self._set_cell(row, column, OK if ok else FAILED, text)
        if column + 1 < self.table.columnCount():
            self._set_cell(row, column + 1, RUNNING, "")
        if self.table.currentRow() == row and self.table.currentColumn() == column:
            self._show_output(row, column)

    def _on_server_done(self, row, ok):
        self._results[row] = ok
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is None or item.text() in ("", t("scenario_state_running")):
                self._set_cell(row, column, SKIPPED, "")
        self._show_progress()

    def _show_progress(self):
        failed = sum(1 for ok in self._results.values() if not ok)
        self.status.setText(t("multirun_progress", len(self._results), len(self._running), failed))

    def _show_output(self, row, column):
        item = self.table.item(row, column)
        self.output.setPlainText((item.data(OUTPUT_ROLE) or "") if item else "")

    def _stop(self):
        if self.worker is not None:
            self.worker.stop.set()  # bieżący krok kończy się sam; następne nie ruszają
            self.stop_button.setEnabled(False)

    def _on_finished(self):
        self.worker = None
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        failed = sum(1 for ok in self._results.values() if not ok)
        notify.notify(t("notify_title"), t("multirun_progress", len(self._results), len(self._running), failed))

    def reject(self):
        if self.worker is not None:
            self.status.setText(t("multirun_busy"))
            return
        super().reject()


def selftest():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    steps = parse_steps("# aktualizacja\n\napt update\n- systemctl stop x\n  df -h  \n-\n")
    assert steps == [("apt update", False), ("systemctl stop x", True), ("df -h", False)], steps

    def fake_run(client, command):
        return command not in client, f"{command}!"

    reports = []
    report = lambda i, ok, text: reports.append((i, ok, text))  # noqa: E731
    assert run_scenario(set(), steps, report, run=fake_run) and len(reports) == 3
    reports.clear()
    assert not run_scenario({"apt update"}, steps, report, run=fake_run)
    assert reports == [(0, False, "apt update!")], "błąd kroku zatrzymuje serwer"
    reports.clear()
    assert run_scenario({"systemctl stop x"}, steps, report, run=fake_run)
    assert [ok for _, ok, _ in reports] == [True, False, True], "`-` ignoruje błąd"
    stop = threading.Event()
    stop.set()
    reports.clear()
    assert not run_scenario(set(), steps, report, stop, run=fake_run) and reports == []

    stored = i18n.settings().value("scenarios")
    try:
        i18n.settings().setValue("scenarios", "zepsute")
        assert load_scenarios() == {}
        save_scenarios({"Aktualizacja": "apt update"})
        assert load_scenarios() == {"Aktualizacja": "apt update"}

        # Okno: tabela serwer × krok, błąd zatrzymuje serwer, reszta „pominięta”.
        dialog = ScenarioDialog(None, [("web", "k1"), ("db", "k2")])
        assert dialog.editor.toPlainText() == "apt update"
        dialog.editor.setPlainText("uptime\nfalse\ndf -h")
        dialog.servers.item(1).setCheckState(Qt.Unchecked)
        started = []
        real_start = _ScenarioRun.start
        _ScenarioRun.start = lambda self: started.append(self)
        try:
            dialog._run()
        finally:
            _ScenarioRun.start = real_start
        worker = started[0]
        assert worker.targets == [("web", "k1")] and len(worker.steps) == 3
        assert dialog.table.rowCount() == 1 and dialog.table.columnCount() == 3
        dialog._on_step(0, 0, True, "up 3 days")
        dialog._on_step(0, 1, False, "exit 1")
        dialog._on_server_done(0, False)
        states = [dialog.table.item(0, c).text() for c in range(3)]
        assert states == [t("scenario_state_ok"), t("scenario_state_failed"), t("scenario_state_skipped")], states
        dialog.table.setCurrentCell(0, 1)
        assert dialog.output.toPlainText() == "exit 1"
        assert dialog.status.text() == t("multirun_progress", 1, 1, 1)
        dialog.worker = None
        dialog.deleteLater()
    finally:
        if stored is None:
            i18n.settings().remove("scenarios")
        else:
            i18n.settings().setValue("scenarios", stored)
    print("scenarios selftest OK")


if __name__ == "__main__":
    selftest()
