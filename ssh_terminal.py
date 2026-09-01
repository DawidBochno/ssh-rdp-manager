"""Sesja SSH (Paramiko) osadzona jako widget zakładki.

Widget jest „głupim" terminalem: pokazuje tekst przysłany przez serwer i wysyła
naciśnięte klawisze do zdalnej powłoki. Sekwencje ANSI są wycinane.

Łączenie odbywa się w osobnym wątku (`SshConnector`), bo `paramiko.connect()`
potrafi wisieć kilkanaście sekund — na wątku GUI zamroziłoby to całe okno.

ponytail: brak emulacji VT100 — kolory i adresowanie kursora są odrzucane, więc
programy pełnoekranowe (vim, htop, mc) będą wyglądać źle. Gdy będą potrzebne,
podmień renderowanie na `pyte` (emulator ekranu w czystym Pythonie) albo QTermWidget.
"""
import re
import socket
import time
from binascii import hexlify

import paramiko
from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit, QProgressDialog

CONNECT_TIMEOUT = 15  # sekundy

# Sekwencje sterujące do wycięcia: tytuł okna (OSC), kolory i ruch kursora (CSI),
# przełączanie zestawu znaków oraz trybu klawiatury.
ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b[()][A-Za-z0-9]"
    r"|\x1b[=>]"
)

# Klawisze specjalne → bajty oczekiwane przez powłokę.
SPECIAL_KEYS = {
    Qt.Key_Return: "\r",
    Qt.Key_Enter: "\r",
    Qt.Key_Backspace: "\x7f",
    Qt.Key_Tab: "\t",
    Qt.Key_Escape: "\x1b",
    Qt.Key_Up: "\x1b[A",
    Qt.Key_Down: "\x1b[B",
    Qt.Key_Right: "\x1b[C",
    Qt.Key_Left: "\x1b[D",
    Qt.Key_Home: "\x1b[H",
    Qt.Key_End: "\x1b[F",
    Qt.Key_PageUp: "\x1b[5~",
    Qt.Key_PageDown: "\x1b[6~",
    Qt.Key_Delete: "\x1b[3~",
}

# Wątki łączenia, które przeżyły anulowanie — trzymamy referencje, żeby Python
# ich nie sprzątnął w trakcie pracy. Same się wypisują po zakończeniu.
# ponytail: zwykły zbiór wystarcza przy kilku równoległych połączeniach.
_pending = set()


def wait_for_pending(timeout_ms=3000):
    """Czeka na anulowane wątki łączenia przed zamknięciem aplikacji.

    Qt wywala proces, jeśli zniszczy wciąż pracujący QThread.
    """
    for connector in list(_pending):
        connector.cancel()
        connector.wait(timeout_ms)


def strip_ansi(text):
    """Usuwa sekwencje sterujące i normalizuje końce linii."""
    return ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "")


def key_to_bytes(key, modifiers, text):
    """Zamienia zdarzenie klawiatury na ciąg wysyłany do powłoki (None = pomiń)."""
    if modifiers & Qt.ControlModifier and Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key - Qt.Key_A + 1)  # Ctrl+C -> \x03, Ctrl+D -> \x04
    if key in SPECIAL_KEYS:
        return SPECIAL_KEYS[key]
    return text or None


def format_wait(seconds, timeout=CONNECT_TIMEOUT):
    """Tekst komunikatu o czasie oczekiwania."""
    return f"Czas oczekiwania: {int(seconds)} s (limit {timeout} s)"


class _ThreadHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Pyta o nieznany klucz serwera — pytanie przekazuje do wątku GUI.

    Okien Qt nie wolno tworzyć poza wątkiem GUI, więc zamiast pokazać
    QMessageBox tutaj, emitujemy sygnał i czekamy na odpowiedź.
    """

    def __init__(self, connector):
        self.connector = connector

    def missing_host_key(self, client, hostname, key):
        fingerprint = hexlify(key.get_fingerprint(), ":").decode()
        answer = {}
        self.connector.ask_host_key.emit(hostname, key.get_name(), fingerprint, answer)
        if not answer.get("accepted"):
            raise paramiko.SSHException(f"Odrzucono klucz serwera {hostname}")
        client.get_host_keys().add(hostname, key.get_name(), key)


class HostKeyAsker(QObject):
    """Żyje w wątku GUI i pokazuje pytanie o odcisk klucza."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.widget = parent

    @Slot(str, str, str, object)
    def ask(self, hostname, key_type, fingerprint, answer):
        reply = QMessageBox.question(
            self.widget,
            "Nieznany klucz serwera",
            f"Serwer {hostname} przedstawił nieznany klucz {key_type}:\n\n"
            f"{fingerprint}\n\n"
            "Zaakceptuj tylko jeśli ten odcisk się zgadza — inaczej połączenie\n"
            "może być przechwytywane. Kontynuować?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        answer["accepted"] = reply == QMessageBox.Yes


class SshConnector(QThread):
    """Nawiązuje połączenie SSH w tle."""

    ask_host_key = Signal(str, str, str, object)
    connected = Signal(object, object)  # client, channel
    failed = Signal(str)

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._cancelled = False
        self._sock = None

    def cancel(self):
        """Użytkownik zrezygnował — przerywa czekanie i sprząta po sobie.

        Zamknięcie gniazda wybija Paramiko z blokującego odczytu, więc wątek
        kończy się od razu zamiast mielić do końca limitu czasu. Bez tego
        wiszący wątek potrafił wywalić aplikację przy zamykaniu.
        """
        self._cancelled = True
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def run(self):
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(_ThreadHostKeyPolicy(self))
        try:
            # Gniazdo tworzymy sami, żeby anulowanie mogło je zamknąć.
            self._sock = socket.create_connection(
                (self.host, self.port), CONNECT_TIMEOUT
            )
            # Puste hasło = próba logowania kluczem (agent lub ~/.ssh).
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password or None,
                look_for_keys=not self.password,
                allow_agent=not self.password,
                timeout=CONNECT_TIMEOUT,
                sock=self._sock,
            )
            channel = client.invoke_shell(term="xterm", width=100, height=30)
        except Exception as error:
            client.close()
            if not self._cancelled:
                self.failed.emit(str(error))
            return

        if self._cancelled:
            client.close()  # nikt już na to nie czeka
            return
        self.connected.emit(client, channel)


