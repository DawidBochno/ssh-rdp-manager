"""Gdzie program trzyma swoje pliki: połączenia, konta, klucze serwerów, logi.

Trzy przypadki:
- uruchomienie ze źródeł (`py main.py`) — obok plików `.py`, jak zawsze;
- wersja przenośna (`.exe` z plikiem `portable.txt` obok) — obok `.exe`, czyli na
  pendrivie razem z programem; ustawienia w `settings.ini` zamiast rejestru;
- wersja zainstalowana — `%APPDATA%\\SSH-RDP-Manager`, bo do `Program Files`
  zwykły użytkownik nie może pisać, a odinstalowanie nie może zabrać połączeń.
"""

import os
import sys
from pathlib import Path

APP_NAME = "SSH-RDP-Manager"
PORTABLE_MARKER = "portable.txt"
FROZEN = getattr(sys, "frozen", False)  # ustawiane przez PyInstaller


def pick_dir(frozen, program_dir, appdata):
    """Czysta funkcja (stąd asercje): katalog danych i czy to wersja przenośna."""
    if not frozen:
        return program_dir, False
    if (program_dir / PORTABLE_MARKER).exists():
        return program_dir, True
    return appdata / APP_NAME, False


PROGRAM_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
DATA_DIR, PORTABLE = pick_dir(FROZEN, PROGRAM_DIR, Path(os.environ.get("APPDATA", Path.home())))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        program, roaming = Path(tmp) / "prog", Path(tmp) / "roaming"
        program.mkdir()
        assert pick_dir(False, program, roaming) == (program, False), "źródła: obok .py"
        assert pick_dir(True, program, roaming) == (roaming / APP_NAME, False), "instalacja: %APPDATA%"
        (program / PORTABLE_MARKER).write_text("")
        assert pick_dir(True, program, roaming) == (program, True), "pendrive: obok .exe"
    print("appdata selftest OK")


if __name__ == "__main__":
    selftest()
