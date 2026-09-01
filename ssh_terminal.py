"""Sesja SSH (Paramiko) osadzona jako widget zakładki.

Widget jest „głupim" terminalem: pokazuje tekst przysłany przez serwer i wysyła
naciśnięte klawisze do zdalnej powłoki. Sekwencje ANSI są wycinane.

ponytail: brak emulacji VT100 — kolory i adresowanie kursora są odrzucane, więc
programy pełnoekranowe (vim, htop, mc) będą wyglądać źle. Gdy będą potrzebne,
podmień renderowanie na `pyte` (emulator ekranu w czystym Pythonie) albo QTermWidget.
"""
import re
from binascii import hexlify

import paramiko
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit

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


class _AskHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Pyta użytkownika o nieznany klucz serwera zamiast ufać mu w ciemno."""

    def __init__(self, parent):
        self.parent = parent

    def missing_host_key(self, client, hostname, key):
        fingerprint = hexlify(key.get_fingerprint(), ":").decode()
        answer = QMessageBox.question(
            self.parent,
            "Nieznany klucz serwera",
            f"Serwer {hostname} przedstawił nieznany klucz {key.get_name()}:\n\n"
            f"{fingerprint}\n\n"
            "Zaakceptuj tylko jeśli ten odcisk się zgadza — inaczej połączenie\n"
            "może być przechwytywane. Kontynuować?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            raise paramiko.SSHException(f"Odrzucono klucz serwera {hostname}")
        client.get_host_keys().add(hostname, key.get_name(), key)


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
    """Terminal SSH nadający się na zawartość zakładki."""

    def __init__(self, host, port, username, password, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Consolas", 10))
        self.setUndoRedoEnabled(False)
        self.document().setMaximumBlockCount(5000)  # ogranicz zużycie pamięci

        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        self.client.set_missing_host_key_policy(_AskHostKeyPolicy(self))

        # Pusty PIN/hasło = próba logowania kluczem (agent lub ~/.ssh).
        # ponytail: connect blokuje GUI do 10 s; przenieś do wątku, gdy zacznie przeszkadzać.
        self.client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password or None,
            look_for_keys=not password,
            allow_agent=not password,
            timeout=10,
        )

        self.channel = self.client.invoke_shell(term="xterm", width=100, height=30)
        self.reader = _Reader(self.channel)
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
    print("ssh_terminal selftest OK")
