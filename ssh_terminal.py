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
import threading
import time
from binascii import hexlify

import paramiko
from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit, QProgressDialog

CONNECT_TIMEOUT = 15  # sekundy

# Wyjście `STATS_CMD` do testów — układ jak na Debianie, przycięte.
_STATS_SAMPLE = """@UP
{up} 2000.00
@CPU
cpu {busy} 0 500 {idle} 0 0 0 0 0 0
@MEM
MemTotal:        8000000 kB
MemAvailable:    3000000 kB
@NET
Inter-|   Receive                    |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets
    lo: 99999 100 0 0 0 0 0 0 99999 100 0 0 0 0 0 0
  eth0: {rx} 100 0 0 0 0 0 0 {tx} 100 0 0 0 0 0 0
@DISK
Filesystem     1024-blocks    Used Available Capacity Mounted on
/dev/sda1         20000000 9000000  11000000      45% /
@USERS
root     pts/0        2026-09-03 10:00 (10.0.0.5)
admin    pts/1        2026-09-03 10:05 (10.0.0.6)
"""



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


# --- statystyki serwera na dolnym pasku ------------------------------------

STATS_INTERVAL = 3  # sekundy między odpytaniami serwera

# Jedno polecenie na jedno odpytanie: czytamy /proc, więc nie potrzebujemy
# ani `top`, ani `vmstat`. Sekcje rozdziela linia z `@`, bo tak najprościej
# rozebrać jeden strumień wyjścia.
STATS_CMD = (
    "echo @UP; cat /proc/uptime; "
    "echo @CPU; grep -m1 '^cpu ' /proc/stat; "
    "echo @MEM; grep -E '^(MemTotal|MemAvailable|MemFree):' /proc/meminfo; "
    "echo @NET; cat /proc/net/dev; "
    "echo @DISK; df -P /; "
    "echo @USERS; who"
)


def human_bytes(value):
    """Rozmiar po ludzku: 1536 -> „1,5 kB". Przecinek, bo interfejs jest po polsku."""
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            text = f"{value:.0f}" if unit == "B" or abs(value) >= 100 else f"{value:.1f}"
            return f"{text.replace('.', ',')} {unit}"
        value /= 1024


def human_uptime(seconds):
    """Czas pracy: „3 d 4 h", „12 h 5 min", „7 min"."""
    minutes = int(seconds) // 60
    days, rest = divmod(minutes, 60 * 24)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def parse_stats(text):
    """Rozbiera wyjście `STATS_CMD` na liczby. None = to nie jest Linux z /proc."""
    sections, current = {}, None
    for line in text.splitlines():
        if line.startswith("@"):
            current = line[1:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)

    stats = {}
    try:
        stats["uptime"] = float(sections["UP"][0].split()[0])

        cpu = [float(v) for v in sections["CPU"][0].split()[1:]]
        # Pola 4 i 5 to idle i iowait — czas, w którym procesor nic nie robił.
        stats["cpu_idle"] = cpu[3] + (cpu[4] if len(cpu) > 4 else 0)
        stats["cpu_total"] = sum(cpu)

        mem = {}
        for line in sections["MEM"]:
            name, value = line.split(":")
            mem[name.strip()] = float(value.split()[0]) * 1024  # kB -> B
        stats["mem_total"] = mem["MemTotal"]
        # MemAvailable jest dokładniejsze, ale nie ma go na starych jądrach.
        stats["mem_free"] = mem.get("MemAvailable", mem.get("MemFree", 0.0))

        rx = tx = 0.0
        for line in sections["NET"]:
            if ":" not in line:
                continue  # dwie linie nagłówka
            name, values = line.split(":", 1)
            if name.strip() == "lo":
                continue  # pętla lokalna to nie ruch sieciowy
            fields = values.split()
            rx += float(fields[0])
            tx += float(fields[8])
        stats["rx"], stats["tx"] = rx, tx

        disk = sections["DISK"][-1].split()
        stats["disk_pct"] = float(disk[-2].rstrip("%"))
        stats["disk_free"] = float(disk[-3]) * 1024  # bloki 1 kB -> B

        stats["users"] = len([line for line in sections["USERS"] if line.strip()])
    except (KeyError, IndexError, ValueError):
        return None
    return stats


