"""Sprawdzanie aktualizacji: czy lokalna kopia nadąża za gałęzią na GitHubie.

Program mieszka w kopii roboczej gita, więc „wersją” jest po prostu identyfikator
commitu. Zamiast własnego pliku `VERSION` i porównywania numerów pytamy o `HEAD`
lokalnie (`git rev-parse`) i zdalnie (jedno żądanie do API GitHuba), a aktualizacja
to `git pull --ff-only`. Bez nowych zależności: `subprocess` i `urllib` ze stdliba.

ponytail: działa tylko dla kopii z gita. Gdy kiedyś powstanie paczka `.exe`,
trzeba będzie dołożyć drugą drogę (pobranie wydania), a nie łatać tej.
"""

import subprocess
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

REPO = "DawidBochno/ssh-rdp-manager"
REMOTE = "origin"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent
TIMEOUT = 5  # sekundy na odpowiedź GitHuba — start okna nie może na tym wisieć

# Bez tego każde wywołanie gita mignęłoby czarnym oknem konsoli.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(*args):
    """Uruchamia gita w katalogu programu. Zwraca (kod, tekst); (None, komunikat) gdy się nie da."""
    try:
        done = subprocess.run(
            ("git", "-C", str(ROOT)) + args,
            capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # brak gita albo zawieszony
        return None, str(exc)
    return done.returncode, (done.stdout + done.stderr).strip()


def local_head():
    """Identyfikator lokalnego commitu albo `None`, gdy to nie jest kopia z gita."""
    code, text = _git("rev-parse", "HEAD")
    return text if code == 0 else None


def current_branch():
    """Nazwa gałęzi albo `None` (oderwany HEAD, brak repozytorium)."""
    code, text = _git("rev-parse", "--abbrev-ref", "HEAD")
    return text if code == 0 and text != "HEAD" else None


def remote_head():
    """Identyfikator commitu na GitHubie albo `None`, gdy nie ma sieci."""
    # Nagłówek `...sha` daje sam identyfikator zamiast całego JSON-a z opisem commitu.
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
        headers={"Accept": "application/vnd.github.sha"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode().strip()
    except Exception:  # brak sieci, limit API, zmieniona nazwa repozytorium
        return None


def is_outdated(local, remote):
    """Czysta funkcja (stąd asercje): czy warto proponować aktualizację."""
    return bool(local) and bool(remote) and local != remote


def pull():
    """Ściąga zmiany. Zwraca `None` gdy się udało, inaczej tekst błędu do pokazania."""
    # Zdalne repo i gałąź podajemy wprost: własna gałąź nie musi mieć
    # ustawionego śledzenia, a wtedy samo `git pull` odmawia.
    code, text = _git("pull", "--ff-only", REMOTE, BRANCH)
    return None if code == 0 else text


class UpdateCheck(QThread):
    """Pyta GitHuba w tle — sieć nie może opóźniać pokazania okna."""

    outdated = Signal(str)  # skrócony identyfikator nowego commitu

    def run(self):
        # Poza gałęzią `main` porównanie nie ma sensu: własna gałąź jest inna
        # z założenia, a `--ff-only` i tak odmówiłby nadpisania swojej pracy.
        if current_branch() != BRANCH:
            return
        local, remote = local_head(), remote_head()
        if is_outdated(local, remote):
            self.outdated.emit(remote[:7])


def selftest():
    assert is_outdated("aaa", "bbb")
    assert not is_outdated("aaa", "aaa")
    assert not is_outdated(None, "bbb"), "bez kopii z gita nie ma czego porównywać"
    assert not is_outdated("aaa", None), "bez sieci nie proponujemy aktualizacji"
    assert local_head() is None or len(local_head()) == 40
    assert current_branch() != "HEAD", "oderwany HEAD to nie nazwa gałęzi"
    print("update selftest OK")


if __name__ == "__main__":
    selftest()
