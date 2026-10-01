"""Monitoring zapisanych serwerów w tle — bez otwierania zakładki.

Co `monitor_interval` sekund (domyślnie 5 min) łączy się z połączeniami
oznaczonymi „Monitoruj w tle” (`conn["monitor"]`), czyta te same statystyki
co pasek statusu (`STATS_CMD`/`WINDOWS_STATS_CMD`) i rozłącza. RDP: tylko
sprawdzenie portu. Wynik idzie na kafelki na Starcie, do historii w SQLite
(`monitor.db`, stdlib) i — przy zmianie stanu — do dymka w zasobniku.

Opt-in per połączenie, nie „wszystkie serwery”: logowanie co 5 min to wpis
w logach serwera, nie każdy tego chce.

Nieznany klucz serwera = błąd (`RejectPolicy`), nie pytanie: w tle nie ma
kogo zapytać, a `AutoAddPolicy` zdjęłoby ochronę przed MITM. Wystarczy raz
połączyć się ręcznie, żeby klucz trafił do `known_hosts`.
"""

import sqlite3
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paramiko
from PySide6.QtCore import QThread, Signal

from i18n import t
from ssh_terminal import (
    STATS_CMD,
    WINDOWS_STATS_CMD,
    cpu_percent,
    load_host_keys,
    mem_percent,
    parse_stats,
    parse_windows_stats,
)

DB_FILE = Path(__file__).with_name("monitor.db")
INTERVAL_DEFAULT = 300  # sekund
CHECK_TIMEOUT = 10  # sekund na połączenie — runda ma się skończyć, zanim zamkniemy okno
KEEP_DAYS = 30  # tyle historii trzymamy w bazie
WORKERS = 8

GREEN, YELLOW, RED = "green", "yellow", "red"
COLORS = {GREEN: "#2ecc71", YELLOW: "#f1c40f", RED: "#e74c3c"}


def target_key(conn):
    """Klucz w historii: host:port — przeżywa zmianę nazwy połączenia."""
    return f"{conn.get('host', '')}:{conn.get('port') or 22}"


# --- sprawdzenie jednego serwera (wątek roboczy) ---------------------------


def _read(client, command, parse):
    try:
        _, out, _ = client.exec_command(command, timeout=CHECK_TIMEOUT)
        return parse(out.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _connect(target):
    """Klient SSH bez pytań: nieznany klucz serwera -> wyjątek."""
    kwargs = dict(
        username=target.get("username") or None,
        password=target.get("password") or None,
        key_filename=target.get("key_file") or None,
        passphrase=target.get("passphrase") or None,
        look_for_keys=not target.get("password"),
        allow_agent=not target.get("password"),
        timeout=CHECK_TIMEOUT,
        banner_timeout=CHECK_TIMEOUT,
        auth_timeout=CHECK_TIMEOUT,
    )
    jump = None
    if target.get("jump_host"):
        jump_host, _, jump_port = target["jump_host"].partition(":")
        jump = paramiko.SSHClient()
        load_host_keys(jump)
        jump.set_missing_host_key_policy(paramiko.RejectPolicy())
        jump.connect(hostname=jump_host, port=int(jump_port or 22), **kwargs)
        sock = jump.get_transport().open_channel(
            "direct-tcpip", (target["host"], target["port"]), ("127.0.0.1", 0)
        )
    else:
        sock = socket.create_connection((target["host"], target["port"]), CHECK_TIMEOUT)
    client = paramiko.SSHClient()
    load_host_keys(client)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(hostname=target["host"], port=target["port"], sock=sock, **kwargs)
    except Exception:
        client.close()
        if jump:
            jump.close()
        raise
    client._jump_client = jump  # zamykany razem z klientem w check_ssh
    return client


def check_ssh(target):
    """-> {"ok", "cpu", "mem", "disk", "error"}. CPU z Linuksa wymaga dwóch próbek."""
    try:
        client = _connect(target)
    except paramiko.SSHException as error:
        if "not found in known_hosts" in str(error):
            return {"ok": False, "error": t("monitor_unknown_key")}
        return {"ok": False, "error": str(error) or type(error).__name__}
    except Exception as error:
        return {"ok": False, "error": str(error) or type(error).__name__}
    try:
        first = _read(client, STATS_CMD, parse_stats)
        if first is not None:
            time.sleep(1)
            current = _read(client, STATS_CMD, parse_stats) or first
            cpu = cpu_percent(current, first if current is not first else None)
        else:
            current = _read(client, WINDOWS_STATS_CMD, parse_windows_stats)
            cpu = current and cpu_percent(current, None)
        if current is None:
            # Zalogowało się, ale statystyk brak (nie Linux/Windows) — żyje, to wystarczy.
            return {"ok": True, "error": t("stats_unavailable")}
        return {"ok": True, "cpu": cpu, "mem": mem_percent(current), "disk": current["disk_pct"]}
    finally:
        client.close()
        if client._jump_client:
            client._jump_client.close()


def check_port(target):
    try:
        with socket.create_connection((target["host"], target["port"]), CHECK_TIMEOUT):
            return {"ok": True}
    except OSError as error:
        return {"ok": False, "error": str(error)}


def check(target):
    return check_ssh(target) if target.get("protocol", "ssh") == "ssh" else check_port(target)


# --- ocena i alerty (czyste funkcje, stąd testy) ----------------------------


def status(result, threshold):
    if not result.get("ok"):
        return RED
    hot = [result.get(k) for k in ("cpu", "mem", "disk")]
    return YELLOW if any(v is not None and v >= threshold for v in hot) else GREEN


def summary(result):
    """Krótki opis na kafelek: „CPU 12% · RAM 40% · dysk 71%” albo błąd."""
    if not result.get("ok"):
        return t("monitor_down")
    parts = []
    for label, key in (("CPU", "cpu"), ("RAM", "mem"), (t("stats_disk"), "disk")):
        if result.get(key) is not None:
            parts.append(f"{label} {result[key]:.0f}%")
    return " · ".join(parts) or t("status_online")


def alert_text(name, old, new, result):
    """Tekst dymka przy zmianie stanu albo None. Pierwszy zielony pomiar to nie zmiana."""
    if old == new or (old is None and new == GREEN):
        return None
    if new == RED:
        return t("monitor_alert_down", name, result.get("error", ""))
    if new == YELLOW:
        return t("monitor_alert_warn", name, summary(result))
    return t("monitor_alert_up", name)


# --- historia w SQLite -------------------------------------------------------


def _db(path):
    db = sqlite3.connect(str(path or DB_FILE))
    db.execute(
        "CREATE TABLE IF NOT EXISTS samples (key TEXT, ts INTEGER, ok INTEGER,"
        " cpu REAL, mem REAL, disk REAL, error TEXT)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS samples_key_ts ON samples (key, ts)")
    return db


def record(results, path=None, now=None):
    """Zapis wyników rundy ({klucz: wynik}) + sprzątanie starszych niż KEEP_DAYS."""
    now = int(now or time.time())
    with _db(path) as db:
        db.executemany(
            "INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (key, now, int(r.get("ok", False)), r.get("cpu"), r.get("mem"),
                 r.get("disk"), r.get("error"))
                for key, r in results.items()
            ],
        )
        db.execute("DELETE FROM samples WHERE ts < ?", (now - KEEP_DAYS * 86400,))
    db.close()


