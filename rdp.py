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

import os
import subprocess
import sys
import tempfile
import threading

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
RESIZE_DELAY_MS = 500  # po ostatniej zmianie rozmiaru zakładki — dopiero wtedy nowa rozdzielczość


def desktop_size(size):
    """Rozdzielczość dla serwera z rozmiaru zakładki: 640×480 – 8192, szerokość parzysta."""
    width = min(max(640, size.width()), 8192) // 2 * 2  # nieparzystą RDP odrzuca
    return width, min(max(480, size.height()), 8192)


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
    if conn.get("rdp_gateway"):
        lines += [
            f"gatewayhostname:s:{conn['rdp_gateway']}",
            "gatewayusagemethod:i:1",         # zawsze przez bramę
            "gatewayprofileusagemethod:i:1",  # ustawienia z pliku, nie z profilu Windows
            "gatewaycredentialssource:i:4",   # mstsc sam wybierze sposób logowania
            "promptcredentialonce:i:1",       # jedno hasło do bramy i serwera
        ]
    if conn.get("rdp_multimon"):
        lines.append("use multimon:i:1")
    return "\n".join(lines) + "\n"


def _cleanup_rdp_file(process, path):
    """Kasuje plik `.rdp` dopiero jak mstsc (cały proces okna) się skończy."""
    process.wait()
    try:
        os.remove(path)
    except OSError:
        pass


def launch_mstsc(conn):
    """Otwiera sesję w osobnym oknie `mstsc.exe`. Zwraca None albo tekst błędu."""
    try:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".rdp", delete=False, encoding="utf-8"
        )
        with handle as rdp_file:
            rdp_file.write(rdp_file_text(conn))
        process = subprocess.Popen(["mstsc", handle.name])
    except OSError as error:
        return str(error)
    # Plik zawiera host/login — nie zostawiamy go w %TEMP% na stałe. mstsc.exe
    # to samo okno sesji (nie launcher, ktory od razu wraca), wiec czekanie na
    # koniec procesu w watku w tle jest prostym i pewnym momentem na sprzatanie.
    threading.Thread(
        target=_cleanup_rdp_file, args=(process, handle.name), daemon=True
    ).start()
    return None


