"""Wbudowane serwery (wzorem „Embedded servers" z MobaXterm).

Chodzi o daemony po *naszej* stronie: admin siedzi przy tym oknie, a zdalny
serwer ma skądś pobrać plik. Zamiast stawiać cokolwiek na zdalnej maszynie
uruchamiamy usługę lokalnie i z sesji SSH robimy `wget http://<mój-ip>:8080/…`.

Bez nowych zależności: HTTP to `http.server` ze standardowej biblioteki,
TFTP to ~50 linii na `socket` (RFC 1350) — sprzęt sieciowy zwykle nie umie nic
innego. Oba chodzą w wątkach demonach, więc nie blokują GUI.
"""

import os
import socket
import struct
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def local_ip():
    """Adres, którym widzi nas zdalny serwer — nie `127.0.0.1`."""
    try:
        # Gniazdo UDP nic nie wysyła; wystarczy, że system wybierze interfejs.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 53))
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class HttpShare:
    """Udostępnia katalog po HTTP (tylko odczyt — `SimpleHTTPRequestHandler`)."""

    label = "HTTP"

    def __init__(self, directory, port):
        self.directory = directory
        self.port = port
        self._server = ThreadingHTTPServer(
            ("", port), partial(SimpleHTTPRequestHandler, directory=directory)
        )
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://{local_ip()}:{self.port}/"

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


class TftpShare:
    """Serwer TFTP (RFC 1350) na katalogu — odczyt i zapis, tryb octet.

    Port 69 na Windows/Linux wymaga uprawnień administratora; dlatego port jest
    parametrem i domyślnie proponujemy 6969.
    """

    label = "TFTP"
    BLOCK = 512
    RRQ, WRQ, DATA, ACK, ERROR = 1, 2, 3, 4, 5

    def __init__(self, directory, port):
        self.directory = directory
        self.port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(("", port))
        self._running = True
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self):
        return f"tftp://{local_ip()}:{self.port}/"

    def _safe_path(self, name):
        """Nie wypuszczamy klienta poza udostępniony katalog (`../../etc/passwd`)."""
        path = os.path.realpath(os.path.join(self.directory, name.lstrip("/\\")))
        root = os.path.realpath(self.directory)
        return path if path == root or path.startswith(root + os.sep) else None

    def _serve(self):
        while self._running:
            try:
                packet, client = self._socket.recvfrom(1024)
            except OSError:
                return  # gniazdo zamknięte przez stop()
            if len(packet) < 4:
                continue
            opcode = struct.unpack("!H", packet[:2])[0]
            if opcode not in (self.RRQ, self.WRQ):
                continue
            name = packet[2:].split(b"\x00")[0].decode("utf-8", "replace")
            handler = self._send_file if opcode == self.RRQ else self._receive_file
            threading.Thread(target=handler, args=(client, name), daemon=True).start()

    def _session_socket(self):
        """Transfer idzie z nowego portu — tak działa TFTP, port 69 tylko przyjmuje."""
        session = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        session.settimeout(5)
        return session

    @staticmethod
    def _error(session, client, message):
        session.sendto(
            struct.pack("!HH", TftpShare.ERROR, 1) + message.encode() + b"\x00", client
        )

    def _send_file(self, client, name):
        path = self._safe_path(name)
        with self._session_socket() as session:
            if not path or not os.path.isfile(path):
                self._error(session, client, "File not found")
                return
            try:
                with open(path, "rb") as handle:
                    block = 1
                    while True:
                        chunk = handle.read(self.BLOCK)
                        session.sendto(
                            struct.pack("!HH", self.DATA, block & 0xFFFF) + chunk, client
                        )
                        reply, _ = session.recvfrom(1024)
                        if struct.unpack("!H", reply[:2])[0] != self.ACK:
                            return
                        if len(chunk) < self.BLOCK:
                            return  # ostatni, krótszy blok kończy transfer
                        block += 1
            except OSError:
                return

    def _receive_file(self, client, name):
        path = self._safe_path(name)
        with self._session_socket() as session:
            if not path:
                self._error(session, client, "Path outside shared directory")
                return
            try:
                with open(path, "wb") as handle:
                    block = 0
                    while True:
                        session.sendto(struct.pack("!HH", self.ACK, block & 0xFFFF), client)
                        packet, _ = session.recvfrom(4 + self.BLOCK)
                        if struct.unpack("!H", packet[:2])[0] != self.DATA:
                            return
                        block = struct.unpack("!H", packet[2:4])[0]
                        handle.write(packet[4:])
                        if len(packet) - 4 < self.BLOCK:
                            session.sendto(
                                struct.pack("!HH", self.ACK, block & 0xFFFF), client
                            )
                            return
            except OSError:
                return

    def stop(self):
        self._running = False
        self._socket.close()


# ponytail: brak retransmisji po timeout i brak numerów bloków > 65535
# (plik ponad 32 MB). Dla „podaj plik do routera po LAN" wystarcza;
# przy zawodnej sieci albo dużych obrazach przenieść na tftpy.

# `label` to klucz tlumaczenia z i18n.py, nie gotowy napis — menu tlumaczy
# go dopiero przy budowaniu, po wczytaniu wybranego jezyka.
SERVERS = [
    {"label": "srv_http", "cls": HttpShare, "port": 8080},
    {"label": "srv_tftp", "cls": TftpShare, "port": 6969},
]


def selftest():
    import tempfile
    import urllib.request

    with tempfile.TemporaryDirectory() as tmp:
        payload = b"x" * 1500  # ponad dwa bloki TFTP — sprawdza numerowanie
        with open(os.path.join(tmp, "plik.txt"), "wb") as handle:
            handle.write(payload)

        http = HttpShare(tmp, 0)
        http.port = http._server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{http.port}/plik.txt") as page:
                assert page.read() == payload, "HTTP oddał co innego niż plik"
        finally:
            http.stop()

        tftp = TftpShare(tmp, 0)
        tftp.port = tftp._socket.getsockname()[1]
        try:
            # Odczyt: RRQ, potem ACK po każdym bloku.
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.settimeout(5)
            client.sendto(b"\x00\x01plik.txt\x00octet\x00", ("127.0.0.1", tftp.port))
            received = b""
            while True:
                packet, server = client.recvfrom(1024)
                assert struct.unpack("!H", packet[:2])[0] == TftpShare.DATA, packet[:4]
                received += packet[4:]
                client.sendto(b"\x00\x04" + packet[2:4], server)
                if len(packet) - 4 < TftpShare.BLOCK:
                    break
            assert received == payload, "TFTP oddał co innego niż plik"

            # Wyjście poza katalog musi dostać błąd, nie zawartość.
            client.sendto(b"\x00\x01../../tajne\x00octet\x00", ("127.0.0.1", tftp.port))
            packet, _ = client.recvfrom(1024)
            assert struct.unpack("!H", packet[:2])[0] == TftpShare.ERROR, packet[:4]
            client.close()
            assert tftp._safe_path("../tajne") is None, "ucieczka z katalogu przepuszczona"
            assert tftp._safe_path("plik.txt"), "zwykły plik odrzucony"
        finally:
            tftp.stop()

    print("servers selftest OK")


if __name__ == "__main__":
    selftest()
