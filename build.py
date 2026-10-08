"""Buduje paczki do wydania: `dist/SSH-RDP-Manager-<wersja>-setup.exe` i `-portable.zip`.

    py -m pip install pyinstaller
    py build.py v1.0.0

Instalator wymaga Inno Setup 6 (`ISCC.exe`); bez niego powstaje sam `.zip`.
To samo odpala `.github/workflows/release.yml` po wypchnięciu tagu `v*`.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import appdata

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
NAME = appdata.APP_NAME
ISCC = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")


def run(*args):
    print(">", " ".join(map(str, args)), flush=True)
    subprocess.run(args, check=True, cwd=ROOT)


def main(version):
    (ROOT / "version.txt").write_text(version, encoding="utf-8")  # czyta go update.VERSION
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
        "--add-data", "version.txt;.",
        # pyserial ładuje obsługę adresów (`loop://` w selftescie) dynamicznie — PyInstaller jej nie widzi.
        "--collect-submodules", "serial.urlhandler",
        "main.py")
    program = DIST / NAME

    # Wersja przenośna = ten sam folder + `portable.txt` (dane obok `.exe`, patrz appdata.py).
    portable = DIST / "portable" / NAME
    shutil.rmtree(portable.parent, ignore_errors=True)
    shutil.copytree(program, portable)
    (portable / appdata.PORTABLE_MARKER).write_text(
        "Ten plik oznacza wersje przenosna: polaczenia i ustawienia zostaja w tym folderze.\n", encoding="utf-8")
    shutil.make_archive(str(DIST / f"{NAME}-{version}-portable"), "zip", portable.parent)

    if ISCC.exists():
        run(ISCC, f"/DAppVersion={version.lstrip('v')}", f"/DOutputName={NAME}-{version}-setup", "installer.iss")
    else:
        print("Brak Inno Setup — pomijam instalator.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
