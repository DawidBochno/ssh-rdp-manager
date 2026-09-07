"""Tunele SSH (przekierowanie portów) na istniejącej sesji Paramiko.

Lokalny (`L`): port na naszej maszynie -> host:port widziany przez serwer.
Zdalny (`R`): port na serwerze -> host:port widziany przez nas.

Cała robota to przepompowywanie bajtów między gniazdem a kanałem SSH, więc
idzie na `socketserver` i `select` ze stdliba — żadnej nowej zależności.
Transport jest ten sam, na którym stoi powłoka w zakładce: tunel nie otwiera
drugiego połączenia i znika razem z sesją.
"""
import re
import select
import socket
import socketserver
import threading

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from i18n import t

# „L 8080:10.0.0.5:80" — kierunek, port u nas, cel widziany z drugiej strony.
TUNNEL_RE = re.compile(r"^\s*([LR])\s*[:\s]\s*(\d+)\s*:\s*([^:\s]+)\s*:\s*(\d+)\s*$", re.I)

BUFFER = 4096


def parse_tunnel(text):
    """„L 8080:host:80" -> („L", 8080, „host", 80). None = nie ta składnia.

    Czysta funkcja — stąd asercje w `selftest()`.
    """
    match = TUNNEL_RE.match(text or "")
    if not match:
        return None
    kind, port, host, dest = match.groups()
    port, dest = int(port), int(dest)
    if not (0 < port < 65536 and 0 < dest < 65536):
        return None
    return kind.upper(), port, host, dest


def format_tunnel(spec):
    """Odwrotność `parse_tunnel` — to, co ląduje w `connections.json`."""
    kind, port, host, dest = spec
    return f"{kind} {port}:{host}:{dest}"


def pump(sock, channel):
    """Przepycha bajty w obie strony, aż któraś strona zamknie połączenie."""
    while True:
        try:
            readable, _, _ = select.select([sock, channel], [], [])
        except (OSError, ValueError):
            break
        if sock in readable:
            data = sock.recv(BUFFER)
            if not data:
                break
            channel.sendall(data)
        if channel in readable:
            data = channel.recv(BUFFER)
            if not data:
                break
            sock.sendall(data)


class _TunnelServer(socketserver.ThreadingTCPServer):
    # Bez `allow_reuse_address` ponowne włączenie tunelu po zamknięciu odbijało
    # się o TIME_WAIT; wątki muszą być demonami, żeby zamknięcie aplikacji nie
    # czekało na wiszące połączenie.
    allow_reuse_address = True
    daemon_threads = True


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            channel = self.server.transport.open_channel(
                "direct-tcpip",
                (self.server.dest_host, self.server.dest_port),
                self.request.getpeername(),
            )
        except Exception:
            return
        if channel is None:
            return  # serwer odmówił przekierowania
        try:
            pump(self.request, channel)
        finally:
            channel.close()


class LocalTunnel:
    """`ssh -L`: słuchamy u siebie, ruch wychodzi po stronie serwera."""

    def __init__(self, transport, port, dest_host, dest_port):
        self.server = _TunnelServer(("127.0.0.1", port), _Handler)
        self.server.transport = transport
        self.server.dest_host = dest_host
        self.server.dest_port = dest_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class RemoteTunnel:
    """`ssh -R`: serwer słucha u siebie, ruch wychodzi z naszej maszyny."""

    def __init__(self, transport, port, dest_host, dest_port):
        self.transport = transport
        self.port = transport.request_port_forward("", port)
        self.dest = (dest_host, dest_port)
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop.is_set():
            # Limit czasu, żeby `stop()` nie czekał na kolejne połączenie.
            channel = self.transport.accept(1)
            if channel is not None:
                threading.Thread(target=self._forward, args=(channel,), daemon=True).start()

    def _forward(self, channel):
        try:
            sock = socket.create_connection(self.dest, 10)
        except OSError:
            channel.close()
            return
        try:
            pump(sock, channel)
        finally:
            channel.close()
            sock.close()

    def stop(self):
        self._stop.set()
        try:
            self.transport.cancel_port_forward("", self.port)
        except Exception:
            pass  # zerwana sesja — przekierowania i tak już nie ma


def start_tunnel(transport, spec):
    """Uruchamia tunel opisany krotką z `parse_tunnel`."""
    kind, port, host, dest = spec
    klass = LocalTunnel if kind == "L" else RemoteTunnel
    return klass(transport, port, host, dest)


# --- okno zarządzania tunelami ----------------------------------------------


class TunnelDialog(QDialog):
    """Lista tuneli aktywnej sesji: dodaj, usuń. Zmiany idą też do pliku."""

    def __init__(self, parent, session, on_change=None):
        super().__init__(parent)
        self.session = session
        self.on_change = on_change
        self.setWindowTitle(t("tunnel_title"))
        self.resize(460, 300)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("tunnel_hint")))

        self.list = QListWidget()
        layout.addWidget(self.list)

        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("L 8080:10.0.0.5:80")
        self.entry.returnPressed.connect(self._add)
        row.addWidget(self.entry)
        add = QPushButton(t("tunnel_add"))
        add.clicked.connect(self._add)
        row.addWidget(add)
        remove = QPushButton(t("tunnel_remove"))
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for spec in self.session.tunnels:
            self.list.addItem(format_tunnel(spec))

    def _add(self):
        spec = parse_tunnel(self.entry.text())
        if spec is None:
            QMessageBox.warning(self, t("tunnel_title"), t("tunnel_bad"))
            return
        error = self.session.open_tunnel(spec)
        if error:
            QMessageBox.warning(self, t("tunnel_title"), t("tunnel_error", error))
            return
        self.entry.clear()
        self.refresh()
        if self.on_change:
            self.on_change()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        self.session.close_tunnel(self.session.tunnels[row])
        self.refresh()
        if self.on_change:
            self.on_change()


def selftest():
    assert parse_tunnel("L 8080:10.0.0.5:80") == ("L", 8080, "10.0.0.5", 80)
    assert parse_tunnel("r 9000:localhost:22") == ("R", 9000, "localhost", 22)
    assert parse_tunnel("L:5432:db:5432") == ("L", 5432, "db", 5432)
    assert parse_tunnel("X 80:host:80") is None, "obcy kierunek"
    assert parse_tunnel("L 80:host") is None, "brak portu docelowego"
    assert parse_tunnel("L 99999:host:80") is None, "port poza zakresem"
    assert parse_tunnel("") is None and parse_tunnel(None) is None
    assert format_tunnel(("L", 8080, "host", 80)) == "L 8080:host:80"
    assert parse_tunnel(format_tunnel(("R", 1, "h", 2))) == ("R", 1, "h", 2)

    # Sama pompa: bajty musza przejsc w obie strony i zatrzymac sie na EOF.
    left, right = socket.socketpair()
    other, far = socket.socketpair()
    worker = threading.Thread(target=pump, args=(right, other), daemon=True)
    worker.start()
    left.sendall(b"do serwera")
    assert far.recv(64) == b"do serwera"
    far.sendall(b"z powrotem")
    assert left.recv(64) == b"z powrotem"
    left.close()
    worker.join(2)
    assert not worker.is_alive(), "pompa ma sie skonczyc po zamknieciu gniazda"
    for sock in (right, other, far):
        sock.close()
    print("tunnels selftest OK")


if __name__ == "__main__":
    selftest()
