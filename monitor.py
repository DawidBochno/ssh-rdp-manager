"""Monitoring zapisanych serwerów w tle — bez otwierania zakładki.

Co `monitor_interval` sekund (domyślnie 5 min) łączy się z połączeniami
oznaczonymi „Monitoruj w tle” (`conn["monitor"]`), czyta te same statystyki
co pasek statusu (`STATS_CMD`/`WINDOWS_STATS_CMD`) i rozłącza. RDP: tylko
sprawdzenie portu. Z polem „Port TLS” (`conn["tls_port"]`) dodatkowo
data ważności certyfikatu — kafelek żółknie `CERT_WARN_DAYS` przed końcem.
Wynik idzie na kafelki na Starcie, do historii w SQLite
(`monitor.db`, stdlib) i — przy zmianie stanu — do dymka w zasobniku.

Opt-in per połączenie, nie „wszystkie serwery”: logowanie co 5 min to wpis
w logach serwera, nie każdy tego chce.

Nieznany klucz serwera = błąd (`RejectPolicy`), nie pytanie: w tle nie ma
kogo zapytać, a `AutoAddPolicy` zdjęłoby ochronę przed MITM. Wystarczy raz
połączyć się ręcznie, żeby klucz trafił do `known_hosts`.
"""

import csv
import io
import sqlite3
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paramiko
from PySide6.QtCore import QThread, Signal

