"""Lista procesów aktywnej sesji z zabijaniem: `ps` / `Get-Process` w tabeli.

Ten sam wzorzec „spróbuj obu" co `services.py`/`disks.py` — przy pierwszym
odświeżeniu Linux, dopiero potem Windows; wariant zapamiętany na czas okna.
"""
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from i18n import t
from ssh_terminal import _try_command

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


def kill_command(variant, pid):
    """PID to liczba (int()), więc nie ma czego cytować."""
    pid = int(pid)
    if variant == "windows":
        return f"powershell -NoProfile -NonInteractive -Command \"Stop-Process -Id {pid} -Force\""
    return f"kill {pid}"


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
        layout.addWidget(self.table)

        row = QHBoxLayout()
        kill = QPushButton(t("processes_kill"))
        kill.clicked.connect(self._kill)
        row.addWidget(kill)
        refresh = QPushButton(t("services_refresh"))
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

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
        rows = self._list()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if rows is None:
            QMessageBox.warning(self, t("processes_title"), t("processes_failed"))
            return
        for values in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for col, value in enumerate(values):
                self.table.setItem(r, col, QTableWidgetItem(str(value)))

    def _kill(self):
        r = self.table.currentRow()
        if r < 0 or not self.variant:
            return
        pid, name = self.table.item(r, 0).text(), self.table.item(r, 4).text()
        if QMessageBox.question(
            self, t("processes_title"), t("processes_kill_confirm", name, pid)
        ) != QMessageBox.Yes:
            return
        if _try_command(self.client, kill_command(self.variant, pid)) is None:
            QMessageBox.warning(self, t("processes_title"), t("processes_kill_failed"))
        self.refresh()


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
    assert "Stop-Process -Id 4 -Force" in kill_command("windows", 4)
    try:
        kill_command("linux", "1; rm -rf /")
        raise AssertionError("PID z doklejonym poleceniem ma odpaść")
    except ValueError:
        pass
    print("processes selftest OK")


if __name__ == "__main__":
    selftest()
