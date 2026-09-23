"""Jedno polecenie albo gotowy skrypt na wielu otwartych sesjach SSH naraz.

Każdy serwer dostaje własne `exec_command` na transporcie swojej zakładki —
nie wpisujemy niczego do terminali, więc wynik wraca osobno per serwer,
a powłoki w zakładkach zostają nietknięte.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
)

import notify
from i18n import t
from ssh_terminal import (
    SCRIPTS,
    _SCRIPT_PARAM_UNSAFE,
    _run_commands,
    install_find,
    save_text,
    script_commands,
    script_label,
    terminal_font,
)

# Limit na jeden odczyt wyniku, nie na całe polecenie — cisza dłuższa niż tyle
# kończy się błędem zamiast wiecznego czekania.
COMMAND_TIMEOUT = 60
MAX_PARALLEL = 16


def run_command(client, command):
    """Własne polecenie -> (ok, tekst). Pokazujemy też stderr, o sukcesie decyduje status."""
    try:
        _, stdout, stderr = client.exec_command(command, timeout=COMMAND_TIMEOUT)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        status = stdout.channel.recv_exit_status()
    except Exception as error:
        return False, t("err_prefix", error)
    return status == 0, (out + err).strip() or t("script_no_output")


def make_job(script, text):
    """-> (funkcja client -> (ok, tekst), tekst błędu). `script` None = własne polecenie."""
    text = text.strip()
    if script is None:
        if not text:
            return None, t("multirun_empty")
        return (lambda client: run_command(client, text)), ""
    param = None
    if script.get("prompt"):
        if not text:
            return None, t("multirun_need_param")
        # Ten sam filtr co przy jednym serwerze — parametr idzie do trzech różnych powłok.
        if _SCRIPT_PARAM_UNSAFE.search(text):
            return None, t("script_param_unsafe")
        param = text
    commands = script_commands(script, param)

    def job(client):
        output = _run_commands(client, *commands)
        return output != t("script_failed"), output

    return job, ""


class _MultiRun(QThread):
    """Rozsyła zadanie równolegle; wynik każdego serwera wraca, gdy tylko jest gotowy."""

    result = Signal(str, bool, str)  # nazwa, ok, tekst

    def __init__(self, targets, job):
        super().__init__()
        self.targets = targets  # [(nazwa, client), ...]
        self.job = job

    def run(self):
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(self.targets))) as pool:
            futures = {pool.submit(self.job, client): name for name, client in self.targets}
            for future in as_completed(futures):
                try:
                    ok, text = future.result()
                except Exception as error:  # zadanie łapie swoje błędy, to tylko asekuracja
                    ok, text = False, t("err_prefix", error)
                self.result.emit(futures[future], ok, text)


class MultiRunDialog(QDialog):
    def __init__(self, parent, targets):
        super().__init__(parent)
        self.setWindowTitle(t("multirun_title"))
        self.resize(800, 600)
        self.worker = None
        self._done = self._total = self._failed = 0

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("multirun_servers")))
        self.servers = QListWidget()
        for name, client in targets:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, client)
            self.servers.addItem(item)
        self.servers.setMaximumHeight(140)
        layout.addWidget(self.servers)

        self.script = QComboBox()
        self.script.addItem(t("multirun_own_command"), None)
        for script in SCRIPTS:
            self.script.addItem(script_label(script), script)
        self.script.currentIndexChanged.connect(self._update_input)
        layout.addWidget(self.script)

        self.input = QLineEdit()
        self.input.returnPressed.connect(self._run)
        layout.addWidget(self.input)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(terminal_font())
        install_find(self.output)
        layout.addWidget(self.output)

        self.status = QLabel("")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.run_button = buttons.addButton(t("multirun_run"), QDialogButtonBox.ActionRole)
        self.run_button.clicked.connect(self._run)
        buttons.addButton(t("script_save"), QDialogButtonBox.ActionRole).clicked.connect(
            lambda: save_text(self, self.output.toPlainText(), t("multirun_default_name"),
                              self.windowTitle())
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_input()

    def _update_input(self):
        script = self.script.currentData()
        if script is None:
            self.input.setEnabled(True)
            self.input.setPlaceholderText(t("multirun_command_ph"))
        else:
            self.input.setEnabled(bool(script.get("prompt")))
            self.input.setPlaceholderText(t(script["prompt"]) if script.get("prompt") else "")

    def _checked(self):
        return [
            (item.text(), item.data(Qt.UserRole))
            for item in (self.servers.item(i) for i in range(self.servers.count()))
            if item.checkState() == Qt.Checked
        ]

    def _run(self):
        if self.worker is not None:
            return
        targets = self._checked()
        if not targets:
            self.status.setText(t("multirun_no_targets"))
            return
        job, error = make_job(self.script.currentData(), self.input.text())
        if job is None:
            self.status.setText(error)
            return
        self.output.clear()
        self._done, self._total, self._failed = 0, len(targets), 0
        self._show_progress()
        self.run_button.setEnabled(False)
        self.worker = _MultiRun(targets, job)
        self.worker.result.connect(self._on_result)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _show_progress(self):
        self.status.setText(t("multirun_progress", self._done, self._total, self._failed))

    def _on_result(self, name, ok, text):
        self._done += 1
        self._failed += not ok
        mark = t("multirun_ok") if ok else t("multirun_err")
        self.output.appendPlainText(f"=== {name} — {mark} ===\n{text}\n")
        self._show_progress()

    def _on_finished(self):
        self.worker = None
        self.run_button.setEnabled(True)
        notify.notify(t("notify_title"), t("multirun_progress", self._done, self._total, self._failed))

    def reject(self):
        # ponytail: bez przerywania w pół — zamknięcie czeka na koniec (najwyżej
        # COMMAND_TIMEOUT ciszy na serwer); przerywanie dołożyć, gdy będzie potrzebne.
        if self.worker is not None:
            self.status.setText(t("multirun_busy"))
            return
        super().reject()


def selftest():
    """Walidacja wejścia i rozsyłanie po udawanych klientach — bez sieci."""
    import i18n
    i18n.use("en")
    assert make_job(None, "  ")[0] is None
    restart = next(s for s in SCRIPTS if s.get("prompt"))
    assert make_job(restart, "")[0] is None
    assert make_job(restart, "nginx; rm -rf /")[1] == t("script_param_unsafe")
    assert make_job(restart, "nginx")[0] is not None

    class Fake:
        def __init__(self, name):
            self.name = name

    def job(client):
        if client.name == "zly":
            raise RuntimeError("padł")
        return client.name != "blad", f"wynik {client.name}"

    targets = [(n, Fake(n)) for n in ("a", "b", "blad", "zly")]
    worker = _MultiRun(targets, job)
    got = {}
    worker.result.connect(lambda name, ok, text: got.update({name: (ok, text)}), Qt.DirectConnection)
    worker.run()  # w tym wątku — sygnały idą wprost
    assert got["a"] == (True, "wynik a") and got["b"][0] is True, got
    assert got["blad"] == (False, "wynik blad"), got
    assert got["zly"][0] is False and "padł" in got["zly"][1], "wyjątek ma być wynikiem, nie wywrotką"
    print("multirun selftest OK")
