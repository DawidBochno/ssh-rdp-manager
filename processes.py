"""Lista procesów aktywnej sesji z zabijaniem: `ps` / `Get-Process` w tabeli.

Ten sam wzorzec „spróbuj obu" co `services.py`/`disks.py` — przy pierwszym
odświeżeniu Linux, dopiero potem Windows; wariant zapamiętany na czas okna.
"""
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from i18n import t
from ssh_terminal import (
    SUDO_NO_PASSWORD,
    SUDO_STDIN,
    _try_command,
    failed_text,
    in_background,
    run_command,
)

LINUX_CMD = "ps -eo pid,user,pcpu,pmem,comm --sort=-pcpu --no-headers | head -300"
# Windows nie ma % CPU chwilowego w Get-Process — pokazujemy sekundy CPU i MB pamięci.
WINDOWS_CMD = (
    "powershell -NoProfile -NonInteractive -Command \""
    "Get-Process | Sort-Object CPU -Descending | Select-Object -First 300"
    " | ForEach-Object { [string]$_.Id+'|'+[math]::Round($_.CPU,1)+'|'"
    "+[math]::Round($_.WorkingSet64/1MB)+'|'+$_.ProcessName }\""
)


def parse_processes(variant, text):
    """Wyjście listowania -> [(pid, użytkownik, cpu, pamięć, nazwa), ...]."""
    rows = []
    for line in text.splitlines():
        if variant == "windows":
            parts = line.strip().split("|", 3)
            if len(parts) == 4 and parts[0].isdigit():
                rows.append((int(parts[0]), "", parts[1] + " s", parts[2] + " MB", parts[3]))
            continue
        parts = line.split(None, 4)
        if len(parts) == 5 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1], parts[2] + "%", parts[3] + "%", parts[4]))
    return rows


def kill_command(variant, pid, force=False, sudo=""):
    """PID to liczba (int()), więc nie ma czego cytować.

    `force` = SIGKILL na Linuksie; `Stop-Process -Force` na Windows i tak
    nie pyta procesu o zgodę, więc tam oba warianty są jednym poleceniem.
    `sudo` = przedrostek z `SUDO_*` (tylko Linux; Windows nie ma sudo).
    """
    pid = int(pid)
    if variant == "windows":
        return f"powershell -NoProfile -NonInteractive -Command \"Stop-Process -Id {pid} -Force\""
    return f"{sudo}kill -9 {pid}" if force else f"{sudo}kill {pid}"


def sort_key(text):
    """Liczba z początku komórki („12.5%”, „140 MB”, PID) albo sam tekst.

    Krotka, żeby liczby i teksty w jednej kolumnie dało się porównać:
    liczby najpierw, tekst potem.
    """
    match = re.match(r"\s*(\d+(?:\.\d+)?)", text)
    return (0, float(match.group(1)), "") if match else (1, 0.0, text.lower())


class _SortItem(QTableWidgetItem):
    """Sortowanie po wartości, nie po tekście — inaczej „9%” > „10%”."""

    def __lt__(self, other):
        return sort_key(self.text()) < sort_key(other.text())


