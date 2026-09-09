"""Menedżer usług: lista z aktywnej sesji SSH plus start/stop/restart.

Ten sam wzorzec „spróbuj obu" co w `_StatsPoller` i skryptach administracyjnych
(`ssh_terminal.SCRIPTS`): przy pierwszym odpytaniu nie wiadomo, co stoi po
drugiej stronie, więc próbujemy Linuksa, a dopiero gdy się nie powiedzie —
Windows. Wariant, który odpowiedział, zostaje zapamiętany na czas otwartego
okna, więc kolejne odświeżenia i akcje nie próbują already-known złej strony.
"""
import shlex

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
from ssh_terminal import _SCRIPT_PARAM_UNSAFE, _try_command

LINUX_LIST_CMD = "systemctl list-units --type=service --all --no-legend --no-pager"
WINDOWS_LIST_CMD = (
    "powershell -NoProfile -NonInteractive -Command \""
    "Get-Service | ForEach-Object { $_.Name + '|' + $_.Status }\""
)

_WIN_VERBS = {
    "start": "Start-Service",
    "stop": "Stop-Service -Force",
    "restart": "Restart-Service -Force",
}


def parse_services(variant, text):
    """Wyjście listowania -> [(nazwa, status), ...]. Czysta funkcja, stąd testy.

    Linux: kolumny `systemctl list-units` rozdzielone spacjami (UNIT LOAD ACTIVE
    SUB opis) — bierzemy ACTIVE, nie SUB, bo to ono mówi „running"/"failed" bez
    rozróżniania podstanów. `●` przed nazwą (jednostki ze statusem failed) nie
    jest częścią nazwy usługi.
    """
    if variant == "windows":
        services = []
        for line in text.splitlines():
            name, sep, status = line.strip().partition("|")
            if sep and name.strip():
                services.append((name.strip(), status.strip()))
        return services
    services = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[0].lstrip("●")
        if name.endswith(".service"):
            services.append((name, parts[2]))
    return services


def action_command(variant, action, name):
    """Polecenie start/stop/restart usługi `name` dla wykrytego wariantu."""
    if variant == "windows":
        verb = _WIN_VERBS[action]
        # W literale PowerShella w apostrofach escapuje się apostrof przez
        # podwojenie (`''`), inaczej "O'Brien" jako nazwa usługi łamie polecenie.
        quoted = name.replace("'", "''")
        return f"powershell -NoProfile -NonInteractive -Command \"{verb} -Name '{quoted}'\""
    return f"sudo systemctl {action} {shlex.quote(name)}"


class ServiceDialog(QDialog):
    """Lista usług aktywnej sesji z przyciskami start/stop/restart."""

    def __init__(self, parent, client):
        super().__init__(parent)
        self.client = client
        self.variant = None  # "linux" / "windows" — ustalane przy pierwszym odświeżeniu
        self.setWindowTitle(t("services_title"))
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([t("services_col_name"), t("services_col_status")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        for label, action in (
            (t("services_start"), "start"),
            (t("services_stop"), "stop"),
            (t("services_restart"), "restart"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked, a=action: self._run_action(a))
            row.addWidget(button)
        refresh = QPushButton(t("services_refresh"))
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.refresh()

    def _list(self):
        variants = [("linux", LINUX_LIST_CMD), ("windows", WINDOWS_LIST_CMD)]
        if self.variant:
            command = LINUX_LIST_CMD if self.variant == "linux" else WINDOWS_LIST_CMD
            variants = [(self.variant, command)]
        for variant, command in variants:
            text = _try_command(self.client, command)
            if text is not None:
                self.variant = variant
                return parse_services(variant, text)
        return None

    def refresh(self):
        services = self._list()
        self.table.setRowCount(0)
        if services is None:
            QMessageBox.warning(self, t("services_title"), t("services_failed"))
            return
        for name, status in services:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(status))

    def _selected_name(self):
        row = self.table.currentRow()
        return self.table.item(row, 0).text() if row >= 0 else None

    def _run_action(self, action):
        name = self._selected_name()
        if not name or not self.variant:
            return
        # Escapowanie apostrofu w PowerShellu (action_command) nie chroni przed
        # warstwa cmd.exe, ktora OpenSSH na Windows uzywa do wykonania calego
        # polecenia — znak '"' przerywa zewnetrzny cudzyslow -Command "..." o
        # poziom wyzej. Prosciej odrzucic niebezpieczne znaki, tak jak
        # run_script() robi dla parametru gotowego skryptu.
        if _SCRIPT_PARAM_UNSAFE.search(name):
            QMessageBox.warning(self, t("services_title"), t("script_param_unsafe"))
            return
        command = action_command(self.variant, action, name)
        text = _try_command(self.client, command)
        if text is None:
            QMessageBox.warning(self, t("services_title"), t("services_action_failed"))
        self.refresh()


def selftest():
    linux_sample = (
        "  sshd.service                  loaded active   running OpenSSH server\n"
        "  ●apache2.service              loaded failed   failed  The Apache HTTP Server\n"
        "  cron.service                  loaded active   running Regular background tasks\n"
    )
    assert parse_services("linux", linux_sample) == [
        ("sshd.service", "active"),
        ("apache2.service", "failed"),
        ("cron.service", "active"),
    ], parse_services("linux", linux_sample)
    assert parse_services("linux", "") == []
    assert parse_services("linux", "junk line, no fields") == []

    windows_sample = "Spooler|Running\nBITS|Stopped\n"
    assert parse_services("windows", windows_sample) == [
        ("Spooler", "Running"), ("BITS", "Stopped"),
    ]

    assert action_command("linux", "restart", "nginx") == "sudo systemctl restart nginx"
    assert action_command("linux", "stop", "my service") == "sudo systemctl stop 'my service'"
    win_cmd = action_command("windows", "restart", "Spooler")
    assert "Restart-Service" in win_cmd and "-Force" in win_cmd and "Spooler" in win_cmd
    assert "Start-Service" in action_command("windows", "start", "Spooler")

    # Apostrof w nazwie usługi nie może wyrwać się z literału PowerShella.
    injected = action_command("windows", "stop", "x'; Remove-Item C:\\ -Force; '")
    assert "-Name 'x''; Remove-Item C:\\ -Force; '''" in injected, injected

    # Cudzyslow wyrywa sie z zewnetrznej warstwy cmd.exe (nie chroni jej
    # escapowanie apostrofu) — taka nazwa ma odpasc jeszcze przed zbudowaniem
    # polecenia, patrz _run_action().
    assert _SCRIPT_PARAM_UNSAFE.search('x" & del C:\\'), "cudzyslow ma odpasc"

    print("services selftest OK")


if __name__ == "__main__":
    selftest()
