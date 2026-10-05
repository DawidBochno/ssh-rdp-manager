"""Szukanie frazy w logach na wielu otwartych sesjach SSH naraz (grep po grupie).

Rozsyłanie jak w `multirun.py` (`_MultiRun`, własne `exec_command` per serwer).
Tylko Linux — logi Windows to nie pliki tekstowe. Plik bez prawa odczytu
(grep kończy się kodem 2) dostaje drugą próbę przez `sudo -n`. Pokazujemy
ostatnie trafienia (najnowsze), nie pierwsze.
"""
import re
import shlex

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from i18n import t
from multirun import _MultiRun, run_command
from ssh_terminal import install_find, save_text, terminal_font

JOURNAL = "journalctl"
SOURCES = (
    "/var/log/syslog", "/var/log/messages", "/var/log/auth.log",
    "/var/log/nginx/*.log", "/var/log/apache2/*.log", JOURNAL,
)
# Ścieżka idzie bez cytowania (żeby `*.log` działało), więc tylko bezpieczne znaki.
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./*?-]+$")
_RC_RE = re.compile(r"^#rc=(\d+)$", re.M)
MAX_SCAN = 20000  # górny limit trafień na plik, zanim `tail` weźmie końcówkę


def build_command(phrase, source, ignore_case=True, last=100):
    """-> polecenie powłoki albo None (pusta fraza / niedozwolona ścieżka)."""
    if not phrase or (source != JOURNAL and not _PATH_RE.match(source)):
        return None
    flags = "-i " if ignore_case else ""
    grep = f"grep -F {flags}-m {MAX_SCAN} -e {shlex.quote(phrase)}"
    if source == JOURNAL:
        search = f"journalctl --no-pager -q --since -24h | {grep}"
    else:
        search = f"{grep} -- {source}"
    return (
        f"out=$({search} 2>&1); rc=$?; "
        # sudo bez hasła też kończy się kodem 1 („a password is required”) —
        # wynik sudo bierzemy tylko, gdy wygląda jak grep (0, albo 1 i cisza).
        f"if [ $rc -eq 2 ]; then o2=$(sudo -n {search} 2>&1); r2=$?; "
        f"if [ $r2 -eq 0 ] || {{ [ $r2 -eq 1 ] && [ -z \"$o2\" ]; }}; "
        f"then out=$o2; rc=$r2; fi; fi; "
        f"printf '%s\\n' \"$out\" | tail -n {int(last)}; echo \"#rc=$rc\""
    )


def parse_result(text):
    """Wyjście polecenia -> (ok, linie albo tekst błędu). grep: 0 = są, 1 = brak, 2 = błąd."""
    match = None
    for match in _RC_RE.finditer(text):
        pass
    if match is None:
        return False, text.strip()  # nie bash (np. Windows) albo zerwane połączenie
    body = text[:match.start()].strip()
    rc = int(match.group(1))
    if rc == 0:
        return True, body.splitlines()
    if rc == 1:
        return True, []
    return False, body or f"exit {rc}"


class LogSearchDialog(QDialog):
    def __init__(self, parent, targets):
        super().__init__(parent)
        self.setWindowTitle(t("logsearch_title"))
        self.resize(900, 640)
        self.worker = None
        self._done = self._total = self._found = 0

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("multirun_servers")))
        self.servers = QListWidget()
        for name, client in targets:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, client)
            self.servers.addItem(item)
        self.servers.setMaximumHeight(120)
        layout.addWidget(self.servers)

        row = QHBoxLayout()
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText(t("logsearch_phrase_ph"))
        self.phrase.returnPressed.connect(self._run)
        row.addWidget(self.phrase, 2)
        self.source = QComboBox()
        self.source.setEditable(True)
        self.source.addItems(SOURCES)
        self.source.setToolTip(t("logsearch_source_tip"))
        row.addWidget(self.source, 2)
        self.ignore_case = QCheckBox(t("logsearch_ignore_case"))
        self.ignore_case.setChecked(True)
        row.addWidget(self.ignore_case)
        row.addWidget(QLabel(t("logsearch_last")))
        self.last = QSpinBox()
        self.last.setRange(10, 5000)
        self.last.setValue(100)
        row.addWidget(self.last)
        layout.addLayout(row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(terminal_font())
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        install_find(self.output)
        layout.addWidget(self.output)
        self.status = QLabel("")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.run_button = buttons.addButton(t("logsearch_run"), QDialogButtonBox.ActionRole)
        self.run_button.clicked.connect(self._run)
        buttons.addButton(t("script_save"), QDialogButtonBox.ActionRole).clicked.connect(
            lambda: save_text(self, self.output.toPlainText(), "logi.txt", self.windowTitle())
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _run(self):
        if self.worker is not None:
            return
        targets = [
            (item.text(), item.data(Qt.UserRole))
            for item in (self.servers.item(i) for i in range(self.servers.count()))
            if item.checkState() == Qt.Checked
        ]
        if not targets:
            self.status.setText(t("multirun_no_targets"))
            return
        command = build_command(
            self.phrase.text(), self.source.currentText().strip(),
            self.ignore_case.isChecked(), self.last.value(),
        )
        if command is None:
            self.status.setText(t("logsearch_invalid"))
            return
        self.output.clear()
        self._done, self._total, self._found = 0, len(targets), 0
        self.run_button.setEnabled(False)
        self.status.setText(t("logsearch_searching"))
        self.worker = _MultiRun(targets, lambda client: run_command(client, command))
        self.worker.result.connect(self._on_result)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_result(self, name, _ok, text):
        self._done += 1
        ok, lines = parse_result(text)
        if not ok:
            self.output.appendPlainText(f"=== {name} — {t('multirun_err')} ===\n{lines}\n")
        else:
            self._found += bool(lines)
            header = t("logsearch_hits", len(lines)) if lines else t("logsearch_none")
            self.output.appendPlainText(f"=== {name} — {header} ===\n" + "\n".join(lines) + "\n")
        self.status.setText(t("logsearch_progress", self._done, self._total, self._found))

    def _on_finished(self):
        self.worker = None
        self.run_button.setEnabled(True)

    def reject(self):
        if self.worker is not None:  # jak w multirun: czekamy na koniec rundy
            self.status.setText(t("multirun_busy"))
            return
        super().reject()


def selftest():
    assert build_command("", "/var/log/syslog") is None, "pusta fraza"
    assert build_command("x", "/var/log/a; rm -rf /") is None, "ścieżka idzie bez cytowania"
    assert build_command("x", "var/log/syslog") is None, "tylko ścieżka bezwzględna"
    command = build_command("it's; rm", "/var/log/nginx/*.log", last=50)
    assert "-e 'it'\"'\"'s; rm'" in command and "-- /var/log/nginx/*.log" in command, command
    assert "-i " in command and "tail -n 50" in command
    assert "-i " not in build_command("x", "/var/log/syslog", ignore_case=False)
    assert build_command("x", JOURNAL).startswith("out=$(journalctl")

    assert parse_result("a error\nb error\n#rc=0\n") == (True, ["a error", "b error"])
    assert parse_result("\n#rc=1\n") == (True, [])
    assert parse_result("grep: /x: No such file or directory\n#rc=2\n") == (
        False, "grep: /x: No such file or directory")
    assert parse_result("'out' is not recognized")[0] is False, "obcy shell"
    # Linia logu wyglądająca jak znacznik nie myli parsera — liczy się ostatni.
    assert parse_result("#rc=7\nline\n#rc=0\n") == (True, ["#rc=7", "line"])

    print("logsearch selftest OK")


if __name__ == "__main__":
    selftest()
