"""Telnet i port szeregowy (COM) w tym samym terminalu co SSH.

Przełączniki, routery, zasilacze UPS: konsola po Telnecie albo kablem
konsolowym. Terminal (`SshTerminal` — VT100, podświetlanie, log sesji, szukanie,
siatka) jest ten sam; podmieniamy tylko „kanał”: obiekt z `recv`/`send`/
`resize_pty`/`close`, czyli to, czego terminal używa z kanału Paramiko.
Bez klienta SSH nie ma statystyk, SFTP ani tuneli — narzędzia z menu sprawdzają
`SessionTab`, więc taką zakładkę po prostu pomijają.
"""

import re
import socket
import struct
import threading

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ssh_terminal import SshTerminal, _Reader

TELNET_PORT = 23
BAUD_RATES = (9600, 19200, 38400, 57600, 115200)
# ponytail: zawsze 8N1 bez kontroli przepływu — tak ma 99% konsol sprzętu
# sieciowego; parzystość/bity stopu w formularzu, gdy trafi się wyjątek.

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ECHO, SGA, TTYPE, NAWS = 1, 3, 24, 31


def naws_bytes(width, height):
    """Rozmiar okna (RFC 1073); bajt 255 w liczbach trzeba podwoić."""
    size = struct.pack(">HH", width, height).replace(b"\xff", b"\xff\xff")
    return bytes([IAC, SB, NAWS]) + size + bytes([IAC, SE])


class TelnetFilter:
    """Wyłuskuje negocjację Telnetu (IAC …) z bajtów serwera, resztę oddaje terminalowi.

    Zgadzamy się tylko na to, co rozumiemy: serwer robi echo i tłumi GA (tryb
    znakowy), my podajemy typ terminala i rozmiar okna. Na resztę odmowa.
    Niedokończona sekwencja na końcu kawałka czeka na następny.
    """

    HIS = {ECHO, SGA}         # serwer może (WILL) — odpowiadamy DO
    OURS = {SGA, TTYPE, NAWS}  # serwer prosi nas (DO) — odpowiadamy WILL

    def __init__(self):
        self._buf = b""
        self._agreed = set()  # ("his"/"ours", opcja) — bez tego dwa WILL dałyby pętlę odpowiedzi
        self.size = (100, 30)

    @property
    def naws(self):
        return ("ours", NAWS) in self._agreed

    def feed(self, data):
        """bytes od serwera -> (bajty dla terminala, odpowiedź do wysłania serwerowi)."""
        buf = self._buf + data
        out, reply = bytearray(), bytearray()
        i = 0
        while i < len(buf):
            if buf[i] != IAC:
                j = buf.find(bytes([IAC]), i)
                j = len(buf) if j < 0 else j
                out += buf[i:j]
                i = j
                continue
            if i + 1 >= len(buf):
                break
            cmd = buf[i + 1]
            if cmd == IAC:
                out.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buf):
                    break
                reply += self._answer(cmd, buf[i + 2])
                i += 3
            elif cmd == SB:
                end = buf.find(bytes([IAC, SE]), i + 2)
                if end < 0:
                    break
                if buf[i + 2:i + 4] == bytes([TTYPE, 1]):  # SEND — pytanie o typ terminala
                    reply += bytes([IAC, SB, TTYPE, 0]) + b"XTERM" + bytes([IAC, SE])
                i = end + 2
            else:
                i += 2  # NOP, GA, AYT… — nic do zrobienia
        self._buf = bytes(buf[i:])
        return bytes(out), bytes(reply)

    def _answer(self, cmd, option):
        side = "his" if cmd in (WILL, WONT) else "ours"
        key = (side, option)
        if cmd in (WONT, DONT):
            if key not in self._agreed:
                return b""
            self._agreed.discard(key)
            return bytes([IAC, DONT if cmd == WONT else WONT, option])
        if key in self._agreed:
            return b""  # już ustalone — nie odpowiadamy drugi raz
        wanted = self.HIS if side == "his" else self.OURS
        if option not in wanted:
            return bytes([IAC, DONT if cmd == WILL else WONT, option])
        self._agreed.add(key)
        answer = bytes([IAC, DO if cmd == WILL else WILL, option])
        return answer + (naws_bytes(*self.size) if key == ("ours", NAWS) else b"")


