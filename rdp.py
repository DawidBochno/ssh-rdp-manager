"""Sesja RDP osadzona w zakładce — kontrolka ActiveX Microsoftu w `QAxWidget`.

Zamiast dowozić FreeRDP (natywne binaria do zbudowania i spakowania) korzystamy
z tego, co Windows ma u siebie: kontrolki `MsTscAx`, tej samej, na której stoi
`mstsc.exe`. `QAxWidget` jest w PySide6, więc **nie dochodzi żadna zależność**.

Pułapki, które kosztowały czas przy pisaniu tego modułu:

- **`setProperty()` na kontrolce głównej NIE dochodzi do COM.** Metaobiekt Qt nie
  dostaje właściwości ActiveX, więc `setProperty` ląduje w dynamicznej właściwości
  Qt i cicho nic nie robi. Do kontrolki głównej idzie `dynamicCall("SetX(...)")`.
- **Argument musi iść w liście.** `dynamicCall("SetServer(QString)", "host")` ustawia
  `"h"` — sam pierwszy znak. Poprawnie: `dynamicCall("SetServer(QString)", ["host"])`.
- **Na podobiektach (`AdvancedSettings9`) `setProperty()` działa normalnie** i czyta
  prawdziwe wartości COM — dlatego hasło i port ustawiamy właśnie tam.
- ProgID `MsTscAx.MsTscAx.13` nie wstaje mimo wpisu w rejestrze; `.11` wstaje.
  Stąd lista i próbowanie po kolei — ten sam wzorzec „spróbuj obu", co przy
  statystykach serwera i skryptach.
"""

import subprocess
import sys
import tempfile

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from i18n import t

RDP_PORT = 3389

# Od najnowszej wersji w dół — bierzemy pierwszą, która faktycznie wstanie.
RDP_PROGIDS = [
    "MsTscAx.MsTscAx.13",
    "MsTscAx.MsTscAx.12",
    "MsTscAx.MsTscAx.11",
    "MsTscAx.MsTscAx.10",
    "MsTscAx.MsTscAx",
]

# Co ile sprawdzać, czy sesja jeszcze żyje.
# ponytail: odpytywanie zamiast zdarzeń COM — kontrolka nie wystawia zdarzeń
# w metaobiekcie Qt (`OnDisconnected` nie ma wśród sygnałów), a odpytanie jednej
# właściwości raz na sekundę jest tańsze niż ręczne wiązanie punktu połączenia.
POLL_MS = 1000


def make_control():
    """Kontrolka RDP gotowa do konfiguracji albo None (brak Windows/komponentu)."""
    try:
        from PySide6.QtAxContainer import QAxWidget
    except ImportError:
        return None  # nie-Windows: QtAxContainer nie istnieje
    for progid in RDP_PROGIDS:
        control = QAxWidget()
        if control.setControl(progid):
            return control
        control.setControl("")  # nie zostawiaj pustej kontrolki przy życiu
    return None


def rdp_file_text(conn):
    """Zawartość pliku `.rdp` dla `mstsc.exe` — droga awaryjna.

    Hasła tu nie ma celowo: w `.rdp` idzie ono jako blob DPAPI, a nie tekstem,
    więc `mstsc` i tak o nie zapyta.
    """
    lines = [
        f"full address:s:{conn['host']}:{conn.get('port', RDP_PORT)}",
        "screen mode id:i:2",
        "authentication level:i:2",
    ]
    if conn.get("username"):
        lines.append(f"username:s:{conn['username']}")
    return "\n".join(lines) + "\n"


def launch_mstsc(conn):
    """Otwiera sesję w osobnym oknie `mstsc.exe`. Zwraca None albo tekst błędu."""
    try:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".rdp", delete=False, encoding="utf-8"
        )
        with handle as rdp_file:
            rdp_file.write(rdp_file_text(conn))
        subprocess.Popen(["mstsc", handle.name])
    except OSError as error:
        return str(error)
    return None