def format_stats(current, previous=None):
    """Składa tekst na pasek. Bez poprzedniej próbki nie ma czym policzyć tempa."""
    seconds = current["uptime"] - previous["uptime"] if previous else 0

    busy = "—"
    if previous and seconds > 0:
        total = current["cpu_total"] - previous["cpu_total"]
        idle = current["cpu_idle"] - previous["cpu_idle"]
        if total > 0:
            busy = f"{max(0.0, 100 * (1 - idle / total)):.0f}%"
    parts = [f"CPU {busy}"]

    used = current["mem_total"] - current["mem_free"]
    share = 100 * used / current["mem_total"] if current["mem_total"] else 0
    parts.append(
        f"RAM {human_bytes(used)} / {human_bytes(current['mem_total'])} ({share:.0f}%)"
    )
    parts.append(
        f"dysk / {current['disk_pct']:.0f}% (wolne {human_bytes(current['disk_free'])})"
    )

    if previous and seconds > 0:
        down = (current["rx"] - previous["rx"]) / seconds
        up = (current["tx"] - previous["tx"]) / seconds
        parts.append(f"↓ {human_bytes(max(0, down))}/s  ↑ {human_bytes(max(0, up))}/s")
    else:
        parts.append("↓ —  ↑ —")

    parts.append(f"uptime {human_uptime(current['uptime'])}")
    parts.append(f"zalogowani: {current['users']}")
    return "   |   ".join(parts)


class _StatsPoller(QThread):
    """Odpytuje serwer o statystyki co `STATS_INTERVAL` sekund.

    Idzie osobnym kanałem (`exec_command`), więc powłoka w zakładce nic o tym
    nie wie — użytkownik nie widzi śmieci w terminalu.
    """

    updated = Signal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        previous = None
        while not self._stop.is_set():
            try:
                _, out, _ = self.client.exec_command(STATS_CMD, timeout=10)
                current = parse_stats(out.read().decode("utf-8", errors="replace"))
            except Exception:
                current = None
            if current is None:
                # Serwer bez /proc albo zamknięta sesja — nie ma sensu pytać dalej.
                if not self._stop.is_set():
                    self.updated.emit("Statystyki niedostępne dla tego serwera")
                return
            self.updated.emit(format_stats(current, previous))
            previous = current
            self._stop.wait(STATS_INTERVAL)


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

    stats_changed = Signal(str)

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

        # Statystyki serwera dla dolnego paska okna.
        self.last_stats = ""
        self.stats = _StatsPoller(client)
        self.stats.updated.connect(self._on_stats)
        self.stats.start()

    def _on_stats(self, text):
        self.last_stats = text
        self.stats_changed.emit(text)

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
        self.stats.stop()
        # Zamknięcie klienta wybija odpytywanie statystyk z blokującego odczytu.
        self.client.close()
        self.stats.wait(3000)


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

    # Statystyki: dwie próbki, bo CPU i tempo sieci liczy się z różnicy.
    first = parse_stats(_STATS_SAMPLE.format(up=1000.0, busy=1000, idle=900, rx=1_000_000, tx=500_000))
    second = parse_stats(_STATS_SAMPLE.format(up=1010.0, busy=1005, idle=905, rx=11_000_000, tx=500_000))
    assert first is not None and second is not None, "wzorcowe wyjście się nie rozebrało"
    assert first["users"] == 2, "źle policzeni zalogowani"
    assert first["mem_total"] == 8_000_000 * 1024
    assert first["disk_pct"] == 45
    assert first["rx"] == 1_000_000, "pętla lokalna nie może wchodzić do ruchu"
    assert parse_stats("cokolwiek innego") is None, "obce wyjście musi dać None"

    text = format_stats(second, first)
    assert "CPU 50%" in text, text  # 10 taktów łącznie, 5 bezczynnych
    assert "↓ 977 kB/s" in text, text  # 10 MB przez 10 s
    assert "↑ 0 B/s" in text, text
    assert "uptime 16 min" in text and "zalogowani: 2" in text, text
    assert "CPU —" in format_stats(first), "pierwsza próbka nie ma z czym się porównać"

    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1,5 kB"
    assert human_bytes(5 * 1024**3) == "5,0 GB"
    assert human_uptime(59) == "0 min"
    assert human_uptime(3 * 3600 + 120) == "3 h 2 min"
    assert human_uptime(50 * 3600) == "2 d 2 h"

    print("ssh_terminal selftest OK")


if __name__ == "__main__":
    # To jest moduł, nie punkt wejścia — aplikację uruchamia `py main.py`.
    print("ssh_terminal.py to moduł. Aplikację uruchom przez: py main.py\n")
    selftest()
