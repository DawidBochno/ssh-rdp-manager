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
import ssl
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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

import notify
from i18n import t

# Port -> etykieta w kolumnie „usługi”. Lista jest krótka celowo: każdy port to
# osobna próba połączenia, a te sumują się przez cały zakres.
PORTS = {22: "SSH", 3389: "RDP", 80: "HTTP", 443: "HTTPS", 445: "SMB", 21: "FTP"}

WORKERS = 64
MAX_HOSTS = 4096  # zapora przed literówką w rodzaju „10.0.0.0/8”
PING_TIMEOUT_MS = 700
PORT_TIMEOUT = 0.3  # sekundy na jeden port
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_mac(text):
    """Adres MAC z dowolnego zapisu (`:`, `-`, bez separatora) jako 6 bajtów."""
    digits = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(digits) != 12:
        raise ValueError(text)
    return bytes.fromhex(digits)


def magic_packet(mac):
    """Ramka Wake-on-LAN: 6 x 0xFF, potem adres MAC szesnaście razy (AMD MP)."""
    return b"\xff" * 6 + parse_mac(mac) * 16


def wake(mac, broadcast="255.255.255.255", port=9):
    """Budzi maszynę magicznym pakietem. Zwraca liczbę wysłanych bajtów."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        return sock.sendto(magic_packet(mac), (broadcast, port))


def split_host_port(text, default=443):
    """„host" albo „host:port" — adres IPv6 w nawiasach nas tu nie interesuje."""
    host, _, port = text.strip().partition(":")
    return host, int(port) if port else default


def cert_info(host, port=443, timeout=5):
    """Certyfikat TLS serwera: podmiot, wystawca, data ważności, dni do końca.

    Kontekst bez weryfikacji — chodzi o *odczytanie* certyfikatu (także
    samopodpisanego, także już wygasłego), a nie o zaufanie serwerowi.
    Weryfikujący kontekst zerwałby połączenie dokładnie w tych przypadkach,
    dla których to narzędzie powstało.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    # Nieweryfikujący kontekst oddaje pusty słownik, więc pola czytamy z DER-a.
    # ponytail: `_test_decode_cert` to prywatna funkcja CPythona — jedyny sposób
    # rozebrania certyfikatu bez `cryptography`. Gdyby zniknęła z nowej wersji
    # Pythona, dołożyć tę zależność; do tego czasu nie warto jej ciągnąć.
    decode = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decode is None:
        raise ValueError("brak dekodera certyfikatow w tej wersji Pythona")
    cert = decode(_write_temp_pem(der))
    expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
    return {
        "subject": _name(cert.get("subject")),
        "issuer": _name(cert.get("issuer")),
        "expires": expires.strftime("%Y-%m-%d %H:%M"),
        "days_left": (expires - datetime.now(timezone.utc).replace(tzinfo=None)).days,
    }


def _write_temp_pem(der):
    """`_test_decode_cert` czyta wyłącznie z pliku PEM — stąd plik tymczasowy."""
    path = Path(tempfile.gettempdir()) / "ssh-rdp-manager-cert.pem"
    path.write_text(ssl.DER_cert_to_PEM_cert(der), encoding="ascii")
    return str(path)


