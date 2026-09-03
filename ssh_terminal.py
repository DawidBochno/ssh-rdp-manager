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
import stat
import threading
import time
from binascii import hexlify
from pathlib import Path

import paramiko
from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

CONNECT_TIMEOUT = 15  # sekundy

# Wyjście `WINDOWS_STATS_CMD` do testów — Windows Server 2019.
_WINDOWS_SAMPLE = """@UP
{up}
@CPUPCT
{cpu}
@MEMW
16777216
8388608
@NETW
{rx}
{tx}
@DISKW
128000000000
64000000000
@USERSW
3
"""



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
    sections = _sections(text)
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


# Windows Server przez OpenSSH: to samo, ale z PowerShella. Skrypt nie może
# zawierać cudzysłowów — cały leci jako jeden argument w cudzysłowie, bo
# domyślną powłoką OpenSSH na Windows bywa cmd.exe (inaczej zjadłby `|` i `>`).
# Liczby rzutujemy na całkowite: `[string]` na ułamku dałby przecinek
# dziesiętny na polskim Windows i parser by się wyłożył.
WINDOWS_STATS_CMD = (
    "powershell -NoProfile -NonInteractive -Command \""
    "$ErrorActionPreference='SilentlyContinue';"
    "$os=Get-CimInstance Win32_OperatingSystem;"
    "$up=((Get-Date)-$os.LastBootUpTime).TotalSeconds;"
    "$cpu=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average;"
    "$d=Get-CimInstance Win32_LogicalDisk | Where-Object {$_.DeviceID -eq $env:SystemDrive};"
    "$n=Get-NetAdapterStatistics;"
    "$rx=($n | Measure-Object -Property ReceivedBytes -Sum).Sum;"
    "$tx=($n | Measure-Object -Property SentBytes -Sum).Sum;"
    "$users=[Math]::Max(0,@(quser).Count-1);"
    "Write-Output '@UP' ([string][int64]$up) '@CPUPCT' ([string][int]$cpu)"
    " '@MEMW' ([string][int64]$os.TotalVisibleMemorySize) ([string][int64]$os.FreePhysicalMemory)"
    " '@NETW' ([string][int64]$rx) ([string][int64]$tx)"
    " '@DISKW' ([string][int64]$d.Size) ([string][int64]$d.FreeSpace)"
    " '@USERSW' ([string][int]$users)"
    "\""
)


def _sections(text):
    """Wyjście podzielone liniami `@NAZWA` na listy linii."""
    sections, current = {}, None
    for line in text.splitlines():
        if line.startswith("@"):
            current = line[1:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line.strip())
    return sections


def parse_windows_stats(text):
    """Rozbiera wyjście `WINDOWS_STATS_CMD`. None = to nie był Windows."""
    sections = _sections(text)
    stats = {}
    try:
        stats["uptime"] = float(sections["UP"][0])
        stats["cpu_pct"] = float(sections["CPUPCT"][0])
        # Pamięć CIM podaje w kilobajtach.
        stats["mem_total"] = float(sections["MEMW"][0]) * 1024
        stats["mem_free"] = float(sections["MEMW"][1]) * 1024
        size = float(sections["DISKW"][0])
        free = float(sections["DISKW"][1])
        stats["disk_free"] = free
        stats["disk_pct"] = 100 * (size - free) / size if size else 0.0
        stats["users"] = int(sections["USERSW"][0])
    except (KeyError, IndexError, ValueError):
        return None
    # Liczniki sieci są opcjonalne: starszy Windows nie ma
    # Get-NetAdapterStatistics, wtedy pasek pokaże przy ruchu „—".
    try:
        stats["rx"] = float(sections["NETW"][0])
        stats["tx"] = float(sections["NETW"][1])
    except (KeyError, IndexError, ValueError):
        pass
    return stats