def history(key, hours=24, path=None, now=None):
    """-> (dostępność %, maks. CPU, maks. RAM, maks. dysk) z ostatnich godzin, albo None."""
    since = int(now or time.time()) - hours * 3600
    db = _db(path)
    try:
        row = db.execute(
            "SELECT COUNT(*), AVG(ok), MAX(cpu), MAX(mem), MAX(disk)"
            " FROM samples WHERE key = ? AND ts >= ?",
            (key, since),
        ).fetchone()
    finally:
        db.close()
    if not row[0]:
        return None
    return (100 * row[1],) + tuple(row[2:])


def history_text(key):
    try:
        stats = history(key)
    except sqlite3.Error:
        return ""  # baza akurat zablokowana zapisem rundy — podpowiedź to tylko dodatek
    if stats is None:
        return ""
    cells = [f"{v:.0f}%" if v is not None else "—" for v in stats]
    return t("monitor_history", *cells)


# --- runda w tle -------------------------------------------------------------


class MonitorRound(QThread):
    """Jedna runda: wszystkie cele równolegle, zapis do bazy, wynik sygnałem.

    Cele (z odszyfrowanymi hasłami) zbiera wątek GUI — drzewa Qt nie wolno
    czytać stąd (ten sam wzorzec co `_TreeStatusCheck`).
    """

    done = Signal(object)  # {klucz: wynik}

    def __init__(self, targets):
        super().__init__()
        self.targets = targets  # {klucz: cel}

    def run(self):
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {key: pool.submit(check, target) for key, target in self.targets.items()}
            results = {key: future.result() for key, future in futures.items()}
        try:
            record(results)
        except sqlite3.Error:
            pass  # historia to dodatek — zablokowana baza nie może zatrzymać kafelków
        self.done.emit(results)


def selftest():
    import tempfile

    assert target_key({"host": "a", "port": 2222}) == "a:2222"
    assert target_key({"host": "a"}) == "a:22"

    assert status({"ok": False}, 90) == RED
    assert status({"ok": True, "cpu": None, "mem": 50, "disk": 95}, 90) == YELLOW
    assert status({"ok": True, "cpu": 10, "mem": 50, "disk": 50}, 90) == GREEN
    assert status({"ok": True}, 90) == GREEN, "RDP: sam port, bez metryk"

    assert alert_text("web", None, GREEN, {"ok": True}) is None, "start na zielono to nie alert"
    assert alert_text("web", GREEN, GREEN, {"ok": True}) is None
    assert "web" in alert_text("web", GREEN, RED, {"ok": False, "error": "timeout"})
    assert "web" in alert_text("web", None, RED, {"ok": False, "error": "x"}), "padnięty od startu"
    assert alert_text("web", RED, GREEN, {"ok": True})
    assert "95%" in alert_text("web", GREEN, YELLOW, {"ok": True, "disk": 95})

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.db"
        now = 1_000_000
        record({"a:22": {"ok": True, "cpu": 10, "mem": 20, "disk": 30}}, path, now - 60)
        record({"a:22": {"ok": False, "error": "x"}}, path, now)
        record({"a:22": {"ok": True, "cpu": 99}}, path, now - 2 * 86400)  # poza 24 h
        avail, cpu, mem, disk = history("a:22", 24, path, now)
        assert avail == 50 and cpu == 10 and mem == 20 and disk == 30, (avail, cpu, mem, disk)
        assert history("b:22", 24, path, now) is None
        later = now + KEEP_DAYS * 86400 + 1
        record({}, path, later)  # sprząta wszystko starsze niż 30 dni
        assert history("a:22", 24 * 365, path, later) is None

    # Nieosiągalny port: błąd, nie wyjątek (port 9 na localhost zwykle zamknięty).
    assert check_port({"host": "127.0.0.1", "port": 9})["ok"] in (True, False)
    print("monitor selftest OK")


if __name__ == "__main__":
    selftest()