class TelnetChannel:
    """Gniazdo Telnetu udające kanał Paramiko dla `SshTerminal`."""

    # Koniec od serwera to zwykłe zakończenie sesji, nie zerwanie do ponawiania.
    exit_status = 0

    def __init__(self, sock, label):
        self.sock, self.label = sock, label
        self.closed = False
        self.filter = TelnetFilter()
        self._lock = threading.Lock()  # piszą GUI (klawisze) i czytnik (odpowiedzi IAC)

    def _send_raw(self, data):
        with self._lock:
            self.sock.sendall(data)

    def recv(self, size):
        while True:
            data = self.sock.recv(size)
            if not data:
                return b""
            text, reply = self.filter.feed(data)
            if reply:
                self._send_raw(reply)
            if text:
                return text

    def send(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        data = data.replace(b"\xff", b"\xff\xff")
        # Enter w terminalu to samo CR; Telnet chce CR LF (jak PuTTY).
        self._send_raw(re.sub(rb"\r(?!\n)", b"\r\n", data))

    def resize_pty(self, width, height):
        self.filter.size = (width, height)
        if self.filter.naws:
            self._send_raw(naws_bytes(width, height))

    def close(self):
        self.closed = True
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()  # zamknięcie z innego wątku wybija czytnik z recv()
            except OSError:
                pass


class SerialChannel:
    """Port COM (pyserial) udający kanał Paramiko."""

    exit_status = 0

    def __init__(self, port, label):
        self.port, self.label = port, label
        self.closed = False

    def recv(self, size):
        # Odczyt z limitem czasu zwraca b"" bez danych — dla `_Reader` to byłby
        # koniec sesji, więc czekamy do skutku albo zamknięcia.
        while not self.closed:
            try:
                data = self.port.read(max(1, min(size, self.port.in_waiting)))
            except Exception:  # wyjęty kabel/adapter USB, zamknięcie z GUI
                return b""
            if data:
                return data
        return b""

    def send(self, data):
        self.port.write(data.encode("utf-8") if isinstance(data, str) else data)

    def resize_pty(self, **_):
        pass  # konsola szeregowa nie zna rozmiaru okna

    def close(self):
        self.closed = True
        try:
            self.port.close()
        except Exception:
            pass


def open_telnet(host, port, timeout=10):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(None)
    return TelnetChannel(sock, f"{host}:{port}")


def open_serial(name, baud):
    import serial  # pyserial — import tu, żeby brak biblioteki nie wywracał startu

    return SerialChannel(serial.Serial(name, int(baud), timeout=0.2), name)


def serial_ports():
    """Nazwy portów COM w systemie (do podpowiedzi w formularzu)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return sorted(port.device for port in list_ports.comports())


class RawTerminal(SshTerminal):
    """`SshTerminal` na kanale Telnet/COM: tylko czytnik, bez klienta i statystyk."""

    def __init__(self, channel, parent=None):
        super().__init__(None, channel, parent)

    def _start_io(self, client, channel):
        self.client, self.channel, self.host = None, channel, channel.label
        self._pty_size = (0, 0)
        self.reader = _Reader(channel)
        self.reader.received.connect(self._append)
        self.reader.finished_session.connect(self._on_closed)
        self.reader.start()

    def _close_client(self):
        self.channel.close()
        self.reader.wait(2000)


class RawTab(QWidget):
    """Zakładka Telnet/COM — sam terminal (bez SFTP), interfejs jak `SessionTab`."""

    last_stats = ""

    def __init__(self, terminal, parent=None):
        super().__init__(parent)
        self.terminal = terminal
        self.split = None  # SplitTab, w którym terminal akurat jest (split.py)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(terminal)

    def close_session(self):
        self.terminal.close_session()


def selftest():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    # Negocjacja: zgoda na echo/SGA serwera, typ terminala i rozmiar od nas,
    # odmowa reszty, IAC IAC to bajt 255, niedokończona sekwencja czeka.
    f = TelnetFilter()
    text, reply = f.feed(bytes([IAC, WILL, ECHO, IAC, DO, NAWS, IAC, DO, 39]) + b"login: \xff\xff" + bytes([IAC]))
    assert text == b"login: \xff", text
    assert reply == bytes([IAC, DO, ECHO, IAC, WILL, NAWS]) + naws_bytes(100, 30) + bytes([IAC, WONT, 39]), reply
    text, reply = f.feed(bytes([WILL, ECHO]) + b"x")
    assert (text, reply) == (b"x", b""), "druga zgoda na to samo = pętla odpowiedzi"
    text, reply = f.feed(bytes([IAC, SB, TTYPE, 1, IAC]))
    assert (text, reply) == (b"", b""), "podnegocjacja bez końca musi poczekać"
    text, reply = f.feed(bytes([SE]) + b"$ ")
    assert text == b"$ " and reply == bytes([IAC, SB, TTYPE, 0]) + b"XTERM" + bytes([IAC, SE]), reply
    assert naws_bytes(255, 24) == bytes([IAC, SB, NAWS, 0, 255, 255, 0, 24, IAC, SE])

    # Prawdziwe gniazdo: odpowiedzi idą do serwera same, terminal widzi tylko tekst,
    # Enter = CR LF, bajt 255 podwojony.
    server, client = socket.socketpair()
    channel = TelnetChannel(client, "test")
    server.sendall(bytes([IAC, WILL, ECHO]) + b"Username: ")
    assert channel.recv(1024) == b"Username: "
    assert server.recv(64) == bytes([IAC, DO, ECHO])
    channel.send("admin\r")
    channel.send(b"\xff")
    assert server.recv(64) == b"admin\r\n\xff\xff"

    # Terminal na tym kanale: dane od serwera trafiają na ekran, zamknięcie
    # zakładki kończy czytnik (bez wiszącego wątku przy zamykaniu programu).
    terminal = RawTerminal(channel)
    server.sendall(b"Switch> ")
    for _ in range(50):
        app.processEvents()
        if "Switch>" in terminal.toPlainText():
            break
        terminal.reader.wait(20)
    assert "Switch>" in terminal.toPlainText(), terminal.toPlainText()
    assert terminal.send_text("show ver\r") and server.recv(64) == b"show ver\r\n"
    tab = RawTab(terminal)
    tab.close_session()
    assert terminal.reader.isFinished(), "czytnik wisi po zamknięciu"
    server.close()
    tab.deleteLater()

    # Port COM: pętla zwrotna pyserial zamiast kabla.
    try:
        import serial
    except ImportError:
        print("rawterm selftest OK (brak pyserial — pominięto COM)")
        return
    loop = SerialChannel(serial.serial_for_url("loop://", timeout=0.2), "loop")
    loop.send("AT\r")
    assert loop.recv(64) == b"AT\r"
    loop.close()
    assert loop.recv(64) == b"", "zamknięty port ma kończyć czytnik"
    print("rawterm selftest OK")


if __name__ == "__main__":
    selftest()
