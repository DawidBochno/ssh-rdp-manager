"""Panel Docker: kontenery aktywnej sesji SSH, start/stop/restart, logi.

Plik nie nazywa się `docker.py`, bo przykrywałby pakiet `docker` z pip.

Klient `docker` ma tę samą składnię na Linuksie i Windows, więc zamiast
wariantu Linux/Windows (jak w `services.py`) próbujemy `docker`, a potem
`sudo -n docker` (użytkownik spoza grupy `docker`). Zapamiętany zostaje ten,
który odpowiedział. Format w cudzysłowach, nie apostrofach — `cmd.exe`
(OpenSSH na Windows) apostrofów nie rozumie, a bash w `"{{.ID}}"` nic nie rozwija.
"""
import re
import shlex

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from i18n import t
from ssh_terminal import SUDO_NO_PASSWORD, failed_text, in_background, run_command

PREFIXES = ("docker ", SUDO_NO_PASSWORD + "docker ")
LIST_ARGS = 'ps -a --format "{{.ID}}|{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}|{{.Ports}}"'
STATS_ARGS = 'stats --no-stream --format "{{.ID}}|{{.CPUPerc}}|{{.MemUsage}}"'
LOG_LINES = 200
_ID_RE = re.compile(r"^[0-9a-f]{6,64}$")


def parse_containers(text):
    """`docker ps` -> [(id, nazwa, obraz, stan, status, porty), ...]."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 6 and _ID_RE.match(parts[0]):
            rows.append(tuple(parts))
    return rows


def parse_stats(text):
    """`docker stats` -> {id: (cpu, ram)}. Stats pokazuje skrócone ID, jak `ps`."""
    stats = {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and _ID_RE.match(parts[0]):
            stats[parts[0]] = (parts[1], parts[2])
    return stats


def action_command(prefix, action, container_id):
    """Polecenie start/stop/restart/logs. ID z wyjścia serwera — sprawdzamy, nie ufamy."""
    if not _ID_RE.match(container_id):
        raise ValueError(container_id)
    if action == "logs":
        return f"{prefix}logs --tail {LOG_LINES} {container_id} 2>&1"
    return f"{prefix}{action} {shlex.quote(container_id)}"


class DockerDialog(QDialog):
    """Tabela kontenerów z przyciskami start/stop/restart/logi."""

    def __init__(self, parent, client):
        super().__init__(parent)
        self.client = client
        self.prefix = None  # "docker " albo "sudo -n docker " — ustalane przy odświeżeniu
        self.setWindowTitle(t("docker_title"))
        self.resize(860, 420)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            t("docker_col_name"), t("docker_col_image"), t("docker_col_status"),
            t("docker_col_cpu"), t("docker_col_ram"), t("docker_col_ports"),
        ])
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
            (t("docker_logs"), "logs"),
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
        layout.addWidget(buttons)

        QTimer.singleShot(0, self.refresh)  # po pokazaniu okna, jak w services.py

    def _list(self):
        """-> (kontenery, statystyki) albo (None, błąd serwera)."""
        error = ""
        for prefix in ([self.prefix] if self.prefix else PREFIXES):
            text, error = run_command(self.client, prefix + LIST_ARGS)
            if text is not None:
                self.prefix = prefix
                stats_text, _ = run_command(self.client, prefix + STATS_ARGS)
                return parse_containers(text), parse_stats(stats_text or "")
        return None, error

    def refresh(self):
        containers, stats = in_background(self, self._list)
        self.table.setRowCount(0)
        if containers is None:
            QMessageBox.warning(self, t("docker_title"), failed_text(t("docker_failed"), stats))
            return
        for cid, name, image, _state, status, ports in containers:
            cpu, ram = stats.get(cid, ("—", "—"))
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate((name, image, status, cpu, ram, ports)):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, cid)
                self.table.setItem(row, col, item)

    def _run_action(self, action):
        row = self.table.currentRow()
        if row < 0 or not self.prefix:
            return
        cid = self.table.item(row, 0).data(Qt.UserRole)
        name = self.table.item(row, 0).text()
        if action in ("stop", "restart") and QMessageBox.question(
            self, t("docker_title"), t("docker_confirm", t(f"services_{action}"), name)
        ) != QMessageBox.Yes:
            return
        command = action_command(self.prefix, action, cid)
        text, error = in_background(self, lambda: run_command(self.client, command))
        if text is None:
            QMessageBox.warning(self, t("docker_title"), failed_text(t("docker_action_failed"), error))
        elif action == "logs":
            self._show_logs(name, text)
            return
        self.refresh()

    def _show_logs(self, name, text):
        dialog = QDialog(self)
        dialog.setWindowTitle(t("docker_logs_title", name))
        dialog.resize(800, 500)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        view.moveCursor(view.textCursor().End)
        layout.addWidget(view)
        dialog.exec()


def selftest():
    sample = (
        "a1b2c3d4e5f6|web|nginx:latest|running|Up 2 hours|0.0.0.0:80->80/tcp\n"
        "0123456789ab|db|postgres:16|exited|Exited (0) 3 days ago|\n"
        "Cannot connect to the Docker daemon\n"
    )
    assert parse_containers(sample) == [
        ("a1b2c3d4e5f6", "web", "nginx:latest", "running", "Up 2 hours", "0.0.0.0:80->80/tcp"),
        ("0123456789ab", "db", "postgres:16", "exited", "Exited (0) 3 days ago", ""),
    ], parse_containers(sample)
    assert parse_containers("") == []

    stats = parse_stats("a1b2c3d4e5f6|0.15%|12.5MiB / 1.9GiB\n")
    assert stats == {"a1b2c3d4e5f6": ("0.15%", "12.5MiB / 1.9GiB")}, stats

    assert action_command("docker ", "restart", "a1b2c3d4e5f6") == "docker restart a1b2c3d4e5f6"
    assert action_command(PREFIXES[1], "stop", "a1b2c3d4e5f6") == "sudo -n docker stop a1b2c3d4e5f6"
    assert action_command("docker ", "logs", "a1b2c3d4e5f6").startswith("docker logs --tail 200 ")
    try:
        action_command("docker ", "stop", "x; rm -rf /")
        raise AssertionError("ID spoza [0-9a-f] ma odpaść")
    except ValueError:
        pass

    print("containers selftest OK")


if __name__ == "__main__":
    selftest()
