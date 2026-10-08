"""Konsola PowerShell przez WinRM — Windows bez OpenSSH.

WinRM nie ma interaktywnej powłoki jak SSH: każde polecenie to osobne
`powershell -EncodedCommand` (pywinrm `run_ps`). Żeby `cd` działało jak
w konsoli, sami pamiętamy katalog: przed poleceniem `Set-Location`, po nim
znacznik z `Get-Location` w wyjściu (`wrap_command`/`split_cwd`).

ponytail: bez programów interaktywnych (pytania `Read-Host`, edytory) i bez
przerywania długiego polecenia — to ograniczenia samego `run_ps`; pełna
sesja PSRP (pypsrp) dopiero, gdy będzie potrzebna.

Moduł nie nazywa się `winrm.py` — przykryłby bibliotekę z pip (jak `containers.py`).
"""

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from i18n import t

WINRM_PORT = 5985  # 5986 = HTTPS
CWD_MARK = "__winrm_cwd__"


def endpoint(host, port):
    scheme = "https" if int(port) == 5986 else "http"
    return f"{scheme}://{host}:{port}/wsman"


def wrap_command(cwd, command):
    """Skrypt dla `run_ps`: UTF-8 na wyjściu, katalog z poprzedniego polecenia, znacznik nowego."""
    lines = ["[Console]::OutputEncoding = [Text.Encoding]::UTF8"]
    if cwd:
        lines.append("Set-Location -LiteralPath '{}'".format(cwd.replace("'", "''")))  # apostrof PS = ''
    if command.strip():
        lines.append(command)
    lines.append(f'"{CWD_MARK}$((Get-Location).Path)"')
    return "\n".join(lines)


def split_cwd(output):
    """(wyjście bez znacznika, katalog albo None — gdy polecenie przerwało skrypt)."""
    kept, cwd = [], None
    for line in output.splitlines():
        if line.startswith(CWD_MARK):
            cwd = line[len(CWD_MARK):].strip()
        else:
            kept.append(line)
    return "\n".join(kept).rstrip(), cwd


def open_session(conn, password):
    import winrm  # pywinrm — import tu, żeby brak biblioteki nie wywracał startu

    # NTLM: konto lokalne i domenowe („DOMENA\\login”); po HTTP pywinrm sam szyfruje treść.
    return winrm.Session(endpoint(conn["host"], conn.get("port") or WINRM_PORT),
                         auth=(conn.get("username", ""), password or ""), transport="ntlm")


class WinrmTab(QWidget):
    """Zakładka: wyjście u góry, linia polecenia na dole."""

    session_ended = Signal(str)
    run_async = True  # testy: False = polecenie od razu, bez wątku
    _done = Signal(str, str)  # wyjście, błąd — z wątku polecenia do GUI

    def __init__(self, conn, session, parent=None):
        super().__init__(parent)
        from ssh_terminal import apply_terminal_theme, terminal_font

        self.conn, self.session = conn, session
        self.split = None
        self.last_stats = ""
        self.cwd = ""
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(terminal_font())
        apply_terminal_theme(self.output)
        self.prompt = QLabel("PS>")
        self.prompt.setFont(terminal_font())
        self.input = QLineEdit()
        self.input.setFont(terminal_font())
        self.input.setPlaceholderText(t("winrm_hint"))
        self.input.returnPressed.connect(self.submit)
        line = QHBoxLayout()
        line.addWidget(self.prompt)
        line.addWidget(self.input, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.output, 1)
        layout.addLayout(line)
        self._done.connect(self._show)
        self.control = None  # ponytail: WinRM poza siatką (split.py) — konsola liniowa, nie terminal
        self._start("")  # pierwsze „polecenie” = logowanie + katalog startowy

    def submit(self):
        command = self.input.text()
        if not self.input.isEnabled() or not command.strip():
            return
        self.output.appendPlainText(f"{self.prompt.text()} {command}")
        self.input.clear()
        self._start(command)

    def _start(self, command):
        self.input.setEnabled(False)
        script = wrap_command(self.cwd, command)
        if self.run_async:
            threading.Thread(target=self._work, args=(script,), daemon=True).start()
        else:
            self._work(script)

    def _work(self, script):
        try:
            result = self.session.run_ps(script)
        except Exception as error:  # zła nazwa hosta, hasło, certyfikat HTTPS…
            self._done.emit("", str(error) or type(error).__name__)
            return
        text = result.std_out.decode("utf-8", "replace")
        error = result.std_err.decode("utf-8", "replace").strip()
        self._done.emit(text, error)

    def _show(self, text, error):
        text, cwd = split_cwd(text)
        if cwd:
            self.cwd = cwd
            self.prompt.setText(f"PS {cwd}>")
        if text:
            self.output.appendPlainText(text)
        if error:
            self.output.appendPlainText(error)
            self.last_stats = error.splitlines()[0]
        self.input.setEnabled(True)
        self.input.setFocus()

    def close_session(self):
        pass  # WinRM nie trzyma połączenia między poleceniami


def selftest():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    assert endpoint("srv", 5985) == "http://srv:5985/wsman" and endpoint("srv", "5986").startswith("https://")
    script = wrap_command("C:\\Users\\O'Neil", "dir")
    assert "Set-Location -LiteralPath 'C:\\Users\\O''Neil'" in script and "\ndir\n" in script
    assert "Set-Location" not in wrap_command("", "dir"), "pierwsze polecenie bez katalogu"
    assert split_cwd(f"a\r\nb\r\n{CWD_MARK}C:\\temp\r\n") == ("a\nb", "C:\\temp")
    assert split_cwd("Błąd") == ("Błąd", None)

    # Zakładka na podstawionej sesji: `cd` przechodzi do następnego polecenia,
    # błąd z serwera trafia na ekran.
    class _Result:
        def __init__(self, out, err=b""):
            self.std_out, self.std_err, self.status_code = out, err, 0

    class _Session:
        def __init__(self):
            self.scripts = []

        def run_ps(self, script):
            self.scripts.append(script)
            if "cd D:\\" in script:
                return _Result(f"{CWD_MARK}D:\\\r\n".encode())
            if "zle" in script:
                return _Result(b"", "zle: nie rozpoznano polecenia".encode())
            return _Result(f"ok\r\n{CWD_MARK}C:\\Users\\admin\r\n".encode())

    session = _Session()
    WinrmTab.run_async = False
    try:
        tab = WinrmTab({"host": "srv"}, session)
        assert tab.prompt.text() == "PS C:\\Users\\admin>", tab.prompt.text()
        tab.input.setText("cd D:\\")
        tab.submit()
        assert tab.cwd == "D:\\" and "Set-Location -LiteralPath 'C:\\Users\\admin'" in session.scripts[-1]
        tab.input.setText("zle")
        tab.submit()
        assert "zle: nie rozpoznano" in tab.output.toPlainText() and tab.cwd == "D:\\"
        assert tab.input.isEnabled(), "po błędzie linia polecenia ma wrócić"
    finally:
        WinrmTab.run_async = True
    tab.deleteLater()
    print("winrmtab selftest OK")


if __name__ == "__main__":
    selftest()
