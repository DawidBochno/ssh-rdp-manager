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


class _RestrictedHTTPServer(ThreadingHTTPServer):
    """Jak `ThreadingHTTPServer`, ale odrzuca połączenia spoza `allowed_ip`.

    `verify_request` to hak z `socketserver.BaseServer` — wywoływany zanim
    cokolwiek przeczyta z gniazda, więc klient spoza listy nie widzi nawet
    nagłówków odpowiedzi.
    """

    allowed_ip = None

    def verify_request(self, request, client_address):
        return self.allowed_ip is None or client_address[0] == self.allowed_ip


class HttpShare:
    """Udostępnia katalog po HTTP (tylko odczyt — `SimpleHTTPRequestHandler`)."""

    label = "HTTP"

    def __init__(self, directory, port, allowed_ip=None):
        self.directory = directory
        self.port = port
        self._server = _RestrictedHTTPServer(
            ("", port), partial(SimpleHTTPRequestHandler, directory=directory)
        )
        self._server.allowed_ip = allowed_ip
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://{local_ip()}:{self.port}/"

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


def wget_command(url):
    """Gotowa komenda do wklejenia w sesję SSH — admin dopisuje nazwę pliku."""
    return f"wget {url}"


def curl_command(url):
    return f"curl -O {url}"


