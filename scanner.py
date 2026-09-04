"""Skaner sieci — lista żywych hostów w podanym zakresie (wzorem Advanced IP Scanner).

Bez nowych zależności: `ping` z systemu, `arp -a` na adresy MAC, `socket` na
sprawdzenie portów i odwrotny DNS. Wątków jest `WORKERS`, bo całość to czekanie
na sieć, nie liczenie — `ThreadPoolExecutor` ze stdliba wystarcza.

Wynik nie jest celem sam w sobie: z tabeli otwiera się sesję SSH albo RDP,
więc skanowanie kończy się tam, gdzie zaczyna się reszta programu.
"""

import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from i18n import t

# Port -> etykieta w kolumnie „usługi”. Lista jest krótka celowo: każdy port to
# osobna próba połączenia, a te sumują się przez cały zakres.
PORTS = {22: "SSH", 3389: "RDP", 80: "HTTP", 443: "HTTPS", 445: "SMB", 21: "FTP"}

WORKERS = 64
MAX_HOSTS = 4096  # zapora przed literówką w rodzaju „10.0.0.0/8”
PING_TIMEOUT_MS = 700
PORT_TIMEOUT = 0.3  # sekundy na jeden port
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_range(text):
    """„192.168.0.1-100”, „192.168.0.0/24”, pojedynczy adres i listy po przecinku."""
    hosts = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            hosts += [str(a) for a in ipaddress.ip_network(part, strict=False).hosts()]
        elif "-" in part:
            start, end = (piece.strip() for piece in part.split("-", 1))
            first = ipaddress.ip_address(start)
            # „192.168.0.1-100” to skrót: po myślniku sam ostatni oktet.
            last = ipaddress.ip_address(
                end if "." in end else ".".join(start.split(".")[:3] + [end])
            )
            if int(last) < int(first):
                raise ValueError(part)
            hosts += [str(ipaddress.ip_address(n)) for n in range(int(first), int(last) + 1)]
        else:
            hosts.append(str(ipaddress.ip_address(part)))
    if len(hosts) > MAX_HOSTS:
        raise ValueError(f"{len(hosts)} > {MAX_HOSTS}")
    return hosts


def parse_arp(text):
    """Adres -> MAC z wyjścia `arp -a`; reszta linii nas nie obchodzi."""
    pairs = re.findall(
        r"(\d+\.\d+\.\d+\.\d+)\s+(?:\w+\s+)?([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})", text
    )
    return {ip: mac.upper().replace("-", ":") for ip, mac in pairs}


def arp_table():
    _, text = _run(("arp", "-a") if sys.platform == "win32" else ("ip", "neigh"))
    return parse_arp(text)


