"""Panel dysków i inode'ów: `df -h`/`df -i` w tabeli, czerwony wiersz nad `WARN_PCT`%.

Ten sam wzorzec „spróbuj obu" i ten sam `_try_command` co w `services.py` —
przy pierwszym odświeżeniu próbujemy Linuksa, dopiero potem Windows.
"""

from PySide6.QtGui import QColor
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

WARN_PCT = 90

# Inode'y to koncepcja Linuksa — jedno polecenie, druga część wyjścia
# rozdzielona znacznikiem, tym samym wzorcem co `STATS_CMD` w ssh_terminal.py.
LINUX_CMD = (
    "df -hP -x tmpfs -x devtmpfs -x squashfs 2>/dev/null; "
    "echo @INODES; df -iP -x tmpfs -x devtmpfs -x squashfs 2>/dev/null"
)
WINDOWS_CMD = (
    "powershell -NoProfile -NonInteractive -Command \""
    "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3'"
    " | ForEach-Object { $_.DeviceID+'|'+$_.Size+'|'+$_.FreeSpace }\""
)


def _df_rows(lines):
    """Linie `df -P` bez nagłówka -> [(punkt montowania, % użycia), ...]."""
    rows = []
    for line in lines:
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            pct = float(parts[4].rstrip("%"))
        except ValueError:
            continue
        rows.append((parts[5], pct))
    return rows


def parse_disks(text):
    """Wyjście `LINUX_CMD` -> [{"mount", "size_pct", "inode_pct"}, ...]. None = obcy shell."""
    if "@INODES" not in text:
        return None
    size_part, _, inode_part = text.partition("@INODES")
    size_lines = size_part.strip().splitlines()[1:]  # bez nagłówka `df`
    inode_lines = inode_part.strip().splitlines()[1:]
    if not size_lines:
        return None
    inode_by_mount = dict(_df_rows(inode_lines))
    return [
        {"mount": mount, "size_pct": pct, "inode_pct": inode_by_mount.get(mount)}
        for mount, pct in _df_rows(size_lines)
    ]


def parse_disks_windows(text):
    """Wyjście `WINDOWS_CMD` -> to samo co `parse_disks`, bez kolumny inode'ów."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        device, size_s, free_s = parts
        try:
            size, free = float(size_s), float(free_s)
        except ValueError:
            continue
        pct = 100 * (size - free) / size if size else 0.0
        rows.append({"mount": device, "size_pct": pct, "inode_pct": None})
    return rows or None


class DiskDialog(QDialog):
    """Lista punktów montowania aktywnej sesji, z ostrzeżeniem przy zapełnieniu."""

    def __init__(self, parent, client):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle(t("disks_title"))
        self.resize(480, 360)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [t("disks_col_mount"), t("disks_col_used"), t("disks_col_inodes")]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        row = QHBoxLayout()
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
        text = _try_command(self.client, LINUX_CMD)
        rows = parse_disks(text) if text is not None else None
        if rows is not None:
            return rows
        text = _try_command(self.client, WINDOWS_CMD)
        return parse_disks_windows(text) if text is not None else None

    def refresh(self):
        rows = self._list()
        self.table.setRowCount(0)
        if not rows:
            QMessageBox.warning(self, t("disks_title"), t("disks_failed"))
            return
        for entry in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(entry["mount"]))
            self.table.setItem(r, 1, QTableWidgetItem(f"{entry['size_pct']:.0f}%"))
            inode_pct = entry["inode_pct"]
            inode_text = f"{inode_pct:.0f}%" if inode_pct is not None else "—"
            self.table.setItem(r, 2, QTableWidgetItem(inode_text))
            if entry["size_pct"] >= WARN_PCT or (inode_pct or 0) >= WARN_PCT:
                for col in range(3):
                    self.table.item(r, col).setBackground(QColor("#c0392b"))


def selftest():
    sample = (
        "Filesystem     Size  Used Avail Use% Mounted on\n"
        "/dev/sda1        20G   18G  1.0G  95% /\n"
        "tmpfs             2G    0B    2G   0% /run\n"
        "@INODES\n"
        "Filesystem      Inodes  IUsed   IFree IUse% Mounted on\n"
        "/dev/sda1       1310720 500000 810720   38% /\n"
    )
    rows = parse_disks(sample)
    assert rows == [
        {"mount": "/", "size_pct": 95.0, "inode_pct": 38.0},
        {"mount": "/run", "size_pct": 0.0, "inode_pct": None},
    ], rows
    assert parse_disks("brak znacznika") is None

    win_rows = parse_disks_windows("C:|500000000000|50000000000\n")
    assert win_rows[0]["mount"] == "C:" and round(win_rows[0]["size_pct"]) == 90, win_rows
    print("disks selftest OK")


if __name__ == "__main__":
    selftest()