class TftpShare:
    """Serwer TFTP (RFC 1350) na katalogu — odczyt i zapis, tryb octet.

    Port 69 na Windows/Linux wymaga uprawnień administratora; dlatego port jest
    parametrem i domyślnie proponujemy 6969.
    """

    label = "TFTP"
    BLOCK = 512
    RRQ, WRQ, DATA, ACK, ERROR = 1, 2, 3, 4, 5
    # ponytail: staly limit zamiast liczenia wolnego miejsca na dysku —
    # prosciej, wystarcza jako zapora przed zapelnieniem dysku; podniesc,
    # jesli ktos naprawde bedzie wgrywal wieksze obrazy przez TFTP.
    MAX_UPLOAD_BYTES = 512 * 1024 * 1024

    def __init__(self, directory, port, allowed_ip=None, allow_write=False):
        self.directory = directory
        self.port = port
        self.allowed_ip = allowed_ip
        self.allow_write = allow_write
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
            if self.allowed_ip is not None and client[0] != self.allowed_ip:
                continue  # cicho ignorujemy — inny klient niz dozwolony
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
        with self._session_socket() as session:
            if not self.allow_write:
                self._error(session, client, "Write not allowed")
                return
            path = self._safe_path(name)
            if not path:
                self._error(session, client, "Path outside shared directory")
                return
            aborted = False
            try:
                with open(path, "wb") as handle:
                    expected = 1
                    written = 0
                    session.sendto(struct.pack("!HH", self.ACK, 0), client)
                    while True:
                        packet, _ = session.recvfrom(4 + self.BLOCK)
                        if struct.unpack("!H", packet[:2])[0] != self.DATA:
                            return
                        # Numer bloku klienta trzeba sprawdzic, nie tylko
                        # przyjac — inaczej pogubione/przekrecone pakiety
                        # zapisza sie po cichu jako uszkodzony plik.
                        block = struct.unpack("!H", packet[2:4])[0]
                        if block != expected & 0xFFFF:
                            self._error(session, client, "Unexpected block number")
                            aborted = True
                            return
                        data = packet[4:]
                        written += len(data)
                        if written > self.MAX_UPLOAD_BYTES:
                            self._error(session, client, "File too large")
                            aborted = True
                            return
                        handle.write(data)
                        session.sendto(struct.pack("!HH", self.ACK, block), client)
                        if len(data) < self.BLOCK:
                            return  # ostatni, krotszy blok konczy transfer
                        expected += 1
            except OSError:
                return
            finally:
                if aborted:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

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
    import time
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

            # Domyslnie zapis jest wylaczony — WRQ ma dostac blad, nie cichy
            # zapis do katalogu widocznego dla calej sieci.
            wclient = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            wclient.settimeout(5)
            wclient.sendto(b"\x00\x02nowy.txt\x00octet\x00", ("127.0.0.1", tftp.port))
            packet, _ = wclient.recvfrom(1024)
            assert struct.unpack("!H", packet[:2])[0] == TftpShare.ERROR, packet[:4]
            wclient.close()
            assert not os.path.exists(os.path.join(tmp, "nowy.txt")), "zapis mial byc zablokowany"
        finally:
            tftp.stop()

        # Adres ograniczony do innego IP niz test — pakiety maja byc ignorowane.
        restricted = TftpShare(tmp, 0, allowed_ip="10.255.255.255")
        restricted.port = restricted._socket.getsockname()[1]
        try:
            rclient = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            rclient.settimeout(0.3)
            rclient.sendto(b"\x00\x01plik.txt\x00octet\x00", ("127.0.0.1", restricted.port))
            try:
                rclient.recvfrom(1024)
                raise AssertionError("serwer odpowiedzial klientowi spoza allowed_ip")
            except socket.timeout:
                pass
            rclient.close()
        finally:
            restricted.stop()

        # Z allow_write=True zapis dziala, ale numer bloku i rozmiar sa pilnowane.
        writable = TftpShare(tmp, 0, allow_write=True)
        writable.port = writable._socket.getsockname()[1]
        try:
            wclient = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            wclient.settimeout(5)
            wclient.sendto(b"\x00\x02wgrany.txt\x00octet\x00", ("127.0.0.1", writable.port))
            packet, server = wclient.recvfrom(1024)
            assert struct.unpack("!HH", packet[:4]) == (TftpShare.ACK, 0), packet[:4]
            block = 1
            for offset in range(0, len(payload), TftpShare.BLOCK):
                chunk = payload[offset:offset + TftpShare.BLOCK]
                wclient.sendto(struct.pack("!HH", TftpShare.DATA, block) + chunk, server)
                packet, _ = wclient.recvfrom(1024)
                assert struct.unpack("!HH", packet[:4]) == (TftpShare.ACK, block), packet[:4]
                block += 1
            with open(os.path.join(tmp, "wgrany.txt"), "rb") as handle:
                assert handle.read() == payload, "zapisany plik nie zgadza sie z wyslanym"
            wclient.close()

            # Przekrecony numer bloku ma przerwac transfer i skasowac czesciowy plik.
            wclient = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            wclient.settimeout(5)
            wclient.sendto(b"\x00\x02zly-blok.txt\x00octet\x00", ("127.0.0.1", writable.port))
            packet, server = wclient.recvfrom(1024)
            wclient.sendto(b"\x00\x03\x00\x05" + b"x", server)  # blok 5 zamiast 1
            packet, _ = wclient.recvfrom(1024)
            assert struct.unpack("!H", packet[:2])[0] == TftpShare.ERROR, packet[:4]
            wclient.close()
            # Sprzatanie (os.remove) leci w wątku serwera PO wysłaniu błędu,
            # wiec chwile poczekac, zanim sprawdzimy, ze pliku juz nie ma.
            bad_path = os.path.join(tmp, "zly-blok.txt")
            for _ in range(50):
                if not os.path.exists(bad_path):
                    break
                time.sleep(0.02)
            assert not os.path.exists(bad_path), "zly blok mial skasowac plik"
        finally:
            writable.stop()

        # HTTP: `verify_request` odcina klienta spoza `allowed_ip` zanim cokolwiek wyśle.
        guarded = HttpShare(tmp, 0, allowed_ip="10.0.0.1")
        guarded.port = guarded._server.server_address[1]
        try:
            assert guarded._server.verify_request(None, ("127.0.0.1", 1)) is False
            assert guarded._server.verify_request(None, ("10.0.0.1", 1)) is True
        finally:
            guarded.stop()

    assert wget_command("http://1.2.3.4:8080/") == "wget http://1.2.3.4:8080/"
    assert curl_command("http://1.2.3.4:8080/") == "curl -O http://1.2.3.4:8080/"

    print("servers selftest OK")


if __name__ == "__main__":
    selftest()