def local_range():
    """Podpowiedź do pola zakresu: cała podsieć /24 spod adresu tej maszyny."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("8.8.8.8", 53))  # nic nie wysyła, tylko wybiera interfejs
            address = probe.getsockname()[0]
        except OSError:
            return "192.168.0.1-254"
    return ".".join(address.split(".")[:3]) + ".1-254"


def _run(args):
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, errors="replace",
            timeout=30, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    return done.returncode, done.stdout + done.stderr


def alive(ip):
    """Czy host odpowiada na ping."""
    if sys.platform == "win32":
        code, text = _run(("ping", "-n", "1", "-w", str(PING_TIMEOUT_MS), ip))
        # Windowsowy ping oddaje 0 także przy „Host docelowy jest nieosiągalny”,
        # więc kod wyjścia nie wystarczy — dopiero „TTL=” znaczy odpowiedź.
        return code == 0 and "TTL=" in text.upper()
    code, _ = _run(("ping", "-c", "1", "-W", str(max(1, PING_TIMEOUT_MS // 1000)), ip))
    return code == 0


def open_ports(ip):
    found = []
    for port in PORTS:
        with socket.socket() as probe:
            probe.settimeout(PORT_TIMEOUT)
            if probe.connect_ex((ip, port)) == 0:
                found.append(port)
    return found


def hostname(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


def details(ip):
    """Nazwa i otwarte porty — jeden wiersz tabeli."""
    ports = open_ports(ip)
    return {
        "ip": ip,
        "name": hostname(ip),
        "ports": ports,
        "services": ", ".join(PORTS[port] for port in ports),
    }


def scan_host(ip):
    """Jeden host: `None` gdy milczy na ping, inaczej słownik do tabeli."""
    return details(ip) if alive(ip) else None


class NetworkScan(QThread):
    """Skanowanie w tle — okno musi zostać klikalne i dać się zatrzymać."""

    found = Signal(dict)
    progress = Signal(int, int)

    def __init__(self, hosts, parent=None):
        super().__init__(parent)
        self.hosts = hosts
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        done = 0
        answered = set()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(self._one, self.hosts):
                done += 1
                self.progress.emit(done, len(self.hosts))
                if result:
                    answered.add(result["ip"])
                    self.found.emit(result)

        # Tablicę ARP czytamy po skanowaniu — pingi zdążyły ją wypełnić.
        # Windows z włączoną zaporą nie odpowiada na ping, ale na ARP owszem,
        # więc bez tej rundy zniknąłby z listy (Advanced IP Scanner robi tak samo).
        table = arp_table()
        silent = [ip for ip in self.hosts if ip in table and ip not in answered]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(self._details, silent):
                if result:
                    self.found.emit(result)
        for ip, mac in table.items():
            self.found.emit({"ip": ip, "mac": mac})

    def _one(self, ip):
        return None if self._stop else scan_host(ip)

    def _details(self, ip):
        return None if self._stop else details(ip)


class ScannerDialog(QDialog):
    """Tabela znalezionych hostów; dwuklik otwiera sesję."""

    COLUMNS = ("scanner_col_name", "scanner_col_ip", "scanner_col_mac", "scanner_col_services")

    def __init__(self, parent, connect_to):
        super().__init__(parent)
        self.connect_to = connect_to  # (adres, protokół, nazwa) -> zakładka
        self.scan = None
        self.rows = {}  # adres -> numer wiersza; MAC dochodzi po skanowaniu
        self.setWindowTitle(t("scanner_title"))
        self.resize(720, 430)

        self.range_field = QLineEdit(local_range())
        self.range_field.returnPressed.connect(self._toggle)
        self.start_btn = QPushButton(t("scanner_start"))
        self.start_btn.clicked.connect(self._toggle)
        top = QHBoxLayout()
        top.addWidget(QLabel(t("scanner_range")))
        top.addWidget(self.range_field)
        top.addWidget(self.start_btn)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([t(key) for key in self.COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemDoubleClicked.connect(self._open_default)

        self.bar = QProgressBar()
        self.bar.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addWidget(self.bar)

    # --- skanowanie ---------------------------------------------------------

    def _toggle(self):
        if self.scan and self.scan.isRunning():
            self.scan.stop()
            return
        try:
            hosts = parse_range(self.range_field.text())
        except ValueError as exc:
            QMessageBox.warning(self, t("scanner_title"), t("scanner_bad_range", exc))
            return
        self.table.setRowCount(0)
        self.rows.clear()
        self.bar.setVisible(True)
        self.start_btn.setText(t("scanner_stop"))
        self.scan = NetworkScan(hosts, self)
        self.scan.found.connect(self._add_row)
        self.scan.progress.connect(self._show_progress)
        self.scan.finished.connect(self._finished)
        self.scan.start()

    def _show_progress(self, done, total):
        self.bar.setMaximum(total)
        self.bar.setValue(done)

    def _finished(self):
        self.bar.setVisible(False)
        self.start_btn.setText(t("scanner_start"))
        self.setWindowTitle(t("scanner_title_done", self.table.rowCount()))

    def _add_row(self, host):
        """Nowy wiersz albo uzupełnienie istniejącego — MAC przychodzi osobno."""
        ip = host["ip"]
        row = self.rows.get(ip)
        if row is None:
            if "services" not in host:
                return  # ARP zna hosta, który nie odpowiedział na ping — pomijamy
            row = self.table.rowCount()
            self.rows[ip] = row
            self.table.insertRow(row)
            for column in range(len(self.COLUMNS)):
                self.table.setItem(row, column, QTableWidgetItem(""))
            self.table.item(row, 1).setData(Qt.UserRole, host)
            self.table.item(row, 0).setText(host["name"])
            self.table.item(row, 1).setText(ip)
            self.table.item(row, 3).setText(host["services"])
        if "mac" in host:
            self.table.item(row, 2).setText(host["mac"])

    # --- otwieranie sesji ---------------------------------------------------

    def _host_at(self, row):
        return self.table.item(row, 1).data(Qt.UserRole)

    def _open_default(self, item):
        """Dwuklik: SSH gdy port 22 otwarty, inaczej RDP, inaczej nic."""
        host = self._host_at(item.row())
        for port, protocol in ((22, "ssh"), (3389, "rdp")):
            if port in host["ports"]:
                self._open(host, protocol)
                return

    def _open(self, host, protocol):
        # Zamykamy się przed otwarciem sesji, żeby formularz i zakładka
        # nie wylądowały pod modalnym oknem skanera.
        self.accept()
        self.connect_to(host["ip"], protocol, host["name"])

    def _menu(self, point):
        item = self.table.itemAt(point)
        if item is None:
            return
        host = self._host_at(item.row())
        menu = QMenu(self)
        menu.addAction(t("scanner_open_ssh"), lambda: self._open(host, "ssh"))
        menu.addAction(t("scanner_open_rdp"), lambda: self._open(host, "rdp"))
        menu.exec(self.table.viewport().mapToGlobal(point))

    def closeEvent(self, event):
        if self.scan and self.scan.isRunning():
            self.scan.stop()
            self.scan.wait()  # inaczej Qt wywala proces
        super().closeEvent(event)


def selftest():
    assert parse_range("192.168.0.5") == ["192.168.0.5"]
    assert parse_range("192.168.0.1-3") == ["192.168.0.1", "192.168.0.2", "192.168.0.3"]
    assert parse_range("192.168.0.1-192.168.0.2") == ["192.168.0.1", "192.168.0.2"]
    assert len(parse_range("192.168.0.0/30")) == 2, "adres sieci i rozgloszeniowy odpadaja"
    assert parse_range("10.0.0.1, 10.0.0.2") == ["10.0.0.1", "10.0.0.2"]
    for bad in ("10.0.0.5-1", "nie-adres", "10.0.0.0/8"):
        try:
            parse_range(bad)
            raise AssertionError(f"{bad} powinno sie wywalic")
        except ValueError:
            pass
    arp = parse_arp(
        "  192.168.0.1           cc-3e-5f-5e-04-fc     dynamiczne\n"
        "  192.168.0.104         00:50:fc:c6:03:2b     dynamiczne\n"
        "  naglowek bez adresu\n"
    )
    assert arp == {"192.168.0.1": "CC:3E:5F:5E:04:FC", "192.168.0.104": "00:50:FC:C6:03:2B"}, arp
    assert local_range().count(".") == 3
    assert set(details("127.0.0.1")) == {"ip", "name", "ports", "services"}
    print("scanner selftest OK")


if __name__ == "__main__":
    selftest()
