"""Podgląd VNC w zakładce — własny klient protokołu RFB (RFC 6143).

Qt nie ma kontrolki VNC, a gotowe biblioteki Pythona to albo zrzuty ekranu,
albo cały program. Sam protokół jest mały: powitanie, logowanie, obraz
prostokątami, mysz i klawiatura jako krótkie komunikaty.

ponytail: kodowanie Raw + CopyRect — w sieci lokalnej wystarcza, przez WAN
pełny ekran 1080p to ~8 MB; Tight/ZRLE dołożyć, gdy ktoś zgłosi przycinanie.
Logowanie: brak hasła albo klasyczne hasło VNC (DES). Bez szyfrowania obrazu
(jak w większości klientów VNC) — przez internet tylko w tunelu SSH.
"""

import socket
import struct
import threading

from PySide6.QtCore import QRect, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from i18n import t

VNC_PORT = 5900

ENC_RAW, ENC_COPYRECT, ENC_DESKTOP_SIZE = 0, 1, -223

# Klawisze Qt -> keysym X11 (tym VNC nazywa klawisze).
_SPECIAL = {
    Qt.Key_Backspace: 0xff08, Qt.Key_Tab: 0xff09, Qt.Key_Backtab: 0xff09,
    Qt.Key_Return: 0xff0d, Qt.Key_Enter: 0xff0d, Qt.Key_Escape: 0xff1b,
    Qt.Key_Insert: 0xff63, Qt.Key_Delete: 0xffff, Qt.Key_Home: 0xff50,
    Qt.Key_End: 0xff57, Qt.Key_PageUp: 0xff55, Qt.Key_PageDown: 0xff56,
    Qt.Key_Left: 0xff51, Qt.Key_Up: 0xff52, Qt.Key_Right: 0xff53, Qt.Key_Down: 0xff54,
    Qt.Key_Shift: 0xffe1, Qt.Key_Control: 0xffe3, Qt.Key_Alt: 0xffe9,
    Qt.Key_Meta: 0xffeb, Qt.Key_CapsLock: 0xffe5,
}
KEY_CTRL, KEY_ALT, KEY_DELETE = 0xffe3, 0xffe9, 0xffff


def keysym(key, text):
    """Keysym dla zdarzenia klawisza albo None (klawisz, którego nie wysyłamy)."""
    if key in _SPECIAL:
        return _SPECIAL[key]
    if Qt.Key_F1 <= key <= Qt.Key_F12:
        return 0xffbe + key - Qt.Key_F1
    if text and text.isprintable():
        code = ord(text[0])
        return code if code < 0x100 else 0x01000000 | code  # „ą” itd. — keysym Unicode
    if Qt.Key_Space <= key <= Qt.Key_AsciiTilde:
        return ord(chr(key).lower())  # Ctrl+C: tekst to „\x03”, serwer chce „c” + Ctrl
    return None


def _des(key, data):
    """DES na sposób VNC: każdy bajt klucza z odwróconą kolejnością bitów."""
    from cryptography.hazmat.primitives.ciphers import Cipher, modes
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:  # starsze cryptography
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
    encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()  # 3× ten sam klucz = DES
    return encryptor.update(data) + encryptor.finalize()


def vnc_response(password, challenge):
    """Odpowiedź na 16-bajtowe wyzwanie: hasło (max 8 znaków) jako klucz DES."""
    return _des(password.encode("latin-1", "replace")[:8].ljust(8, b"\0"), challenge)