class RdpTab(QWidget):
    """Zawartość zakładki RDP — odpowiednik `SessionTab` dla drugiego protokołu."""

    session_ended = Signal(str)

    def __init__(self, conn, password=None, parent=None, autoconnect=True):
        super().__init__(parent)
        self.conn = conn
        self._ended = ""
        self.control = make_control()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if self.control is None:
            layout.addWidget(QLabel(t("rdp_no_control")))
            return
        layout.addWidget(self.control)

        self._configure(conn, password)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_state)
        if autoconnect:
            self.connect_now()

    # --- konfiguracja i połączenie ---------------------------------------

    def _configure(self, conn, password):
        """Wpisuje dane połączenia do kontrolki. Patrz pułapki w nagłówku modułu."""
        control = self.control
        control.dynamicCall("SetServer(QString)", [conn["host"]])
        control.dynamicCall("SetUserName(QString)", [conn.get("username", "")])

        # Podobiekt przyjmuje zwykłe setProperty — w przeciwieństwie do kontrolki.
        advanced = control.querySubObject("AdvancedSettings9")
        if advanced is not None:
            advanced.setProperty("RDPPort", int(conn.get("port", RDP_PORT)))
            advanced.setProperty("EnableCredSspSupport", True)
            if password:
                advanced.setProperty("ClearTextPassword", password)

    def connect_now(self):
        size = self.size()
        # Rozdzielczość ustala się PRZED połączeniem; zmiana w locie wymaga
        # UpdateSessionDisplaySettings, co dokładamy dopiero gdy będzie potrzebne.
        self.control.dynamicCall("SetDesktopWidth(int)", [max(640, size.width())])
        self.control.dynamicCall("SetDesktopHeight(int)", [max(480, size.height())])
        self.control.dynamicCall("Connect()")
        self._timer.start(POLL_MS)

    def _check_state(self):
        """0 = rozłączone, 1 = połączone, 2 = w trakcie łączenia."""
        if self.control.dynamicCall("Connected") == 0:
            self._timer.stop()
            code = self.control.dynamicCall("ExtendedDisconnectReason") or 0
            self._ended = t("rdp_disconnected", code)
            self.session_ended.emit(self._ended)

    # --- interfejs wspólny z SessionTab -----------------------------------

    @property
    def last_stats(self):
        """RDP nie daje statystyk serwera; po rozłączeniu pokazujemy powód."""
        return self._ended

    def close_session(self):
        if self.control is None:
            return
        if hasattr(self, "_timer"):
            self._timer.stop()
        self.control.dynamicCall("Disconnect()")


def open_rdp(parent, conn, password=None):
    """Zwraca `RdpTab` do wstawienia w zakładkę albo None.

    None = sesja poszła do osobnego `mstsc.exe` albo w ogóle się nie udała;
    komunikat użytkownik już zobaczył.
    """
    if sys.platform != "win32":
        QMessageBox.warning(parent, t("dlg_rdp_connection"), t("rdp_needs_windows"))
        return None
    if make_control() is None:
        QMessageBox.information(parent, t("dlg_rdp_connection"), t("rdp_no_control"))
        error = launch_mstsc(conn)
        if error:
            QMessageBox.warning(
                parent, t("dlg_rdp_connection"), t("rdp_mstsc_failed", error)
            )
        return None
    return RdpTab(conn, password, parent)


def selftest():
    from PySide6.QtWidgets import QApplication

    import i18n

    app = QApplication.instance() or QApplication([])
    i18n.use("en")

    conn = {"name": "srv", "host": "10.0.0.7", "port": 3390, "username": "admin"}
    text = rdp_file_text(conn)
    assert "full address:s:10.0.0.7:3390" in text, text
    assert "username:s:admin" in text, text
    assert "password" not in text, "hasło nie ma prawa trafić do pliku .rdp"
    # Port domyślny, gdy wpis go nie ma.
    assert "full address:s:h:3389" in rdp_file_text({"host": "h"})

    control = make_control()
    if control is None:
        print("rdp selftest OK (brak kontrolki ActiveX — pominięto część testów)")
        return

    # Konfiguracja musi realnie dojść do COM. To pilnuje obu pułapek naraz:
    # setProperty na kontrolce nic nie robi, a argument bez listy gubi znaki.
    tab = RdpTab(conn, "tajne", autoconnect=False)
    assert tab.control is not None
    assert tab.control.dynamicCall("Server") == "10.0.0.7", (
        "argument musi iść listą — bez niej zostaje pierwszy znak"
    )
    assert tab.control.dynamicCall("UserName") == "admin"
    advanced = tab.control.querySubObject("AdvancedSettings9")
    assert advanced.property("RDPPort") == 3390, "port nie doszedł do kontrolki"
    assert tab.last_stats == "", "przed rozłączeniem nie ma czego pokazywać"
    tab.close_session()
    tab.deleteLater()

    print("rdp selftest OK")


if __name__ == "__main__":
    selftest()
