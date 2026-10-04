"""Panel aktualizacji: ile pakietów czeka i czy trzeba restartu, na otwartych sesjach.

Plik nie nazywa się `updates.py` — obok jest `update.py` (aktualizacje programu).

Rozsyłanie jak w `multirun.py` (`_MultiRun`, własne `exec_command` per serwer).
Skrypt wypisuje linie `klucz=wartość`, parsowanie to czysta `parse_report()`.
Tylko odczyt, bez `sudo`: `apt-get -s` liczy z cache ostatniego `apt update`,
nie odświeża list (to wymaga roota).
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from i18n import t
from multirun import _MultiRun, run_command

LINUX_CMD = (
    ". /etc/os-release 2>/dev/null; echo \"os=$PRETTY_NAME\"; "
    "if command -v apt-get >/dev/null 2>&1; then "
    "L=$(apt-get -s upgrade 2>/dev/null | grep '^Inst'); echo pm=apt; "
    "echo \"updates=$(printf '%s\\n' \"$L\" | grep -c '^Inst')\"; "
    "echo \"security=$(printf '%s\\n' \"$L\" | grep -ci security)\"; "
    "[ -f /var/run/reboot-required ] && echo reboot=yes || echo reboot=no; "
    "elif P=$(command -v dnf || command -v yum); then echo pm=$(basename $P); "
    "echo \"updates=$($P -q check-update 2>/dev/null | grep -c '^[A-Za-z0-9]')\"; "
    "echo \"security=$($P -q updateinfo list --security 2>/dev/null | grep -c .)\"; "
    "needs-restarting -r >/dev/null 2>&1; [ $? -eq 1 ] && echo reboot=yes || echo reboot=no; "
    "fi"
)
# Bez cudzysłowów w środku (OpenSSH na Windows bywa cmd.exe). Wyszukiwanie
# Windows Update przez SSH bywa zabronione (logowanie sieciowe) — wtedy „?”.
WINDOWS_CMD = (
    "powershell -NoProfile -NonInteractive -Command \""
    "'os=' + (Get-CimInstance Win32_OperatingSystem).Caption; 'pm=windows'; "
    "try { 'updates=' + (New-Object -ComObject Microsoft.Update.Session)"
    ".CreateUpdateSearcher().Search('IsInstalled=0').Updates.Count } catch { 'updates=?' }; "
    "'reboot=' + $(if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
    "\\WindowsUpdate\\Auto Update\\RebootRequired') { 'yes' } else { 'no' })\""
)


def parse_report(text):
    """Linie `klucz=wartość` -> słownik. Śmieci (obcy shell, błędy) pomijane."""
    report = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key in ("os", "pm", "updates", "security", "reboot"):
            report[key] = value.strip()
    return report


def check(client):
    """Zadanie dla `_MultiRun`: -> (ok, surowy raport albo błąd)."""
    text = ""
    for command in (LINUX_CMD, WINDOWS_CMD):
        _, text = run_command(client, command)
        if "pm" in parse_report(text):
            return True, text
    return False, text


class PatchDialog(QDialog):
    """Tabela: serwer, system, aktualizacje, bezpieczeństwa, restart."""

    def __init__(self, parent, targets):
        super().__init__(parent)
        self.targets = targets  # [(nazwa, client), ...]
        self.worker = None
        self.setWindowTitle(t("patches_title"))
        self.resize(820, 360)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            t("patches_col_server"), t("patches_col_os"), t("patches_col_updates"),
            t("patches_col_security"), t("patches_col_reboot"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        self.status = QLabel("")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.refresh_button = buttons.addButton(t("services_refresh"), QDialogButtonBox.ActionRole)
        self.refresh_button.clicked.connect(self.refresh)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        if self.worker is not None:
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.status.setText(t("patches_checking"))
        self.refresh_button.setEnabled(False)
        self.worker = _MultiRun(self.targets, check)
        self.worker.result.connect(self._on_result)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_result(self, name, ok, text):
        report = parse_report(text) if ok else {}
        reboot = {"yes": t("patches_yes"), "no": t("patches_no")}.get(report.get("reboot"), "—")
        os_name = report.get("os") or (report.get("pm") if ok else t("patches_failed", text[:200]))
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = (name, os_name, report.get("updates", "—"), report.get("security", "—"), reboot)
        for col, value in enumerate(cells):
            item = QTableWidgetItem()
            # Liczby jako liczby — sortowanie „9” > „10” jak tekst byłoby złe.
            item.setData(Qt.DisplayRole, int(value) if value.isdigit() else value)
            if report.get("reboot") == "yes" or (value.isdigit() and int(value) and col == 3):
                item.setForeground(QColor(230, 120, 40))
            self.table.setItem(row, col, item)

    def _on_finished(self):
        self.worker = None
        self.refresh_button.setEnabled(True)
        self.status.setText("")
        self.table.setSortingEnabled(True)

    def reject(self):
        if self.worker is not None:  # jak w multirun: czekamy na koniec rundy
            self.status.setText(t("multirun_busy"))
            return
        super().reject()


def selftest():
    apt = "os=Ubuntu 24.04 LTS\npm=apt\nupdates=12\nsecurity=3\nreboot=yes\n"
    assert parse_report(apt) == {
        "os": "Ubuntu 24.04 LTS", "pm": "apt", "updates": "12", "security": "3", "reboot": "yes",
    }, parse_report(apt)
    # Obcy shell (cmd.exe na skrypcie basha) — bez „pm” to nie jest odpowiedź.
    assert "pm" not in parse_report("'.' is not recognized as an internal command\nos=\n")
    assert parse_report("") == {}

    class Fake:
        """Linux: brak odpowiedzi; Windows: raport — `check` ma spróbować obu."""

        def __init__(self):
            self.commands = []

        def exec_command(self, command, timeout=None):
            self.commands.append(command)
            out = b"os=Windows Server 2022\npm=windows\nupdates=?\nreboot=no\n" \
                if command.startswith("powershell") else b"syntax error"

            class Stream:
                def __init__(self, data):
                    self.data = data
                    self.channel = self

                def read(self):
                    return self.data

                def recv_exit_status(self):
                    return 0

            return None, Stream(out), Stream(b"")

    client = Fake()
    ok, text = check(client)
    assert ok and parse_report(text)["pm"] == "windows", text
    assert len(client.commands) == 2, client.commands
    assert '"' not in WINDOWS_CMD[WINDOWS_CMD.index('"') + 1:-1], "cudzysłów w środku łamie cmd.exe"
    print("patches selftest OK")


if __name__ == "__main__":
    selftest()