class ProcessDialog(QDialog):
    def __init__(self, parent, client):
        super().__init__(parent)
        self.client = client
        self.variant = None
        self.setWindowTitle(t("processes_title"))
        self.resize(640, 460)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "PID", t("processes_col_user"), "CPU", t("processes_col_mem"),
            t("processes_col_name"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        # Klik w nagłówek sortuje; na start po CPU malejąco, jak `ps --sort=-pcpu`.
        self.table.horizontalHeader().setSortIndicator(2, Qt.DescendingOrder)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        kill = QPushButton(t("processes_kill"))
        kill.clicked.connect(lambda: self._kill(False))
        row.addWidget(kill)
        force = QPushButton(t("processes_kill_force"))
        force.clicked.connect(lambda: self._kill(True))
        row.addWidget(force)
        self.sudo = QCheckBox(t("processes_sudo"))
        row.addWidget(self.sudo)
        refresh = QPushButton(t("services_refresh"))
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QTimer.singleShot(0, self.refresh)  # po pokazaniu okna, jak w services.py

    def _list(self):
        variants = [("linux", LINUX_CMD), ("windows", WINDOWS_CMD)]
        if self.variant:
            variants = [v for v in variants if v[0] == self.variant]
        for variant, command in variants:
            text = _try_command(self.client, command)
            rows = parse_processes(variant, text) if text is not None else []
            if rows:
                self.variant = variant
                return rows
        return None

    def refresh(self):
        rows = in_background(self, self._list)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if rows is None:
            QMessageBox.warning(self, t("processes_title"), t("processes_failed"))
            return
        for values in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for col, value in enumerate(values):
                self.table.setItem(r, col, _SortItem(str(value)))
        # Włączenie sortowania układa wiersze wg bieżącego wskaźnika w nagłówku.
        self.table.setSortingEnabled(True)

    def _kill(self, force=False):
        r = self.table.currentRow()
        if r < 0 or not self.variant:
            return
        pid, name = self.table.item(r, 0).text(), self.table.item(r, 4).text()
        if QMessageBox.question(
            self, t("processes_title"), t("processes_kill_confirm", name, pid)
        ) != QMessageBox.Yes:
            return
        ok, error = self._run_kill(pid, force)
        if not ok:
            QMessageBox.warning(
                self, t("processes_title"), failed_text(t("processes_kill_failed"), error)
            )
        self.refresh()

    def _command(self, command, stdin_text=None):
        """-> (udało się, błąd z serwera); polecenie w tle, okno żyje."""
        text, error = in_background(self, lambda: run_command(self.client, command, stdin_text))
        return text is not None, error

    def _run_kill(self, pid, force):
        if not (self.sudo.isChecked() and self.variant == "linux"):
            return self._command(kill_command(self.variant, pid, force))
        # Najpierw bez hasła (NOPASSWD w sudoers), dopiero potem pytamy.
        if self._command(kill_command("linux", pid, force, SUDO_NO_PASSWORD))[0]:
            return True, ""
        user = self.client.get_transport().get_username() or ""
        password, ok = QInputDialog.getText(
            self, t("processes_title"), t("sudo_prompt", user), QLineEdit.Password
        )
        if not ok:
            return True, ""  # anulowane — bez komunikatu o błędzie
        return self._command(kill_command("linux", pid, force, SUDO_STDIN), password + "\n")


def selftest():
    linux = "    1 root      0.0  0.1 systemd\n 4242 www-data 12.5  3.2 nginx: worker\nśmieci\n"
    assert parse_processes("linux", linux) == [
        (1, "root", "0.0%", "0.1%", "systemd"),
        (4242, "www-data", "12.5%", "3.2%", "nginx: worker"),
    ], parse_processes("linux", linux)
    assert parse_processes("windows", "4|12.3|140|System\nxx\n") == [
        (4, "", "12.3 s", "140 MB", "System"),
    ]
    assert kill_command("linux", "4242") == "kill 4242"
    assert kill_command("linux", 4242, force=True) == "kill -9 4242"
    assert "Stop-Process -Id 4 -Force" in kill_command("windows", 4, force=True)
    assert kill_command("linux", 7, True, SUDO_NO_PASSWORD) == "sudo -n kill -9 7"
    assert kill_command("linux", 7, False, SUDO_STDIN) == "sudo -S -p '' kill 7"

    # Sortowanie po wartości: 9% przed 10%, liczby przed tekstem.
    cells = ["10.0%", "9.5%", "140 MB", "nginx", "2"]
    assert sorted(cells, key=sort_key) == ["2", "9.5%", "10.0%", "140 MB", "nginx"], \
        sorted(cells, key=sort_key)
    assert "Stop-Process -Id 4 -Force" in kill_command("windows", 4)
    try:
        kill_command("linux", "1; rm -rf /")
        raise AssertionError("PID z doklejonym poleceniem ma odpaść")
    except ValueError:
        pass
    print("processes selftest OK")


if __name__ == "__main__":
    selftest()