def connect_with_progress(parent, host, port, username, password):
    """Łączy się pokazując okno postępu. Zwraca SshTerminal albo None.

    None oznacza anulowanie lub błąd (błąd jest pokazywany użytkownikowi).
    """
    target = f"{username}@{host}:{port}" if username else f"{host}:{port}"
    dialog = QProgressDialog(f"Łączenie z {target}…", "Anuluj", 0, 0, parent)
    dialog.setWindowTitle("Łączenie SSH")
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)

    connector = SshConnector(host, port, username, password)
    asker = HostKeyAsker(parent)
    # Blocking: wątek roboczy czeka, aż użytkownik odpowie w oknie GUI.
    connector.ask_host_key.connect(asker.ask, Qt.BlockingQueuedConnection)

    result = {}
    connector.connected.connect(lambda c, ch: result.update(client=c, channel=ch))
    connector.failed.connect(lambda err: result.update(error=err))

    loop = QEventLoop()
    connector.finished.connect(loop.quit)
    dialog.canceled.connect(loop.quit)

    started = time.monotonic()
    timer = QTimer(dialog)
    timer.timeout.connect(
        lambda: dialog.setLabelText(
            f"Łączenie z {target}…\n{format_wait(time.monotonic() - started)}"
        )
    )
    timer.start(500)

    _pending.add(connector)
    connector.finished.connect(lambda: _pending.discard(connector))
    connector.start()
    dialog.show()
    loop.exec()

    timer.stop()
    dialog.close()

    if dialog.wasCanceled() and connector.isRunning():
        connector.cancel()  # dokończy w tle i sam po sobie posprząta
        return None
    if "error" in result:
        QMessageBox.critical(
            parent, "Błąd połączenia", f"Nie udało się połączyć:\n\n{result['error']}"
        )
        return None
    if "client" not in result:
        return None
    return SshTerminal(result["client"], result["channel"], parent)


class _Reader(QThread):
    """Czyta z kanału w tle, żeby nie blokować GUI."""

    received = Signal(str)
    finished_session = Signal()

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    def run(self):
        while True:
            try:
                data = self.channel.recv(4096)
            except Exception:
                break
            if not data:
                break
            self.received.emit(data.decode("utf-8", errors="replace"))
        self.finished_session.emit()


class SshTerminal(QPlainTextEdit):
    """Terminal SSH nadający się na zawartość zakładki.

    Dostaje gotowe, już nawiązane połączenie — łączeniem zajmuje się
    `connect_with_progress()`.
    """

    def __init__(self, client, channel, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Consolas", 10))
        self.setUndoRedoEnabled(False)
        self.document().setMaximumBlockCount(5000)  # ogranicz zużycie pamięci

        self.client = client
        self.channel = channel
        self.reader = _Reader(channel)
        self.reader.received.connect(self._append)
        self.reader.finished_session.connect(self._on_closed)
        self.reader.start()

    def _append(self, text):
        self.moveCursor(QTextCursor.End)
        self.insertPlainText(strip_ansi(text))
        self.moveCursor(QTextCursor.End)

    def _on_closed(self):
        self._append("\n[sesja zakończona]\n")
        self.setReadOnly(True)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            super().keyPressEvent(event)
            return
        if not self.channel or self.channel.closed:
            return
        data = key_to_bytes(event.key(), event.modifiers(), event.text())
        if data:
            self.channel.send(data)

    def close_session(self):
        """Zamyka kanał i połączenie; bezpieczne do wielokrotnego wywołania."""
        if self.channel and not self.channel.closed:
            self.channel.close()
        self.reader.wait(2000)
        self.client.close()


def selftest():
    """Sprawdza czyste funkcje — bez sieci."""
    assert strip_ansi("\x1b[31mczerwony\x1b[0m") == "czerwony"
    assert strip_ansi("\x1b]0;tytul\x07tekst") == "tekst"
    assert strip_ansi("linia\r\ndruga") == "linia\ndruga"
    assert strip_ansi("bez zmian") == "bez zmian"

    assert key_to_bytes(Qt.Key_Return, Qt.NoModifier, "\r") == "\r"
    assert key_to_bytes(Qt.Key_Up, Qt.NoModifier, "") == "\x1b[A"
    assert key_to_bytes(Qt.Key_C, Qt.ControlModifier, "") == "\x03"
    assert key_to_bytes(Qt.Key_A, Qt.NoModifier, "a") == "a"
    assert key_to_bytes(Qt.Key_Shift, Qt.NoModifier, "") is None

    assert format_wait(0) == "Czas oczekiwania: 0 s (limit 15 s)"
    assert format_wait(3.7) == "Czas oczekiwania: 3 s (limit 15 s)"
    print("ssh_terminal selftest OK")


if __name__ == "__main__":
    # To jest moduł, nie punkt wejścia — aplikację uruchamia `py main.py`.
    print("ssh_terminal.py to moduł. Aplikację uruchom przez: py main.py\n")
    selftest()