def _name(fields):
    """Nazwa wyróżniająca z krotek OpenSSL-a na jedną linię."""
    return ", ".join(f"{key}={value}" for group in (fields or ()) for key, value in group)


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
    phase = Signal(str)  # klucz tłumaczenia etapu — pokazywany nad paskiem

    def __init__(self, hosts, parent=None):
        super().__init__(parent)
        self.hosts = hosts
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        done = 0
        answered = set()
        self.phase.emit("scanner_phase_ping")
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
        self.phase.emit("scanner_phase_arp")
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

        # Pasek postępu z licznikiem hostów i etykietą etapu: bez tego skanowanie
        # dużego zakresu wygląda jak zawieszone okno, zwłaszcza w rundzie ARP,
        # która nie posuwa licznika do przodu.
        self.status = QLabel(t("scanner_status", t("scanner_phase_idle"), 0))
        self.bar = QProgressBar()
        self.bar.setFormat(t("scanner_progress"))
        self.bar.setVisible(False)
        self.phase_key = "scanner_phase_idle"

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addWidget(self.status)
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
        self.scan.phase.connect(self._show_phase)
        self.scan.finished.connect(self._finished)
        self.scan.start()

    def _show_progress(self, done, total):
        self.bar.setMaximum(total)
        self.bar.setValue(done)

    def _show_phase(self, key):
        self.phase_key = key
        self._refresh_status()

    def _refresh_status(self):
        self.status.setText(t("scanner_status", t(self.phase_key), self.table.rowCount()))

    def _finished(self):
        self.bar.setVisible(False)
        self.phase_key = "scanner_phase_idle"
        self._refresh_status()
        self.start_btn.setText(t("scanner_start"))
        self.setWindowTitle(t("scanner_title_done", self.table.rowCount()))
        notify.notify(t("notify_title"), t("notify_scan_done", self.table.rowCount()))

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
            self._refresh_status()
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
        row = item.row()
        host = self._host_at(row)
        menu = QMenu(self)
        menu.addAction(t("scanner_open_ssh"), lambda: self._open(host, "ssh"))
        menu.addAction(t("scanner_open_rdp"), lambda: self._open(host, "rdp"))
        # MAC mamy z tablicy ARP, więc uśpioną maszynę budzimy wprost z listy.
        mac = self.table.item(row, 2).text()
        if mac:
            menu.addAction(t("scanner_wake"), lambda: wake_dialog(self, mac))

        # Podmenu „Kopiuj” składa się z kolumn — nowa kolumna dopisuje się sama.
        copy_menu = menu.addMenu(t("scanner_copy"))
        copy_menu.addAction(t("scanner_copy_all"), lambda: self._copy(row))
        copy_menu.addSeparator()
        for column, key in enumerate(self.COLUMNS):
            copy_menu.addAction(t(key), lambda _checked=False, c=column: self._copy(row, c))

        menu.exec(self.table.viewport().mapToGlobal(point))

    def _copy(self, row, column=None):
        """Jedna komórka albo cały wiersz (kolumny rozdzielone tabulatorem)."""
        if column is None:
            text = "	".join(
                self.table.item(row, c).text() for c in range(len(self.COLUMNS))
            )
        else:
            text = self.table.item(row, column).text()
        QGuiApplication.clipboard().setText(text)
        return text

    def closeEvent(self, event):
        if self.scan and self.scan.isRunning():
            self.scan.stop()
            self.scan.wait()  # inaczej Qt wywala proces
        super().closeEvent(event)


def wake_dialog(parent, mac=None):
    """Wake-on-LAN z menu albo z wiersza tabeli (wtedy MAC jest już znany)."""
    if mac is None:
        mac, ok = QInputDialog.getText(parent, t("menu_wol"), t("wol_prompt"))
        if not ok or not mac.strip():
            return
        mac = mac.strip()
    try:
        wake(mac)
    except ValueError:
        QMessageBox.warning(parent, t("menu_wol"), t("wol_bad_mac", mac))
        return
    except OSError as error:
        QMessageBox.warning(parent, t("menu_wol"), t("wol_failed", error))
        return
    QMessageBox.information(parent, t("menu_wol"), t("wol_sent", mac))


def cert_dialog(parent):
    """Pyta o host i pokazuje datę ważności certyfikatu TLS."""
    text, ok = QInputDialog.getText(parent, t("tls_title"), t("tls_prompt"))
    if not ok or not text.strip():
        return
    try:
        host, port = split_host_port(text)
        info = cert_info(host, port)
    except (OSError, ValueError, ssl.SSLError) as error:
        QMessageBox.warning(parent, t("tls_title"), t("tls_error", error))
        return
    QMessageBox.information(
        parent,
        t("tls_title"),
        t("tls_result", f"{host}:{port}", info["subject"], info["issuer"],
          info["expires"], info["days_left"]),
    )


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

    # Wake-on-LAN: ramka to 6 x 0xFF i szesnaście powtórzeń adresu.
    packet = magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102, len(packet)
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff")
    assert packet[-6:] == packet[6:12], "MAC musi sie powtarzac do konca ramki"
    assert magic_packet("aa-bb-cc-dd-ee-ff") == packet, "separator nie ma znaczenia"
    assert magic_packet("aabbccddeeff") == packet
    for bad in ("AA:BB:CC:DD:EE", "nie-mac", ""):
        try:
            magic_packet(bad)
            raise AssertionError(f"{bad} powinno sie wywalic")
        except ValueError:
            pass

    # Rozbicie „host:port" — brak portu daje domyslny.
    assert split_host_port("example.com") == ("example.com", 443)
    assert split_host_port(" example.com:8443 ") == ("example.com", 8443)

    print("scanner selftest OK")


if __name__ == "__main__":
    selftest()
