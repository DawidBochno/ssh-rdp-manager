"""Powiadomienia systemowe — dymek z zasobnika, gdy coś długiego się skończy.

`QSystemTrayIcon` jest w PySide6, więc żadnej nowej zależności. Ikona powstaje
raz, przy pierwszym powiadomieniu: tworzenie jej w `main()` zabierałoby miejsce
w zasobniku także wtedy, gdy nic nigdy nie zawiadomi.

ponytail: bez kolejki i bez historii — dymek pokazuje ostatnią rzecz i tyle.
"""

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

_tray = None


def _icon():
    """Ikona z motywu systemowego — nie dowozimy własnego pliku."""
    app = QApplication.instance()
    return app.style().standardIcon(QStyle.SP_ComputerIcon) if app else QIcon()


def notify(title, text):
    """Dymek w zasobniku; brak wsparcia w systemie = cisza, nie wyjątek."""
    global _tray
    if QApplication.instance() is None or not QSystemTrayIcon.isSystemTrayAvailable():
        return False
    if _tray is None:
        _tray = QSystemTrayIcon(_icon())
        _tray.setToolTip(title)
        _tray.show()
    _tray.showMessage(title, text, QSystemTrayIcon.Information, 5000)
    return True


TELEGRAM_URL = "https://api.telegram.org/bot{0}/sendMessage"
# Token wchodzi do ścieżki URL — tylko format z BotFathera, nic innego.
_TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]+")


def send_telegram(token, chat_id, text, timeout=10):
    """Wiadomość od bota -> None przy sukcesie, tekst błędu inaczej.

    Blokuje do `timeout` s — z GUI przez `telegram_async` albo `in_background`.
    Błąd nie zawiera URL-a (byłby w nim token).
    """
    if not _TOKEN_RE.fullmatch(token or ""):
        return "token?"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(TELEGRAM_URL.format(token), data, timeout) as response:
            json.load(response)
    except urllib.error.HTTPError as error:
        # Telegram tłumaczy błąd w treści („chat not found”, „Unauthorized”).
        try:
            return json.load(error).get("description") or str(error)
        except ValueError:
            return str(error)
    except (OSError, ValueError) as error:
        return str(getattr(error, "reason", error))
    return None


def telegram_async(token, chat_id, text):
    """Wysyłka w tle, bez czekania — alert nie może przyblokować okna."""
    # ponytail: błąd wysyłki ginie po cichu; widać go tylko przez „Wyślij test”.
    threading.Thread(target=send_telegram, args=(token, chat_id, text), daemon=True).start()


def selftest():
    app = QApplication.instance() or QApplication([])
    # Bez zasobnika (serwer bez pulpitu) `notify` ma milczeć, a nie się wywalić.
    assert notify("test", "tresc") in (True, False)
    del app

    # Telegram na lokalnym serwerze zamiast api.telegram.org — bez sieci.
    from http.server import BaseHTTPRequestHandler, HTTPServer

    got = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            got.append((self.path, urllib.parse.parse_qs(body)))
            ok = "chat_id=42" in body
            self.send_response(200 if ok else 400)
            self.end_headers()
            self.wfile.write(json.dumps(
                {"ok": True} if ok else {"ok": False, "description": "chat not found"}
            ).encode())

        def log_message(self, *args):
            pass

    global TELEGRAM_URL
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    original = TELEGRAM_URL
    TELEGRAM_URL = f"http://127.0.0.1:{server.server_port}/bot{{0}}/sendMessage"
    try:
        assert send_telegram("123:abc_D-e", "42", "web padł") is None
        assert got[0][0] == "/bot123:abc_D-e/sendMessage", got
        assert got[0][1] == {"chat_id": ["42"], "text": ["web padł"]}, got
        assert send_telegram("123:abc", "7", "x") == "chat not found"
        assert send_telegram("123:abc/../x", "42", "x") == "token?", "token wchodzi do URL-a"
        assert len(got) == 2, "zły token nie może nic wysłać"
    finally:
        TELEGRAM_URL = original
        server.shutdown()
    print("notify selftest OK")


if __name__ == "__main__":
    selftest()