from i18n import t
from scanner import cert_info
from ssh_terminal import (
    STATS_CMD,
    WINDOWS_STATS_CMD,
    InteractiveClient,
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
CERT_WARN_DAYS = 14

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


def _no_track(_obj):
    pass


def _socket(host, port, track):
    """Gniazdo zgłoszone do `track` *przed* łączeniem — zamknięcie go z innego
    wątku przerywa nawet wiszące `connect()` (zamykanie okna w trakcie rundy).

    ponytail: tylko pierwszy adres z DNS (bez próbowania IPv4 po IPv6 jak
    `create_connection`) — dołożyć pętlę, gdy jakiś serwer na tym polegnie.
    """
    family, kind, proto, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    sock = socket.socket(family, kind, proto)
    track(sock)
    sock.settimeout(CHECK_TIMEOUT)
    sock.connect(address)
    return sock


def _ssh_kwargs(target):
    return dict(
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


def _client(host, port, sock, target, track):
    """Klient SSH bez pytań: nieznany klucz serwera -> wyjątek."""
    # Bez pytań: kod 2FA w tle = czytelny błąd, nie `input()` z konsoli Paramiko.
    client = InteractiveClient(ask=None)
    track(client)
    load_host_keys(client)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(hostname=host, port=port, sock=sock, **_ssh_kwargs(target))
    return client


def _jump_channel(target, port, track):
    """Kanał do `host:port` celu przez serwer pośredni (to samo konto co na cel)."""
    jump_host, _, jump_port = target["jump_host"].partition(":")
    jump_port = int(jump_port or 22)
    jump = _client(jump_host, jump_port, _socket(jump_host, jump_port, track), target, track)
    channel = jump.get_transport().open_channel(
        "direct-tcpip", (target["host"], port), ("127.0.0.1", 0)
    )
    track(channel)
    return channel


def _connect(target, track):
    if target.get("jump_host"):
        sock = _jump_channel(target, target["port"], track)
    else:
        sock = _socket(target["host"], target["port"], track)
    return _client(target["host"], target["port"], sock, target, track)


def check_ssh(target, track=_no_track):
    """-> {"ok", "cpu", "mem", "disk", "error"}. CPU z Linuksa wymaga dwóch próbek.

    Wszystko, co otwiera, zgłasza do `track` — zamyka to `check()` albo runda.
    """
    try:
        client = _connect(target, track)
    except paramiko.SSHException as error:
        if "not found in known_hosts" in str(error):
            return {"ok": False, "error": t("monitor_unknown_key")}
        return {"ok": False, "error": str(error) or type(error).__name__}
    except Exception as error:
        return {"ok": False, "error": str(error) or type(error).__name__}
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


def check_port(target, track=_no_track):
    try:
        _socket(target["host"], target["port"], track)
        return {"ok": True}
    except OSError as error:
        return {"ok": False, "error": str(error)}


def check_cert(target, result, track=_no_track):
    """Dopisuje do wyniku dni do końca certyfikatu TLS (albo błąd odczytu).

    Z serwerem pośrednim (`jump_host`) certyfikat czytany przez niego — cel
    bywa osiągalny tylko stamtąd. ponytail: osobne logowanie na serwer
    pośredni (nie to z `check_ssh`), jedno więcej na rundę.
    """
    port = target["tls_port"]
    try:
        if target.get("jump_host"):
            sock = _jump_channel(target, port, track)
        else:
            sock = _socket(target["host"], port, track)
        result["cert_days"] = cert_info(target["host"], port, CHECK_TIMEOUT, sock)["days_left"]
    except Exception as error:
        result["cert_error"] = t("monitor_tls_error", str(error) or type(error).__name__)
    return result


def check(target, track=_no_track):
    """Pełne sprawdzenie celu; wszystko, co otworzyło, zamyka na końcu."""
    opened = []

    def keep(obj):
        opened.append(obj)
        track(obj)

    try:
        is_ssh = target.get("protocol", "ssh") == "ssh"
        result = check_ssh(target, keep) if is_ssh else check_port(target, keep)
        return check_cert(target, result, keep) if target.get("tls_port") else result
    finally:
        _close_all(opened)


def _close_all(objects):
    # Od końca: kanał/klient celu przed serwerem pośrednim.
    for obj in reversed(objects):
        try:
            obj.close()
        except Exception:
            pass


# --- ocena i alerty (czyste funkcje, stąd testy) ----------------------------


def status(result, threshold):
    if not result.get("ok"):
        return RED
    hot = [result.get(k) for k in ("cpu", "mem", "disk")]
    if any(v is not None and v >= threshold for v in hot):
        return YELLOW
    cert = result.get("cert_days")
    return YELLOW if cert is not None and cert <= CERT_WARN_DAYS else GREEN


def summary(result):
    """Krótki opis na kafelek: „CPU 12% · RAM 40% · dysk 71%” albo błąd."""
    if not result.get("ok"):
        return t("monitor_down")
    parts = []
    for label, key in (("CPU", "cpu"), ("RAM", "mem"), (t("stats_disk"), "disk")):
        if result.get(key) is not None:
            parts.append(f"{label} {result[key]:.0f}%")
    if result.get("cert_days") is not None:
        parts.append(t("monitor_tls_days", result["cert_days"]))
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


def samples(key, hours=24, path=None, now=None, buckets=None):
    """-> [(cpu, ram), ...] od najstarszej — punkty dużego wykresu historii.

    `buckets` = najwyżej tyle punktów (średnia w przedziale czasu): 30 dni co
    5 min to 8640 próbek, a wykres ma kilkaset pikseli szerokości. Puste
    przedziały (serwer nie był monitorowany) po prostu nie dają punktu.
    """
    since = int(now or time.time()) - hours * 3600
    width = max(1, hours * 3600 // buckets) if buckets else 1
    db = _db(path)
    try:
        return db.execute(
            "SELECT AVG(cpu), AVG(mem) FROM samples WHERE key = ? AND ts >= ?"
            " GROUP BY (ts - ?) / ? ORDER BY MIN(ts)",
            (key, since, since, width),
        ).fetchall()
    finally:
        db.close()


def export_csv(key, hours=24, path=None, now=None):
    """Surowe próbki jako tekst CSV (separator „;” — Excel w polskich ustawieniach)."""
    since = int(now or time.time()) - hours * 3600
    db = _db(path)
    try:
        rows = db.execute(
            "SELECT ts, ok, cpu, mem, disk, error FROM samples WHERE key = ? AND ts >= ?"
            " ORDER BY ts",
            (key, since),
        ).fetchall()
    finally:
        db.close()
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(["time", "ok", "cpu", "ram", "disk", "error"])
    for ts, ok, *rest in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        writer.writerow([stamp, ok] + ["" if v is None else v for v in rest])
    return out.getvalue()


def history_text(key, hours=24, label="24 h"):
    try:
        stats = history(key, hours)
    except sqlite3.Error:
        return ""  # baza akurat zablokowana zapisem rundy — podpowiedź to tylko dodatek
    if stats is None:
        return ""
    cells = [f"{v:.0f}%" if v is not None else "—" for v in stats]
    return t("monitor_history", label, *cells)


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
        self._lock = threading.Lock()
        self._open = []
        self.cancelled = False

    def _track(self, obj):
        """Gniazda/klienci/kanały w locie; po `cancel()` zamykane od razu."""
        with self._lock:
            if not self.cancelled:
                self._open.append(obj)
                return
        obj.close()
        raise ConnectionAbortedError("monitoring zatrzymany")

    def cancel(self):
        """Zamknięcie okna: zrywa połączenia w locie zamiast czekać do CHECK_TIMEOUT."""
        with self._lock:
            self.cancelled = True
            opened, self._open = self._open, []
        _close_all(opened)

    def _check(self, target):
        if self.cancelled:
            return {"ok": False, "error": "cancelled"}
        return check(target, self._track)

    def run(self):
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {key: pool.submit(self._check, target) for key, target in self.targets.items()}
            results = {key: future.result() for key, future in futures.items()}
        if self.cancelled:
            return  # część wyników to „przerwane” — nie do historii ani na kafelki
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

    # Certyfikat TLS: żółty od CERT_WARN_DAYS w dół, wygasły też; dni w opisie.
    assert status({"ok": True, "cert_days": CERT_WARN_DAYS}, 90) == YELLOW
    assert status({"ok": True, "cert_days": -3}, 90) == YELLOW
    assert status({"ok": True, "cert_days": CERT_WARN_DAYS + 1}, 90) == GREEN
    assert "10" in summary({"ok": True, "cert_days": 10})
    assert "10" in alert_text("web", GREEN, YELLOW, {"ok": True, "cert_days": 10})
    failed = check({"protocol": "rdp", "host": "127.0.0.1", "port": 9, "tls_port": 9})
    assert "cert_error" in failed and "cert_days" not in failed, "zamknięty port TLS = błąd, nie wyjątek"
    assert "cert_error" not in check({"protocol": "rdp", "host": "127.0.0.1", "port": 9})

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.db"
        now = 1_000_000
        record({"a:22": {"ok": True, "cpu": 10, "mem": 20, "disk": 30}}, path, now - 60)
        record({"a:22": {"ok": False, "error": "x"}}, path, now)
        record({"a:22": {"ok": True, "cpu": 99}}, path, now - 2 * 86400)  # poza 24 h
        avail, cpu, mem, disk = history("a:22", 24, path, now)
        assert avail == 50 and cpu == 10 and mem == 20 and disk == 30, (avail, cpu, mem, disk)
        assert history("b:22", 24, path, now) is None
        assert samples("a:22", 24, path, now) == [(10, 20), (None, None)], "od najstarszej, bez starszych niż 24 h"
        later = now + KEEP_DAYS * 86400 + 1
        record({}, path, later)  # sprząta wszystko starsze niż 30 dni
        assert history("a:22", 24 * 365, path, later) is None

    # Wykres z dni/tygodni: najwyżej `buckets` punktów, średnia w przedziale.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.db"
        now = 2_000_000
        for i in range(12):  # godzina co 5 min
            record({"a:22": {"ok": True, "cpu": i * 10, "mem": 50}}, path, now - 3600 + i * 300)
        assert len(samples("a:22", 1, path, now)) == 12, "bez buckets = wszystkie próbki"
        squeezed = samples("a:22", 1, path, now, buckets=2)
        assert squeezed == [(25.0, 50.0), (85.0, 50.0)], squeezed
        record({"a:22": {"ok": False, "error": "brak; trasy"}}, path, now)
        rows = export_csv("a:22", 1, path, now).splitlines()
        assert rows[0] == "time;ok;cpu;ram;disk;error" and len(rows) == 14, rows[:2]
        assert rows[-1].endswith(';0;;;;"brak; trasy"'), rows[-1]

    # Certyfikat przez gotowe połączenie (droga serwera pośredniego) i wprost.
    from scanner import tls_test_server
    port, stop = tls_test_server(days=5)
    try:
        assert check_cert({"host": "127.0.0.1", "tls_port": port}, {})["cert_days"] == 5
    finally:
        stop()

    # Zamknięcie okna w trakcie rundy: wiszące łączenie przerwane od razu,
    # runda nie zapisuje i nie zgłasza połowicznych wyników.
    blackhole = {"protocol": "rdp", "host": "10.255.255.1", "port": 22}
    stopped = MonitorRound({"x": blackhole})
    got = []
    stopped.done.connect(got.append)
    stopped.start()
    time.sleep(0.5)
    started = time.monotonic()
    stopped.cancel()
    assert stopped.wait(3000), "runda nie skończyła się po cancel()"
    assert time.monotonic() - started < 3 and got == [], (time.monotonic() - started, got)
    late = MonitorRound({})
    late.cancel()
    try:
        late._track(socket.socket())
        raise AssertionError("po cancel() nowe połączenie ma być odrzucone")
    except ConnectionAbortedError:
        pass

    # Nieosiągalny port: błąd, nie wyjątek (port 9 na localhost zwykle zamknięty).
    assert check_port({"host": "127.0.0.1", "port": 9})["ok"] in (True, False)
    print("monitor selftest OK")


if __name__ == "__main__":
    selftest()