def format_stats(current, previous=None):
    """Składa tekst na pasek. Bez poprzedniej próbki nie ma czym policzyć tempa."""
    seconds = current["uptime"] - previous["uptime"] if previous else 0

    busy = "—"
    if "cpu_pct" in current:
        busy = f"{current['cpu_pct']:.0f}%"
    elif previous and seconds > 0:
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
        f"dysk {current['disk_pct']:.0f}% (wolne {human_bytes(current['disk_free'])})"
    )

    if previous and seconds > 0 and "rx" in current and "rx" in previous:
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

    def _read(self, command, parse):
        try:
            _, out, _ = self.client.exec_command(command, timeout=10)
            return parse(out.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

    def run(self):
        # Przy pierwszym odpytaniu nie wiemy, co stoi po drugiej stronie:
        # próbujemy obu poleceń i zapamiętujemy to, które odpowiedziało.
        variants = [(STATS_CMD, parse_stats), (WINDOWS_STATS_CMD, parse_windows_stats)]
        previous = None
        while not self._stop.is_set():
            current = None
            for command, parse in list(variants):
                current = self._read(command, parse)
                if current is not None:
                    variants = [(command, parse)]
                    break
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


# --- graficzna przeglądarka plików (SFTP) -----------------------------------

# ponytail: listdir/get/put wołane wprost na wątku GUI — dla admina po LAN/VPN
# to milisekundy, więc osobny wątek na razie nie jest wart złożoności.
# Przy wolnych/dużych transferach przenieść na QThread jak _StatsPoller.
class SftpPanel(QWidget):
    """Panel plików po lewej stronie zakładki sesji — wzorem MobaXterm."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.sftp = None
        self.path = "/"
        try:
            self.sftp = paramiko.SFTPClient.from_transport(client.get_transport())
            self.path = self.sftp.normalize(".")
        except Exception:
            self.sftp = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        toolbar = QHBoxLayout()
        for text, tooltip, handler in (
            ("⬆", "Do folderu nadrzędnego", self._go_up),
            ("🔄", "Odśwież", self.refresh),
            ("📁+", "Nowy folder", self._new_folder),
            ("📤", "Wyślij plik", self._upload),
        ):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tooltip)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self.path_edit = QLineEdit(self.path)
        self.path_edit.returnPressed.connect(self._go_to_typed_path)
        layout.addWidget(self.path_edit)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list)

        if self.sftp is None:
            self.path_edit.setEnabled(False)
            self.list.addItem("SFTP niedostępne dla tego serwera")
            self.list.setEnabled(False)
        else:
            self.refresh()

    # --- nawigacja ------------------------------------------------------

    def refresh(self):
        self.list.clear()
        if not self.sftp:
            return
        try:
            entries = self.sftp.listdir_attr(self.path)
        except Exception as error:
            self.list.addItem(f"Błąd: {error}")
            return
        entries.sort(key=lambda e: (not stat.S_ISDIR(e.st_mode), e.filename.lower()))
        for entry in entries:
            is_dir = stat.S_ISDIR(entry.st_mode)
            item = QListWidgetItem(f"{'📁' if is_dir else '📄'} {entry.filename}")
            item.setData(Qt.UserRole, (entry.filename, is_dir))
            self.list.addItem(item)
        self.path_edit.setText(self.path)

    def _child_path(self, name):
        return self.path.rstrip("/") + "/" + name

    def _navigate(self, path):
        self.path = path or "/"
        self.refresh()

    def _go_up(self):
        if self.sftp:
            self._navigate(self.path.rsplit("/", 1)[0] or "/")

    def _go_to_typed_path(self):
        self._navigate(self.path_edit.text().strip())

    def _open_item(self, item):
        name, is_dir = item.data(Qt.UserRole)
        if is_dir:
            self._navigate(self._child_path(name))
        else:
            self._download(self._child_path(name), name)

    # --- akcje na plikach -------------------------------------------------

    def _download(self, remote_path, name):
        local_path, _ = QFileDialog.getSaveFileName(self, "Pobierz plik", name)
        if not local_path:
            return
        try:
            self.sftp.get(remote_path, local_path)
        except Exception as error:
            QMessageBox.warning(self, "Błąd pobierania", str(error))

    def _upload(self):
        if not self.sftp:
            return
        local_path, _ = QFileDialog.getOpenFileName(self, "Wyślij plik")
        if not local_path:
            return
        try:
            self.sftp.put(local_path, self._child_path(Path(local_path).name))
        except Exception as error:
            QMessageBox.warning(self, "Błąd wysyłania", str(error))
        self.refresh()

    def _new_folder(self):
        if not self.sftp:
            return
        name, ok = QInputDialog.getText(self, "Nowy folder", "Nazwa:")
        if not ok or not name:
            return
        try:
            self.sftp.mkdir(self._child_path(name))
        except Exception as error:
            QMessageBox.warning(self, "Błąd", str(error))
        self.refresh()

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None or not self.sftp:
            return
        name, is_dir = item.data(Qt.UserRole)
        menu = QMenu(self)
        if not is_dir:
            menu.addAction("Pobierz", lambda: self._download(self._child_path(name), name))
        menu.addAction("Usuń", lambda: self._delete(name, is_dir))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _delete(self, name, is_dir):
        if QMessageBox.question(
            self, "Usunąć?", f"Usunąć „{name}”?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            (self.sftp.rmdir if is_dir else self.sftp.remove)(self._child_path(name))
        except Exception as error:
            QMessageBox.warning(self, "Błąd usuwania", str(error))
        self.refresh()

    def closeEvent(self, event):
        if self.sftp:
            self.sftp.close()
        super().closeEvent(event)


class SessionTab(QWidget):
    """Zawartość zakładki sesji: SFTP po lewej, terminal po prawej — wzorem MobaXterm."""

    def __init__(self, terminal, parent=None):
        super().__init__(parent)
        self.terminal = terminal
        self.sftp_panel = SftpPanel(terminal.client, self)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self.sftp_panel)
        splitter.addWidget(self.terminal)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 780])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    @property
    def last_stats(self):
        return self.terminal.last_stats

    def close_session(self):
        self.sftp_panel.close()
        self.terminal.close_session()


# --- gotowe skrypty administracyjne -----------------------------------------

# Każdy skrypt ma wariant Linux i (opcjonalnie) Windows — uruchamiamy Linux,
# a dopiero gdy się nie powiedzie (obcy shell, brak narzędzia), próbujemy
# Windows. Ten sam wzorzec „spróbuj obu” co w _StatsPoller.
# Skrypty z {0} przyjmują parametr od użytkownika (np. nazwę usługi, host).
# Uwaga: polecenia PowerShell zawierają dosłowne `{` (np. `@{LogName=...}`),
# dlatego .format() woła się WYŁĄCZNIE gdy skrypt ma "prompt" — inaczej
# str.format wywaliłby się na tych nawiasach.
SCRIPTS = [
    {
        "label": "Top procesów (CPU/RAM)",
        "unix": "ps aux --sort=-%cpu | head -n 15",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name,CPU,WorkingSet"
        " | Format-Table -AutoSize | Out-String -Width 200\"",
    },
    {
        "label": "Miejsce na dyskach",
        "unix": "df -hP",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,Size,FreeSpace"
        " | Format-Table -AutoSize | Out-String -Width 200\"",
    },
    {
        "label": "Ostatnie błędy w logach",
        "unix": "journalctl -p err -n 50 --no-pager 2>/dev/null || dmesg | tail -n 50",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Get-EventLog -LogName System -EntryType Error -Newest 20"
        " | Format-Table TimeGenerated,Source,Message -AutoSize -Wrap | Out-String -Width 200\"",
    },
    {
        "label": "Nasłuchujące porty",
        "unix": "ss -tulpn 2>/dev/null || netstat -tulpn",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess"
        " | Format-Table -AutoSize | Out-String -Width 200\"",
    },
    {
        "label": "Restart usługi…",
        "prompt": "Nazwa usługi:",
        "unix": "sudo systemctl restart {0} && systemctl status {0} --no-pager",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Restart-Service -Name '{0}' -Force; Get-Service -Name '{0}'\"",
    },
    {
        "label": "Dostępne / ostatnie aktualizacje",
        "unix": "apt list --upgradable 2>/dev/null || yum check-update || dnf check-update",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10"
        " | Format-Table -AutoSize | Out-String -Width 200\"",
    },
    {
        "label": "Czyszczenie starych logów (7 dni)",
        "unix": "sudo journalctl --vacuum-time=7d",
        "windows": None,
    },
    {
        "label": "Nieudane logowania SSH",
        "unix": "sudo lastb -n 20 2>/dev/null || journalctl -u sshd -p err -n 20 --no-pager",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625} -MaxEvents 20"
        " | Select-Object TimeCreated,Message | Format-Table -AutoSize -Wrap | Out-String -Width 200\"",
    },
    {
        "label": "Kto jest zalogowany",
        "unix": "who -u",
        "windows": "quser",
    },
    {
        "label": "Ping hosta…",
        "prompt": "Host do sprawdzenia:",
        "unix": "ping -c 4 {0}",
        "windows": "ping -n 4 {0}",
    },
    {
        "label": "Aktywne połączenia sieciowe",
        "unix": "ss -tn state established 2>/dev/null || netstat -tn",
        "windows": "powershell -NoProfile -NonInteractive -Command \""
        "Get-NetTCPConnection -State Established"
        " | Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort"
        " | Format-Table -AutoSize | Out-String -Width 200\"",
    },
]


def _try_command(client, command):
    """Uruchamia polecenie, zwraca tekst albo None (błąd/obcy shell)."""
    try:
        _, stdout, stderr = client.exec_command(command, timeout=15)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        status = stdout.channel.recv_exit_status()
    except Exception:
        return None
    if status != 0 and not out.strip():
        return None
    return out.strip() or err.strip() or "(brak wyniku)"


def _run_commands(client, unix_cmd, windows_cmd):
    """Próbuje wariantu Linux, potem Windows. Zawsze zwraca tekst do pokazania."""
    text = _try_command(client, unix_cmd)
    if text is None and windows_cmd:
        text = _try_command(client, windows_cmd)
    return text if text is not None else "Nie udało się uruchomić skryptu na tym serwerze."


def _show_script_output(parent, title, text):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(700, 450)
    layout = QVBoxLayout(dialog)
    output = QPlainTextEdit(text)
    output.setReadOnly(True)
    output.setFont(QFont("Consolas", 10))
    layout.addWidget(output)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def run_script(parent, client, script):
    """Pyta o parametr (jeśli skrypt go wymaga), uruchamia i pokazuje wynik."""
    param = None
    if script.get("prompt"):
        param, ok = QInputDialog.getText(parent, script["label"], script["prompt"])
        if not ok or not param.strip():
            return
        param = param.strip()

    unix_cmd = script["unix"].format(param) if param is not None else script["unix"]
    windows_cmd = script.get("windows")
    if windows_cmd and param is not None:
        windows_cmd = windows_cmd.format(param)

    text = _run_commands(client, unix_cmd, windows_cmd)
    _show_script_output(parent, script["label"], text)


def selftest():
    """Sprawdza czyste funkcje — bez sieci."""
    app = QApplication.instance() or QApplication([])
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

    # Windows Server: inne polecenie, ten sam pasek. CPU dostajemy gotowe.
    win_first = parse_windows_stats(_WINDOWS_SAMPLE.format(up=100000, cpu=37, rx=1_000_000, tx=2_000_000))
    win_second = parse_windows_stats(_WINDOWS_SAMPLE.format(up=100010, cpu=37, rx=1_000_000, tx=12_000_000))
    assert win_first is not None, "wzorcowe wyjście Windows się nie rozebrało"
    assert win_first["users"] == 3 and win_first["disk_pct"] == 50
    assert win_first["mem_total"] == 16 * 1024**3, "pamięć CIM jest w kB"
    assert parse_windows_stats(_STATS_SAMPLE.format(up=1, busy=1, idle=1, rx=1, tx=1)) is None
    assert parse_stats(_WINDOWS_SAMPLE.format(up=1, cpu=1, rx=1, tx=1)) is None

    win_text = format_stats(win_second, win_first)
    assert "CPU 37%" in win_text, win_text  # gotowy procent, bez dwóch próbek
    assert "↑ 977 kB/s" in win_text, win_text
    assert "uptime 1 d 3 h" in win_text and "zalogowani: 3" in win_text, win_text
    assert "CPU 37%" in format_stats(win_first), "Windows nie potrzebuje próbki wstecz"

    # Bez liczników sieci (stary Windows) pasek pokazuje kreski, nie zera.
    no_net = dict(win_second)
    del no_net["rx"], no_net["tx"]
    assert "↓ —" in format_stats(no_net, win_first), "brak liczników to nie zero"

    # Skrypty administracyjne: każdy ma etykietę i wariant linuksowy;
    # parametryzowane mają {0} w obu wariantach, gdzie występują.
    for script in SCRIPTS:
        assert script.get("label") and script.get("unix"), script
        if script.get("prompt"):
            assert "{0}" in script["unix"]
            if script.get("windows"):
                assert "{0}" in script["windows"]

    class _FakeStream:
        def __init__(self, text=""):
            self._text = text.encode()

        def read(self):
            return self._text

    class _FakeStdout(_FakeStream):
        def __init__(self, text, status):
            super().__init__(text)
            self.channel = type("C", (), {"recv_exit_status": lambda self: status})()

    class _FakeClient:
        def __init__(self, responses):
            self.responses = responses  # polecenie -> (wyjście, kod wyjścia)

        def exec_command(self, command, timeout=None):
            out, status = self.responses.get(command, ("", 127))
            return None, _FakeStdout(out, status), _FakeStream("")

    ok_client = _FakeClient({"echo linux": ("wynik linux", 0)})
    assert _try_command(ok_client, "echo linux") == "wynik linux"
    assert _try_command(ok_client, "brak takiego") is None, "obcy shell musi dać None"

    # Linux nie odpowiada (obcy shell) -> pada próba Windows.
    fallback_client = _FakeClient({"win": ("wynik win", 0)})
    assert _run_commands(fallback_client, "linux", "win") == "wynik win"
    assert "Nie udało się" in _run_commands(_FakeClient({}), "linux", None)

    # Panel SFTP: gdy transport nie daje kanału SFTP (obcy serwer, brak
    # uprawnień), panel ma się wyłączyć, a nie wywalić.
    class _NoSftpClient:
        def get_transport(self):
            raise OSError("brak transportu")

    panel = SftpPanel(_NoSftpClient())
    assert panel.sftp is None, "atrapa bez transportu nie mogła dać działającego SFTP"
    assert not panel.list.isEnabled(), "panel bez SFTP musi być wyłączony"
    panel.deleteLater()
    del app

    print("ssh_terminal selftest OK")


if __name__ == "__main__":
    # To jest moduł, nie punkt wejścia — aplikację uruchamia `py main.py`.
    print("ssh_terminal.py to moduł. Aplikację uruchom przez: py main.py\n")
    selftest()