def fit_rect(width, height, fb_width, fb_height):
    """Gdzie narysować obraz w widżecie: skalowany z zachowaniem proporcji, na środku."""
    if not fb_width or not fb_height:
        return QRect()
    scale = min(width / fb_width, height / fb_height)
    w, h = int(fb_width * scale), int(fb_height * scale)
    return QRect((width - w) // 2, (height - h) // 2, w, h)


def to_fb(x, y, rect, fb_width, fb_height):
    """Punkt widżetu -> piksel ekranu zdalnego (przycięty do ekranu)."""
    if rect.isEmpty():
        return 0, 0
    fx = (x - rect.x()) * fb_width // rect.width()
    fy = (y - rect.y()) * fb_height // rect.height()
    return min(max(fx, 0), fb_width - 1), min(max(fy, 0), fb_height - 1)


class VncClient(QThread):
    """Połączenie RFB: powitanie i odbiór obrazu w tle; mysz/klawisze z GUI."""

    frame = Signal(object, int, int)  # bajty BGRX, szerokość, wysokość
    clipboard = Signal(str)
    ended = Signal(str)  # powód końca; "" = zamknięte przez nas

    def __init__(self, sock, password=""):
        super().__init__()
        self.sock, self.password = sock, password or ""
        self.closed = False
        self.name = ""
        self.width = self.height = 0
        self.fb = bytearray()
        self._send_lock = threading.Lock()  # piszą wątek (prośby o obraz) i GUI (mysz, klawisze)
        self._shown = threading.Event()  # GUI pokazał klatkę — dopiero wtedy prosimy o następną

    # --- gniazdo ---------------------------------------------------------

    def _recv(self, size):
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError(t("vnc_closed_by_server"))
            data += chunk
        return bytes(data)

    def _send(self, data):
        with self._send_lock:
            self.sock.sendall(data)

    def _reason(self):
        length = struct.unpack(">I", self._recv(4))[0]
        return self._recv(min(length, 4096)).decode("utf-8", "replace")

    # --- protokół ----------------------------------------------------------

    def handshake(self):
        version = self._recv(12)
        if not version.startswith(b"RFB "):
            raise ValueError(t("vnc_not_vnc"))
        major, minor = int(version[4:7]), int(version[8:11])
        minor = 8 if (major, minor) >= (3, 8) else 7 if (major, minor) >= (3, 7) else 3
        self._send(b"RFB 003.%03d\n" % minor)
        if minor == 3:
            types = [struct.unpack(">I", self._recv(4))[0]]
        else:
            count = self._recv(1)[0]
            types = list(self._recv(count)) if count else [0]
        if types == [0]:
            raise PermissionError(self._reason())
        # Hasło podane -> VNC auth, jeśli serwer zna; inaczej bez hasła.
        if 2 in types and (self.password or 1 not in types):
            chosen = 2
        elif 1 in types:
            chosen = 1
        else:
            raise ValueError(t("vnc_unsupported_auth", ", ".join(map(str, types))))
        if minor != 3:
            self._send(bytes([chosen]))
        if chosen == 2:
            self._send(vnc_response(self.password, self._recv(16)))
        if chosen == 2 or minor == 8:
            if struct.unpack(">I", self._recv(4))[0]:
                raise PermissionError(self._reason() if minor == 8 else t("vnc_auth_failed"))
        self._send(b"\x01")  # ClientInit: współdzielony — nie wyrzucaj innych podglądających
        self.width, self.height = struct.unpack(">HH", self._recv(4))
        self._recv(16)  # format serwera — i tak narzucamy własny
        self.name = self._reason()
        self.fb = bytearray(self.width * self.height * 4)
        # 32 bity, little endian, 0x00RRGGBB = BGRX w pamięci = QImage.Format_RGB32.
        self._send(struct.pack(">BxxxBBBBHHHBBBxxx", 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        encodings = (ENC_COPYRECT, ENC_RAW, ENC_DESKTOP_SIZE)
        self._send(struct.pack(">BxH", 2, len(encodings)) + struct.pack(f">{len(encodings)}i", *encodings))
        self._request(incremental=False)

    def _request(self, incremental=True):
        self._send(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, self.width, self.height))

    def _check(self, x, y, w, h):
        # Prostokąt spoza ekranu zmieniłby rozmiar bufora (przypisanie wycinka) —
        # od serwera przyjmujemy tylko to, co się mieści.
        if x + w > self.width or y + h > self.height:
            raise ValueError(t("vnc_bad_data"))

    def read_message(self):
        kind = self._recv(1)[0]
        if kind == 0:  # FramebufferUpdate
            count = struct.unpack(">xH", self._recv(3))[0]
            for _ in range(count):
                x, y, w, h, encoding = struct.unpack(">HHHHi", self._recv(12))
                if encoding == ENC_DESKTOP_SIZE:
                    self.width, self.height = w, h
                    self.fb = bytearray(w * h * 4)
                    continue
                self._check(x, y, w, h)
                if encoding == ENC_RAW:
                    self._blit(x, y, w, h, self._recv(w * h * 4))
                elif encoding == ENC_COPYRECT:
                    sx, sy = struct.unpack(">HH", self._recv(4))
                    self._check(sx, sy, w, h)
                    self._blit(x, y, w, h, b"".join(self._row(sx, sy + row, w) for row in range(h)))
                else:
                    raise ValueError(t("vnc_bad_data"))
            self._shown.clear()
            self.frame.emit(bytes(self.fb), self.width, self.height)
            self._shown.wait(1)
            self._request()
        elif kind == 1:  # paleta kolorów — przy true colour nieużywana, ale trzeba ją przeczytać
            count = struct.unpack(">xHH", self._recv(5))[1]
            self._recv(count * 6)
        elif kind == 2:  # dzwonek
            pass
        elif kind == 3:  # schowek serwera
            self._recv(3)
            self.clipboard.emit(self._reason().replace("\r\n", "\n"))
        else:
            raise ValueError(t("vnc_bad_data"))

    def _row(self, x, y, w):
        start = (y * self.width + x) * 4
        return self.fb[start:start + w * 4]

    def _blit(self, x, y, w, h, data):
        for row in range(h):
            start = ((y + row) * self.width + x) * 4
            self.fb[start:start + w * 4] = data[row * w * 4:(row + 1) * w * 4]

    def run(self):
        try:
            self.handshake()
            while True:
                self.read_message()
        except Exception as error:
            self.ended.emit("" if self.closed else (str(error) or type(error).__name__))

    # --- z GUI -------------------------------------------------------------

    def frame_shown(self):
        self._shown.set()

    def pointer(self, x, y, buttons):
        self._safe_send(struct.pack(">BBHH", 5, buttons, x, y))

    def key(self, down, sym):
        self._safe_send(struct.pack(">BBxxI", 4, int(down), sym))

    def _safe_send(self, data):
        if self.closed:
            return
        try:
            self._send(data)
        except OSError:
            pass  # zerwane — czytnik zaraz zgłosi koniec

    def close(self):
        self.closed = True
        self._shown.set()
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()
            except OSError:
                pass
        self.wait(2000)


class VncView(QWidget):
    """Obraz zdalnego ekranu skalowany do zakładki; mysz i klawisze idą do serwera."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.image = QImage()
        self.message = t("vnc_connecting")
        self._buttons = 0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        client.frame.connect(self.set_frame)

    def set_frame(self, data, width, height):
        self._data = data  # QImage nie kopiuje bajtów — muszą żyć razem z nim
        self.image = QImage(data, width, height, width * 4, QImage.Format_RGB32)
        self.message = ""
        self.update()
        self.client.frame_shown()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if not self.image.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(self._rect(), self.image)
        if self.message:
            painter.setPen(Qt.white)
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.message)

    def _rect(self):
        return fit_rect(self.width(), self.height(), self.image.width(), self.image.height())

    # --- mysz ----------------------------------------------------------------

    _MASKS = {Qt.LeftButton: 1, Qt.MiddleButton: 2, Qt.RightButton: 4}

    def _pointer(self, event, buttons=None):
        if self.image.isNull():
            return
        pos = event.position().toPoint()
        x, y = to_fb(pos.x(), pos.y(), self._rect(), self.image.width(), self.image.height())
        self.client.pointer(x, y, self._buttons if buttons is None else buttons)

    def mousePressEvent(self, event):
        self._buttons |= self._MASKS.get(event.button(), 0)
        self._pointer(event)

    def mouseReleaseEvent(self, event):
        self._buttons &= ~self._MASKS.get(event.button(), 0)
        self._pointer(event)

    def mouseMoveEvent(self, event):
        self._pointer(event)

    def wheelEvent(self, event):
        wheel = 8 if event.angleDelta().y() > 0 else 16  # przyciski 4/5 = kółko w górę/dół
        self._pointer(event, self._buttons | wheel)
        self._pointer(event)

    # --- klawiatura ------------------------------------------------------------

    def focusNextPrevChild(self, _next):
        return False  # Tab ma iść do zdalnego ekranu, nie przenosić fokusu

    def _key(self, event, down):
        sym = keysym(event.key(), event.text())
        if sym is not None:
            self.client.key(down, sym)

    def keyPressEvent(self, event):
        self._key(event, True)

    def keyReleaseEvent(self, event):
        if not event.isAutoRepeat():
            self._key(event, False)


class VncTab(QWidget):
    """Zakładka VNC — interfejs jak `RdpTab` (siatka bierze `control`)."""

    session_ended = Signal(str)

    def __init__(self, conn, sock, password="", parent=None):
        super().__init__(parent)
        self.conn = conn
        self.split = None
        self.last_stats = ""
        self.client = VncClient(sock, password)
        self.control = VncView(self.client)
        self.client.ended.connect(self._on_ended)
        self.client.clipboard.connect(lambda text: QGuiApplication.clipboard().setText(text))

        cad = QPushButton(t("vnc_cad"))
        cad.setFocusPolicy(Qt.NoFocus)
        cad.clicked.connect(self.send_ctrl_alt_del)
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 2, 2, 0)
        bar.addWidget(cad)
        bar.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.control, 1)
        self.client.start()

    def send_ctrl_alt_del(self):
        for sym in (KEY_CTRL, KEY_ALT, KEY_DELETE):
            self.client.key(True, sym)
        for sym in (KEY_DELETE, KEY_ALT, KEY_CTRL):
            self.client.key(False, sym)

    def _on_ended(self, reason):
        if not reason:
            return
        self.last_stats = t("vnc_ended", reason)
        self.control.message = self.last_stats
        self.control.update()
        self.session_ended.emit(self.last_stats)

    def close_session(self):
        self.client.close()


def selftest():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    # Znany wektor: `vncpasswd` z hasłem „password” zapisuje dbd83cfd727a1458
    # (DES stałym kluczem VNC) — pilnuje odwracania bitów klucza.
    assert _des(bytes([23, 82, 107, 6, 35, 78, 88, 7]), b"password").hex() == "dbd83cfd727a1458"
    assert keysym(Qt.Key_Return, "\r") == 0xff0d and keysym(Qt.Key_F5, "") == 0xffc2
    assert keysym(Qt.Key_C, "\x03") == ord("c"), "Ctrl+C ma iść jako „c”"
    assert keysym(Qt.Key_A, "A") == 0x41 and keysym(0, "ą") == 0x01000105
    rect = fit_rect(200, 100, 400, 100)  # szerszy ekran — pasy u góry i dołu
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (0, 25, 200, 50), rect
    assert to_fb(100, 50, rect, 400, 100) == (200, 50) and to_fb(-5, 999, rect, 400, 100) == (0, 99)

    # Prawdziwa rozmowa RFB 3.8 z hasłem przez parę gniazd; serwer w wątku.
    server, client_sock = socket.socketpair()
    seen = {}

    def exact(size):
        data = b""
        while len(data) < size:
            data += server.recv(size - len(data))
        return data

    def serve():
        server.sendall(b"RFB 003.008\n")
        seen["version"] = exact(12)
        server.sendall(bytes([2, 1, 2]))
        seen["type"] = exact(1)
        challenge = bytes(range(16))
        server.sendall(challenge)
        seen["auth_ok"] = exact(16) == vnc_response("tajne", challenge)
        server.sendall(struct.pack(">I", 0 if seen["auth_ok"] else 1))
        seen["shared"] = exact(1)
        server.sendall(struct.pack(">HH", 4, 2) + bytes(16) + struct.pack(">I", 4) + b"test")
        seen["format"] = exact(20)
        count = struct.unpack(">xxH", exact(4))[0]
        seen["encodings"] = struct.unpack(f">{count}i", exact(4 * count))
        seen["request"] = exact(10)
        pixels = bytes([1, 2, 3, 0, 4, 5, 6, 0])
        server.sendall(struct.pack(">BxH", 0, 2) + struct.pack(">HHHHi", 0, 0, 2, 1, ENC_RAW) + pixels
                       + struct.pack(">HHHHi", 2, 1, 2, 1, ENC_COPYRECT) + struct.pack(">HH", 0, 0))
        seen["next"] = exact(10)
        seen["input"] = exact(6 + 8)
        # Prostokąt wystający poza ekran 4×2 — ma zostać odrzucony.
        server.sendall(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", 3, 0, 2, 1, ENC_RAW))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    vnc = VncClient(client_sock, "tajne")
    frames = []
    vnc.frame.connect(lambda data, w, h: (frames.append((data, w, h)), vnc.frame_shown()))
    vnc.handshake()  # bez start() — te same kroki co `run`, ale w tym wątku
    vnc.read_message()
    # Dopiero tu: serwer wysyła obraz po przeczytaniu prośby, więc `seen` jest już pełne.
    assert seen["version"] == b"RFB 003.008\n" and seen["type"] == b"\x02" and seen["auth_ok"]
    assert seen["shared"] == b"\x01" and vnc.name == "test" and (vnc.width, vnc.height) == (4, 2)
    assert seen["format"][4:6] == bytes([32, 24]) and seen["encodings"] == (1, 0, -223)
    assert seen["request"] == struct.pack(">BBHHHH", 3, 0, 0, 0, 4, 2), "pierwsza prośba ma być pełna"
    data, width, height = frames[-1]
    assert (width, height) == (4, 2) and data[:8] == bytes([1, 2, 3, 0, 4, 5, 6, 0])
    assert data[(1 * 4 + 2) * 4:(1 * 4 + 4) * 4] == bytes([1, 2, 3, 0, 4, 5, 6, 0]), "CopyRect"
    vnc.pointer(3, 1, 1)
    vnc.key(True, 0xff0d)
    thread.join(5)
    assert seen["next"][1] == 1, "kolejne prośby — tylko zmiany (incremental)"
    assert seen["input"] == struct.pack(">BBHH", 5, 1, 3, 1) + struct.pack(">BBxxI", 4, 1, 0xff0d)
    try:
        vnc.read_message()
        raise AssertionError("prostokąt spoza ekranu przepuszczony")
    except ValueError:
        pass
    assert len(vnc.fb) == 4 * 2 * 4, "bufor nie może zmienić rozmiaru"

    server.close()
    vnc.close()
    print("vnc selftest OK")


if __name__ == "__main__":
    selftest()