class RdpTab(QWidget):
    """Zawartość zakładki RDP — odpowiednik `SessionTab` dla drugiego protokołu."""

    session_ended = Signal(str)

    def __init__(self, conn, password=None, parent=None, autoconnect=True):
        super().__init__(parent)
        self.conn = conn
        self.split = None  # SplitTab, w którym kontrolka akurat jest (split.py)
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
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._update_resolution)
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
            # Schowak: tak samo jak w mstsc.exe, włączony domyślnie.
            advanced.setProperty("RedirectClipboard", True)
            # Dyski lokalne: opt-in per połączenie — to widoczność plików
            # z tego komputera dla zdalnego serwera, nie każdy tego chce.
            advanced.setProperty("RedirectDrives", bool(conn.get("redirect_drives")))
            # Zanim serwer zmieni rozdzielczość (albo gdy nie umie — starsze
            # Windowsy), obraz skaluje się do zakładki zamiast obcinać.
            advanced.setProperty("SmartSizing", True)

        # Brama RD Gateway: RDP przez HTTPS bez VPN. Te same dane logowania co
        # do serwera (`GatewayCredSharing`), jak „użyj moich poświadczeń” w mstsc.
        gateway = conn.get("rdp_gateway")
        transport = control.querySubObject("TransportSettings2") if gateway else None
        if transport is not None:
            transport.setProperty("GatewayHostname", gateway)
            transport.setProperty("GatewayUsageMethod", 1)         # zawsze przez bramę
            transport.setProperty("GatewayProfileUsageMethod", 1)  # nasze ustawienia, nie profil
            transport.setProperty("GatewayCredsSource", 4)         # dowolny sposób logowania
            transport.setProperty("GatewayCredSharing", 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Odczekanie: przeciąganie krawędzi okna to dziesiątki zdarzeń, serwer
        # ma dostać jedno — końcowy rozmiar.
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start(RESIZE_DELAY_MS)

    def _update_resolution(self):
        """Zmiana rozdzielczości w trakcie sesji (Windows 8.1 / 2012 R2 i nowsze).

        Starszy serwer odrzuci wywołanie — wtedy zostaje `SmartSizing`.
        """
        if self.control.dynamicCall("Connected") != 1:
            return
        width, height = desktop_size(self.size())
        self.control.dynamicCall(
            "UpdateSessionDisplaySettings(uint,uint,uint,uint,uint,uint,uint)",
            [width, height, width, height, 0, 100, 100],
        )

    def connect_now(self):
        width, height = desktop_size(self.size())
        # Rozdzielczość na start; dalej idzie za zakładką (`_update_resolution`).
        self.control.dynamicCall("SetDesktopWidth(int)", [width])
        self.control.dynamicCall("SetDesktopHeight(int)", [height])
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
    # Wiele monitorów: zakładka nie rozciągnie się na kilka ekranów — to umie
    # tylko pełnoekranowe okno mstsc.
    multimon = bool(conn.get("rdp_multimon"))
    if multimon or make_control() is None:
        if not multimon:
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
    assert app is not None  # referencja trzyma QApplication przy zyciu do konca testu
    i18n.use("en")

    conn = {"name": "srv", "host": "10.0.0.7", "port": 3390, "username": "admin"}
    text = rdp_file_text(conn)
    assert "full address:s:10.0.0.7:3390" in text, text
    assert "username:s:admin" in text, text
    assert "password" not in text, "hasło nie ma prawa trafić do pliku .rdp"
    # Port domyślny, gdy wpis go nie ma.
    assert "full address:s:h:3389" in rdp_file_text({"host": "h"})
    gateway_text = rdp_file_text(dict(conn, rdp_gateway="gw.firma.pl", rdp_multimon=True))
    assert "gatewayhostname:s:gw.firma.pl" in gateway_text and "gatewayusagemethod:i:1" in gateway_text
    assert "use multimon:i:1" in gateway_text and "gateway" not in text and "multimon" not in text
    from PySide6.QtCore import QSize
    assert desktop_size(QSize(1023, 300)) == (1022, 480) and desktop_size(QSize(9999, 9999)) == (8192, 8192)

    # Plik .rdp (host/login) nie moze zostac w %TEMP% na stale — sprzatanie
    # czeka na koniec "procesu" mstsc, tu podstawionego zamiast prawdziwego.
    class _FakeProcess:
        def wait(self):
            pass

    tmp_path = tempfile.NamedTemporaryFile(suffix=".rdp", delete=False).name
    _cleanup_rdp_file(_FakeProcess(), tmp_path)
    assert not os.path.exists(tmp_path), "plik .rdp mial zniknac po zakonczeniu mstsc"

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
    assert advanced.property("RedirectClipboard") is True, "schowek ma być domyślnie wlaczony"
    assert advanced.property("RedirectDrives") is False, "dyski maja byc wylaczone bez opt-in"
    assert tab.last_stats == "", "przed rozłączeniem nie ma czego pokazywać"
    tab.close_session()
    tab.deleteLater()

    drives_conn = dict(conn, redirect_drives=True)
    drives_tab = RdpTab(drives_conn, "tajne", autoconnect=False)
    advanced = drives_tab.control.querySubObject("AdvancedSettings9")
    assert advanced.property("RedirectDrives") is True, "opt-in nie dotarl do kontrolki"
    assert advanced.property("SmartSizing") is True, "obraz ma się skalować do zakładki"
    drives_tab.close_session()
    drives_tab.deleteLater()

    gateway_tab = RdpTab(dict(conn, rdp_gateway="gw.firma.pl"), "tajne", autoconnect=False)
    transport = gateway_tab.control.querySubObject("TransportSettings2")
    assert transport.property("GatewayHostname") == "gw.firma.pl", "brama nie doszła do kontrolki"
    assert transport.property("GatewayUsageMethod") == 1 and transport.property("GatewayCredSharing") == 1
    gateway_tab._update_resolution()  # niepołączona — nic nie wysyła, nie wywraca się
    gateway_tab.close_session()
    gateway_tab.deleteLater()

    print("rdp selftest OK")


if __name__ == "__main__":
    selftest()
