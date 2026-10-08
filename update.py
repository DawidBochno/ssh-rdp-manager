"""Sprawdzanie aktualizacji z GitHuba — dwie drogi, zależnie od tego, jak program trafił na dysk.

Kopia z gita (`py main.py`): „wersją” jest identyfikator commitu. Pytamy o `HEAD`
lokalnie (`git rev-parse`) i zdalnie (jedno żądanie do API GitHuba), a aktualizacja
to `git pull --ff-only`.

Paczka `.exe` (PyInstaller): wersją jest nazwa wydania z `version.txt`, wpisana przy
budowaniu (`.github/workflows/release.yml`). Porównujemy ją z najnowszym wydaniem na
GitHubie; zainstalowany program pobiera instalator i go uruchamia, przenośny otwiera
stronę wydania (nowy `.zip` rozpakowuje się samemu — program nie nadpisze sam siebie).
Bez nowych zależności: `subprocess`, `json` i `urllib` ze stdliba.
"""

import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

REPO = "DawidBochno/ssh-rdp-manager"
REMOTE = "origin"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent
TIMEOUT = 5  # sekundy na odpowiedź GitHuba — start okna nie może na tym wisieć
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
INSTALLER_SUFFIX = "-setup.exe"
# Pobieramy tylko z wydań tego repozytorium — adres przychodzi z sieci, więc pilnujemy go.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"

# Bez tego każde wywołanie gita mignęłoby czarnym oknem konsoli.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _read_version():
    """Wersja paczki `.exe` albo `None`, gdy to kopia ze źródeł (pliku nie ma)."""
    try:
        return ROOT.joinpath("version.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


VERSION = _read_version()


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


def _get(url, accept):
    request = urllib.request.Request(url, headers={"Accept": accept})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode()


def remote_head():
    """Identyfikator commitu na GitHubie albo `None`, gdy nie ma sieci."""
    # Nagłówek `...sha` daje sam identyfikator zamiast całego JSON-a z opisem commitu.
    try:
        return _get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                    "application/vnd.github.sha").strip()
    except Exception:  # brak sieci, limit API, zmieniona nazwa repozytorium
        return None


def parse_release(data):
    """Czysta funkcja: (nazwa wydania, adres instalatora albo None) z odpowiedzi API."""
    installer = None
    for asset in data.get("assets", []):
        url = asset.get("browser_download_url", "")
        if asset.get("name", "").endswith(INSTALLER_SUFFIX) and url.startswith(DOWNLOAD_PREFIX):
            installer = url
    return data.get("tag_name"), installer


def latest_release():
    """(nazwa, adres instalatora) najnowszego wydania albo (None, None) bez sieci."""
    try:
        return parse_release(json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest",
                                             "application/vnd.github+json")))
    except Exception:  # brak sieci, brak wydań (404), limit API
        return None, None


def is_outdated(local, remote):
    """Czysta funkcja (stąd asercje): czy warto proponować aktualizację."""
    return bool(local) and bool(remote) and local != remote


def pull():
    """Ściąga zmiany. Zwraca `None` gdy się udało, inaczej tekst błędu do pokazania."""
    # Zdalne repo i gałąź podajemy wprost: własna gałąź nie musi mieć
    # ustawionego śledzenia, a wtedy samo `git pull` odmawia.
    code, text = _git("pull", "--ff-only", REMOTE, BRANCH)
    return None if code == 0 else text


def download(url):
    """Pobiera instalator do katalogu tymczasowego, zwraca ścieżkę. Blokuje — tylko przez `in_background`."""
    if not url.startswith(DOWNLOAD_PREFIX):
        raise ValueError(url)
    target = Path(tempfile.gettempdir()) / url.rsplit("/", 1)[-1]
    # ponytail: bez paska postępu (okno wyszarzone na czas pobierania), dołożyć przy skargach.
    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as file:
        shutil.copyfileobj(response, file)
    return target


class UpdateCheck(QThread):
    """Pyta GitHuba w tle — sieć nie może opóźniać pokazania okna."""

    outdated = Signal(str)  # skrócony identyfikator nowego commitu albo nazwa wydania
    installer = None  # adres instalatora nowego wydania (tylko paczka `.exe`)

    def run(self):
        if VERSION:
            tag, self.installer = latest_release()
            if is_outdated(VERSION, tag):
                self.outdated.emit(tag)
            return
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
    good = DOWNLOAD_PREFIX + "v1.1/SSH-RDP-Manager-v1.1-setup.exe"
    release = {"tag_name": "v1.1", "assets": [
        {"name": "SSH-RDP-Manager-v1.1-portable.zip", "browser_download_url": DOWNLOAD_PREFIX + "v1.1/x.zip"},
        {"name": "SSH-RDP-Manager-v1.1-setup.exe", "browser_download_url": good},
    ]}
    assert parse_release(release) == ("v1.1", good)
    evil = {"tag_name": "v9", "assets": [{"name": "a-setup.exe", "browser_download_url": "https://evil.example/a-setup.exe"}]}
    assert parse_release(evil) == ("v9", None), "instalator spoza wydań repozytorium odrzucony"
    assert parse_release({}) == (None, None)
    try:
        download("https://evil.example/a-setup.exe")
        raise AssertionError("pobieranie spoza GitHuba przeszło")
    except ValueError:
        pass
    print("update selftest OK")


if __name__ == "__main__":
    selftest()
