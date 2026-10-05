"""Szkielet aplikacji desktopowej do zarządzania połączeniami SSH/RDP.

Lewa strona: drzewo katalogów z grupami i połączeniami.
Prawa strona: zakładki, jedna na każde otwarte połączenie.
"""
import copy
import xml.etree.ElementTree as ET
import hashlib
import json
import re
import socket
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paramiko
from PySide6.QtCore import QEvent, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QCloseEvent,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

import containers
import credentials
import disks
import graphs
import i18n
import importers
import keygen
import logsearch
import logtail
import monitor
import multirun
import notify
import patches
import processes
import scanner
import services
import sftp
import settings
import split
import transfers
import tunnels
import update
from credentials import CAN_STORE_PASSWORDS, decrypt_password, encrypt_password
from i18n import t
from rdp import RDP_PORT, open_rdp
from servers import SERVERS, HttpShare, TftpShare, curl_command, wget_command
from ssh_terminal import (
    SCRIPTS,
    KnownHostsDialog,
    SessionTab,
    SshTerminal,
    TERMINAL_THEMES,
    TerminalHighlighter,
    alert_threshold,
    alerts_enabled,
    apply_terminal_theme,
    connect_with_progress,
    in_background,
    load_user_scripts,
    run_script,
    save_text,
    script_label,
    scrollback,
    session_log_enabled,
    set_alert_threshold,
    set_alerts_enabled,
    set_scrollback,
    set_session_log_enabled,
    set_terminal_font,
    set_terminal_theme,
    set_macros,
    set_triggers,
    terminal_font,
    terminal_theme,
    macros_text,
    parse_macros,
    triggers_text,
    wait_for_pending,
)

# Środowisko połączenia (ROADMAP #19): kolor nazwy zakładki; produkcja dodatkowo
# pyta przed wysłaniem polecenia (`SshTerminal.confirm_send`).
ENVIRONMENTS = (("", "env_none"), ("prod", "env_prod"), ("test", "env_test"), ("dev", "env_dev"))
ENV_COLORS = {"prod": "#e74c3c", "test": "#e67e22", "dev": "#27ae60"}

# Połączenie → Importuj z innego programu: (napis, czytnik w `importers`).
IMPORT_SOURCES = (
    ("import_putty", "putty"),
    ("import_mobaxterm", "mobaxterm"),
    ("import_mremoteng", "mremoteng"),
    ("import_csv", "csv"),
)

# Menu „Programy”: klucz napisu -> metoda `MainWindow`. Kolejny dodatek
# narzędziowy to jeden wiersz tutaj, bez dotykania budowania menu.
TOOLS = (
    ("menu_network_scanner", "_open_scanner"),
    ("menu_wol", "_wake_on_lan"),
    ("menu_tls", "_check_certificate"),
    ("menu_tunnels", "_manage_tunnels"),
    ("menu_dashboard", "_open_dashboard"),
    ("menu_logtail", "_open_log_tail"),
    ("menu_logsearch", "_open_logsearch"),
    ("menu_services", "_manage_services"),
    ("menu_disks", "_open_disks"),
    ("menu_processes", "_open_processes"),
    ("menu_docker", "_open_docker"),
    ("menu_multirun", "_open_multirun"),
    ("menu_patches", "_open_patches"),
    ("menu_split", "_open_split"),
    ("menu_credentials", "_manage_credentials"),
    ("menu_keygen", "_open_keygen"),
    ("menu_known_hosts", "_manage_known_hosts"),
)


def telegram_config():
    """-> (token, chat_id) do alertów monitoringu; puste, gdy nieustawione.

    Token bota to sekret (kto go ma, pisze jako bot) — w `QSettings` leży
    zaszyfrowany DPAPI, jak hasła połączeń.
    """
    stored = i18n.settings()
    token = decrypt_password(stored.value("telegram_token", "")) if stored.value("telegram_token") else ""
    return token or "", stored.value("telegram_chat", "")


def set_telegram_config(token, chat_id):
    stored = i18n.settings()
    if token and CAN_STORE_PASSWORDS:
        stored.setValue("telegram_token", encrypt_password(token))
    else:
        stored.remove("telegram_token")  # bez DPAPI nie trzymamy go jawnie
    stored.setValue("telegram_chat", chat_id)


def dark_palette():
    """Ciemna paleta w stylu Fusion — bez arkusza QSS, sam `QPalette`."""
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ToolTipBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, Qt.black)
    return palette


def apply_dark_mode(on):
    """Przełącza motyw całego okna; zapamiętane w `QSettings`, stosowane od startu."""
    app = QApplication.instance()
    if app is not None:
        app.setPalette(dark_palette() if on else app.style().standardPalette())
    i18n.settings().setValue("dark_mode", on)

CONNECTION_TYPE = QTreeWidgetItem.UserType + 1
CONNECTION_DATA = Qt.UserRole + 1


class ConnectionData(dict):
    """Słownik połączenia trzymany w elemencie drzewa.

    Zwykły `dict` PySide6 zamienia w `setData` na QVariantMap i każde `data()`
    oddaje **nową kopię** — `conn["last_used"] = ...`, zakładki SFTP i tunele
    zmieniały kopię i przepadały, a `origin is conn` przy dwukliku nigdy nie
    trafiało. Podklasę dict PySide trzyma jako obiekt Pythona i oddaje ten sam.

    Dziedziczenie z grupy (ROADMAP #1): `defaults` to scalone ustawienia grup
    nad połączeniem (`ConnectionTree.refresh_inheritance`). `get()` oddaje
    wartość grupy, gdy połączenie samo ma puste pole — dzięki temu łączenie,
    monitoring, kolor zakładki i `effective_auth` dziedziczą bez zmian u siebie.
    Do pliku idzie tylko to, co własne (`json.dumps` nie woła `get`), a formularz
    dostaje `dict(conn)`, czyli też tylko własne pola.
    """

    defaults = {}

    @classmethod
    def wrap(cls, data):
        # Nie-słownik (uszkodzony plik) zostaje jak jest — wyłapie go `load()`.
        return cls(data) if isinstance(data, dict) and not isinstance(data, cls) else data

    def get(self, key, default=None):
        if key in INHERITED_KEYS and not super().get(key) and self.defaults.get(key):
            # Konto z grupy nie przykrywa własnego logowania połączenia.
            if key == "credential" and any(super(ConnectionData, self).get(k) for k in OWN_AUTH_KEYS):
                return super().get(key, default)
            return self.defaults[key]
        return super().get(key, default)


# Pola ustawiane raz na grupie. Port celowo nie: formularz zawsze go zapisuje,
# więc „puste” nie istnieje — a 22 jako „dziedzicz” myliłoby się z wpisanym 22.
INHERITED_KEYS = ("username", "credential", "key_file", "jump_host", "environment")
OWN_AUTH_KEYS = ("username", "password", "key_file")
GROUP_DATA = Qt.UserRole + 4
COLOR_DATA = Qt.UserRole + 3
GROUP_ICON = "📁"

# Zapisujemy obok skryptu; .gitignore trzyma ten plik poza repozytorium.
# Hasła NIE trafiają tutaj — celowo, plik jest zwykłym tekstem.
CONFIG_FILE = Path(__file__).with_name("connections.json")

# Ikona (emoji) jako sposób odróżnienia elementów; trzymana osobno od nazwy.
ICON_DATA = Qt.UserRole + 2
ICONS = ["📁", "🗂️", "🖥️", "🐧", "🪟",
         "🌐", "🗄️", "🔒", "⭐", "🔥",
         "🧪", "⚙️"]

# Kropka statusu (żywy/martwy zapisany serwer) — ikona osobno od ICON_DATA
# (emoji doklejone do nazwy), więc jedno nie koliduje z drugim.
STATUS_INTERVAL_DEFAULT = 120  # sekund; sprawdzanie ma być rzadkie, nie skaner

LOCK_TIMEOUT_DEFAULT = 10  # minut bezczynności do zablokowania okna

# Aktywnosc, ktora ma odkladac blokade — nie AllEvents, bo np. same Timer/Paint
# tez by ja odkladaly i blokada nigdy by sie nie wlaczyla.
_ACTIVITY_EVENTS = {QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel}


def _hash_pin(pin):
    """Sam PIN nigdy nie idzie do QSettings otwartym tekstem."""
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


class _LockDialog(QDialog):
    """Ekran blokady. Modalnosc Qt sama blokuje reszte okna — bez wlasnej nakladki.

    Escape i przycisk zamkniecia sa wylaczone (`reject()`/`closeEvent()`),
    inaczej blokada wychodzilaby bez podania PIN-u.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._unlocked = False
        self.setWindowTitle(t("lock_screen_title"))
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("lock_pin_prompt")))
        self.pin_field = QLineEdit()
        self.pin_field.setEchoMode(QLineEdit.Password)
        self.pin_field.returnPressed.connect(self._try_unlock)
        layout.addWidget(self.pin_field)
        unlock_button = QPushButton(t("lock_unlock_button"))
        unlock_button.clicked.connect(self._try_unlock)
        layout.addWidget(unlock_button)

    def _try_unlock(self):
        if _hash_pin(self.pin_field.text()) == i18n.settings().value("lock_pin_hash"):
            self._unlocked = True
            self.accept()
        else:
            QMessageBox.warning(self, t("lock_screen_title"), t("lock_wrong_pin"))
            self.pin_field.clear()

    def reject(self):
        pass  # Escape nie ma prawa zamknac blokady bez PIN-u

    def closeEvent(self, event):
        if self._unlocked:
            super().closeEvent(event)
        else:
            event.ignore()


def _status_icon(color):
    pixmap = QPixmap(10, 10)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, 10, 10)
    painter.end()
    return QIcon(pixmap)


# Leniwie: QPixmap przed QApplication potrafi wywalić proces (tak samo jak
# QFont w terminal_font()).
_status_icons = {}


def status_icon(online):
    if online not in _status_icons:
        _status_icons[online] = _status_icon("#2ecc71" if online else "#e74c3c")
    return _status_icons[online]


class _TreeStatusCheck(QThread):
    """Sprawdza port podanych hostów — jednorazowo, jedno wywołanie na rundę.

    Dostaje gotową migawkę {id(item): (host, port)} zebraną na wątku GUI
    (przed startem, patrz `MainWindow._poll_status_once`) i nie dotyka
    żadnego obiektu Qt poza własnym sygnałem — QTreeWidget wolno czytać
    tylko z wątku GUI, a to właśnie tu wcześniej cicho się wywalało.
    """

    # object, nie dict: klucze to id(item) (int), a Signal(dict) w PySide
    # próbuje je zmapować na QVariantMap (klucze-stringi) i wywala konwersję.
    result = Signal(object)

    def __init__(self, targets):
        super().__init__()
        self.targets = targets

    @staticmethod
    def _check(host, port):
        try:
            with socket.socket() as probe:
                probe.settimeout(2)
                return probe.connect_ex((host, port)) == 0
        except OSError:
            return False

    def run(self):
        with ThreadPoolExecutor(max_workers=scanner.WORKERS) as pool:
            futures = {
                key: pool.submit(self._check, host, port)
                for key, (host, port) in self.targets.items()
            }
            results = {key: future.result() for key, future in futures.items()}
        self.result.emit(results)


SSH_PORT = 22


def parse_tags(text):
    """„prod, db,  prod” -> ["prod", "db"] — bez pustych i duplikatów."""
    tags = []
    for tag in text.split(","):
        tag = tag.strip().lstrip("#").lower()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def search_text(data):
    """Tekst, po którym szuka filtr drzewa i wyszukiwarka Home (tagi jako #tag)."""
    parts = [str(data.get(k, "")) for k in ("name", "host", "username")]
    parts += ["#" + tag for tag in data.get("tags", [])]
    return " ".join(parts).lower()


class GroupDialog(QDialog):
    """Ustawienia grupy dziedziczone przez połączenia w niej (i w podgrupach),
    które same mają te pola puste."""

    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        data = data or {}
        self.setWindowTitle(t("group_settings_title"))
        self.username = QLineEdit(data.get("username", ""))
        self.credential = QComboBox()
        self.credential.addItem(t("cred_none"), None)
        for cred in credentials.load()[0]:
            self.credential.addItem(f"{cred['name']} ({cred.get('username', '')})", cred["id"])
        self.credential.setCurrentIndex(max(0, self.credential.findData(data.get("credential"))))
        self.key_file = QLineEdit(data.get("key_file", ""))
        self.jump_host = QLineEdit(data.get("jump_host", ""))
        self.jump_host.setPlaceholderText(t("ph_jump_host"))
        self.environment = QComboBox()
        for value, label in ENVIRONMENTS:
            self.environment.addItem(t(label), value)
        self.environment.setCurrentIndex(max(0, self.environment.findData(data.get("environment", ""))))
        form = QFormLayout(self)
        hint = QLabel(t("group_settings_hint"))
        hint.setWordWrap(True)
        form.addRow(hint)
        form.addRow(t("fld_user"), self.username)
        form.addRow(t("fld_credential"), self.credential)
        form.addRow(t("fld_key_file"), self.key_file)
        form.addRow(t("fld_jump_host"), self.jump_host)
        form.addRow(t("fld_environment"), self.environment)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        """Tylko wypełnione pola — puste nie przykrywają ustawień grupy wyżej."""
        values = {
            "username": self.username.text().strip(),
            "credential": self.credential.currentData(),
            "key_file": self.key_file.text().strip(),
            "jump_host": self.jump_host.text().strip(),
            "environment": self.environment.currentData(),
        }
        return {key: value for key, value in values.items() if value}


class ConnectionDialog(QDialog):
    """Formularz danych połączenia — SSH albo RDP."""

    # Klucze, które formularz odczytuje/zapisuje. Reszta (np. "bookmarks"
    # zakładek SFTP, "tunnels" tuneli SSH) ma zostać nietknięta przy edycji.
    _FORM_KEYS = {
        "name", "host", "port", "username", "protocol", "key_file",
        "jump_host", "startup", "notes", "redirect_drives", "tags",
        "password", "passphrase", "credential", "monitor", "tls_port", "forward_agent",
        "environment",
    }

    def __init__(self, parent=None, data=None, inherited=None):
        super().__init__(parent)
        # Kopia = tylko własne pola (patrz `ConnectionData.get`), dziedziczone
        # pokazujemy jako podpowiedź w pustym polu, nie jako wpisaną wartość.
        data = dict(data or {})
        self._data = data
        self._inherited = inherited = inherited or {}

        self.protocol = QComboBox()
        self.protocol.addItem("SSH", "ssh")
        self.protocol.addItem("RDP", "rdp")
        self.protocol.setCurrentIndex(
            max(0, self.protocol.findData(data.get("protocol", "ssh")))
        )

        self.name = QLineEdit(data.get("name", ""))
        self.host = QLineEdit(data.get("host", ""))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(data.get("port", self._default_port()))
        self.username = QLineEdit(data.get("username", ""))

        # Klucz prywatny dotyczy tylko SSH — przy RDP wiersz się chowa.
        self.key_file = QLineEdit(data.get("key_file", ""))
        browse = QPushButton(t("btn_browse"))
        browse.clicked.connect(self._pick_key_file)
        key_box = QWidget()
        key_layout = QHBoxLayout(key_box)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_file)
        key_layout.addWidget(browse)

        stored = decrypt_password(data["password"]) if data.get("password") else ""
        self.password = QLineEdit(stored or "")
        self.password.setEchoMode(QLineEdit.Password)

        # Hasło klucza to osobna rzecz niż hasło konta — Paramiko ma na to
        # osobny argument, a wpisanie jednego w rolę drugiego kończy się
        # komunikatem o „niepoprawnym pliku klucza".
        stored_passphrase = (
            decrypt_password(data["passphrase"]) if data.get("passphrase") else ""
        )
        self.passphrase = QLineEdit(stored_passphrase or "")
        self.passphrase.setEchoMode(QLineEdit.Password)
        self.passphrase.setToolTip(t("tip_passphrase"))

        # Bastion (ProxyJump): to samo konto/klucz co na cel, jak w typowym
        # skoku jednym kluczem. Puste = łączenie bezpośrednie, jak dotąd.
        self.jump_host = QLineEdit(data.get("jump_host", ""))
        self.jump_host.setPlaceholderText(t("ph_jump_host"))
        self.jump_host.setToolTip(t("tip_jump_host"))
        self.forward_agent = QCheckBox(t("chk_forward_agent"))
        self.forward_agent.setChecked(bool(data.get("forward_agent")))
        self.forward_agent.setToolTip(t("tip_forward_agent"))

        # Przekierowanie dysków dotyczy tylko RDP — opt-in, bo wystawia
        # lokalne pliki zdalnemu serwerowi. Schowek idzie zawsze (jak w mstsc).
        self.redirect_drives = QCheckBox(t("chk_redirect_drives"))
        self.redirect_drives.setChecked(bool(data.get("redirect_drives")))

        self.monitor = QCheckBox(t("chk_monitor"))
        self.monitor.setChecked(bool(data.get("monitor")))
        self.monitor.setToolTip(t("tip_monitor"))
        # Port certyfikatu TLS do pilnowania przez monitoring; 0 = nie sprawdzaj.
        self.environment = QComboBox()
        for value, label in ENVIRONMENTS:
            if not value and inherited.get("environment"):
                label = t("from_group", t(dict(ENVIRONMENTS)[inherited["environment"]]))
            else:
                label = t(label)
            self.environment.addItem(label, value)
        self.environment.setCurrentIndex(max(0, self.environment.findData(data.get("environment", ""))))
        self.environment.setToolTip(t("tip_environment"))
        for field, key in ((self.username, "username"), (self.key_file, "key_file"),
                           (self.jump_host, "jump_host")):
            if inherited.get(key):
                field.setPlaceholderText(t("from_group", inherited[key]))

        self.tls_port = QSpinBox()
        self.tls_port.setRange(0, 65535)
        self.tls_port.setSpecialValueText("—")
        self.tls_port.setValue(int(data.get("tls_port") or 0))
        self.tls_port.setToolTip(t("tip_tls_port"))

        self.save_password = QCheckBox(t("chk_save_password"))
        self.save_password.setChecked(bool(stored or stored_passphrase))
        self.save_password.setEnabled(CAN_STORE_PASSWORDS)
        if not CAN_STORE_PASSWORDS:
            self.save_password.setToolTip(t("tip_save_password_windows_only"))

        # Polecenia startowe dotyczą powłoki, więc tylko SSH; notatki obu.
        self.startup = QPlainTextEdit(data.get("startup", ""))
        self.startup.setToolTip(t("tip_startup"))
        self.startup.setFixedHeight(60)
        self.notes = QPlainTextEdit(data.get("notes", ""))
        self.notes.setFixedHeight(60)
        self.tags = QLineEdit(", ".join(data.get("tags", [])))
        self.tags.setPlaceholderText(t("ph_tags"))

        # Konto współdzielone (menedżer poświadczeń): gdy wybrane, login/hasło/
        # klucz idą z niego, a własne pola formularza są wyszarzone.
        self.credential = QComboBox()
        new_credential = QPushButton(t("cred_new"))
        new_credential.clicked.connect(self._new_credential)
        credential_box = QWidget()
        credential_layout = QHBoxLayout(credential_box)
        credential_layout.setContentsMargins(0, 0, 0, 0)
        credential_layout.addWidget(self.credential, 1)
        credential_layout.addWidget(new_credential)
        self._fill_credentials(data.get("credential"))
        self._auth_fields = (
            self.username, self.password, key_box, self.passphrase, self.save_password
        )
        self.credential.currentIndexChanged.connect(self._credential_changed)

        form = QFormLayout(self)
        form.addRow(t("fld_protocol"), self.protocol)
        form.addRow(t("fld_name"), self.name)
        form.addRow(t("fld_host"), self.host)
        form.addRow(t("fld_port"), self.port)
        form.addRow(t("fld_credential"), credential_box)
        form.addRow(t("fld_user"), self.username)
        form.addRow(t("fld_password"), self.password)
        self._key_row = form.rowCount()
        form.addRow(t("fld_key_file"), key_box)
        self._passphrase_row = form.rowCount()
        form.addRow(t("fld_passphrase"), self.passphrase)
        self._jump_host_row = form.rowCount()
        form.addRow(t("fld_jump_host"), self.jump_host)
        self._forward_agent_row = form.rowCount()
        form.addRow("", self.forward_agent)
        self._startup_row = form.rowCount()
        form.addRow(t("fld_startup"), self.startup)
        form.addRow(t("fld_tags"), self.tags)
        form.addRow(t("fld_environment"), self.environment)
        form.addRow(t("fld_notes"), self.notes)
        self._redirect_drives_row = form.rowCount()
        form.addRow("", self.redirect_drives)
        form.addRow("", self.save_password)
        form.addRow("", self.monitor)
        form.addRow(t("fld_tls_port"), self.tls_port)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        test = buttons.addButton(t("btn_test_connection"), QDialogButtonBox.ActionRole)
        test.clicked.connect(self._test_connection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._form = form
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self._protocol_changed()
        self._credential_changed()

    # --- konto współdzielone ---------------------------------------------------

    def _fill_credentials(self, selected=None):
        self.credential.blockSignals(True)
        self.credential.clear()
        accounts = credentials.load()[0]
        group_cred = credentials.find(accounts, self._inherited.get("credential"))
        self.credential.addItem(
            t("from_group", group_cred["name"]) if group_cred else t("cred_none"), None
        )
        for cred in accounts:
            self.credential.addItem(f"{cred['name']} ({cred.get('username', '')})", cred["id"])
        # Wskazane konto mogło zostać usunięte — wtedy „brak”, własne pola.
        self.credential.setCurrentIndex(max(0, self.credential.findData(selected)))
        self.credential.blockSignals(False)

    def _credential_changed(self):
        own = self.credential.currentData() is None
        for widget in self._auth_fields:
            widget.setEnabled(own and (widget is not self.save_password or CAN_STORE_PASSWORDS))

    def _new_credential(self):
        dialog = credentials.CredentialDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        creds, _ = credentials.load()
        cred = dialog.values()
        creds.append(cred)
        try:
            credentials.save(creds)
        except OSError as error:
            QMessageBox.warning(self, t("err_save_title"), t("err_save_body", error))
            return
        self._fill_credentials(cred["id"])
        self._credential_changed()

    # --- protokół -----------------------------------------------------------

    def _default_port(self):
        return RDP_PORT if self.protocol.currentData() == "rdp" else SSH_PORT

    def _protocol_changed(self):
        """Tytuł, domyślny port i widoczność pola klucza idą za protokołem."""
        is_rdp = self.protocol.currentData() == "rdp"
        self.setWindowTitle(t("dlg_rdp_connection") if is_rdp else t("dlg_ssh_connection"))
        # Port zmieniamy tylko wtedy, gdy stoi na domyślnym dla drugiego
        # protokołu — ręcznie wpisanego numeru nie wolno nadpisać.
        other_default = SSH_PORT if is_rdp else RDP_PORT
        if self.port.value() == other_default:
            self.port.setValue(self._default_port())
        self._form.setRowVisible(self._key_row, not is_rdp)
        self._form.setRowVisible(self._passphrase_row, not is_rdp)
        self._form.setRowVisible(self._jump_host_row, not is_rdp)
        self._form.setRowVisible(self._forward_agent_row, not is_rdp)
        self._form.setRowVisible(self._startup_row, not is_rdp)
        self._form.setRowVisible(self._redirect_drives_row, is_rdp)

    def _pick_key_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t("dlg_key_file"), "", t("filter_all_files")
        )
        if path:
            self.key_file.setText(path)

    def _test_connection(self):
        """Łączy na próbę danymi z formularza i od razu rozłącza — bez zakładki."""
        host = self.host.text().strip()
        if not host:
            QMessageBox.warning(self, t("err_missing_data_title"), t("err_missing_host"))
            return
        port = self.port.value()
        if self.protocol.currentData() == "rdp":
            # RDP łączy kontrolka ActiveX — sprawdzamy tylko, czy port odpowiada.
            ok = _TreeStatusCheck._check(host, port)
            (QMessageBox.information if ok else QMessageBox.warning)(
                self, t("btn_test_connection"),
                t("test_port_open" if ok else "test_port_closed", f"{host}:{port}"),
            )
            return
        if self.credential.currentData():
            auth = credentials.effective_auth({"credential": self.credential.currentData()})
            password = decrypt_password(auth["password"]) if auth["password"] else ""
            passphrase = decrypt_password(auth["passphrase"]) if auth["passphrase"] else ""
        else:
            auth = {"username": self.username.text().strip() or self._inherited.get("username", ""),
                    "key_file": self.key_file.text().strip() or self._inherited.get("key_file", "")}
            password, passphrase = self.password.text(), self.passphrase.text()
        terminal = connect_with_progress(
            self, host, port, auth["username"], password or "",
            auth.get("key_file") or None, passphrase or None,
            self.jump_host.text().strip() or self._inherited.get("jump_host") or None,
        )
        if terminal is None:
            return  # błąd już pokazany
        terminal.close_session()
        terminal.deleteLater()
        QMessageBox.information(self, t("btn_test_connection"), t("test_ok"))

    def accept(self):
        if not self.host.text().strip():
            QMessageBox.warning(self, t("err_missing_data_title"), t("err_missing_host"))
            return
        super().accept()

    def values(self):
        host = self.host.text().strip()
        protocol = self.protocol.currentData()
        # Zaczynamy od pól spoza formularza (bookmarks, tunnels...), żeby
        # edycja połączenia ich nie kasowała.
        data = {k: v for k, v in self._data.items() if k not in self._FORM_KEYS}
        data.update({
            "name": self.name.text().strip() or host,
            "host": host,
            "port": self.port.value(),
            "username": self.username.text().strip(),
            "protocol": protocol,
        })
        if protocol == "ssh" and self.key_file.text().strip():
            data["key_file"] = self.key_file.text().strip()
        if protocol == "ssh" and self.jump_host.text().strip():
            data["jump_host"] = self.jump_host.text().strip()
        if protocol == "ssh" and self.forward_agent.isChecked():
            data["forward_agent"] = True
        if protocol == "ssh" and self.startup.toPlainText().strip():
            data["startup"] = self.startup.toPlainText().strip()
        tags = parse_tags(self.tags.text())
        if tags:
            data["tags"] = tags
        if self.environment.currentData():
            data["environment"] = self.environment.currentData()
        if self.notes.toPlainText().strip():
            data["notes"] = self.notes.toPlainText().strip()
        if protocol == "rdp" and self.redirect_drives.isChecked():
            data["redirect_drives"] = True
        if self.monitor.isChecked():
            data["monitor"] = True
        if self.tls_port.value():
            data["tls_port"] = self.tls_port.value()
        if self.credential.currentData():
            # Hasła są na koncie — własnych nie trzymamy, żeby nie zostały
            # stare, zapomniane kopie obok.
            data["credential"] = self.credential.currentData()
            return data
        if self.save_password.isChecked() and self.password.text():
            data["password"] = encrypt_password(self.password.text())
        if protocol == "ssh" and self.save_password.isChecked() and self.passphrase.text():
            data["passphrase"] = encrypt_password(self.passphrase.text())
        return data


class ConnectionTree(QTreeWidget):
    """Drzewo grup i połączeń z menu kontekstowym do zarządzania nimi."""

    saved = Signal()  # po każdym zapisie — Home odświeża listę (np. przypięcie)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(False)
        self.setHeaderLabel(t("tree_header"))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        # Konta do dymków (nazwa konta, jego login) — odświeżane przy każdej
        # zmianie, formularz połączenia też potrafi dodać konto.
        self.credentials = []

        # Przenoszenie elementów myszą wewnątrz drzewa.
        self.setDragDropMode(QTreeWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

        root = QTreeWidgetItem([t("tree_root")])
        self.addTopLevelItem(root)
        # Korzenia nie da się przeciągnąć — wszystko ma zostać pod nim.
        root.setFlags(root.flags() & ~Qt.ItemIsDragEnabled)
        root.setExpanded(True)
        self.load()

    # --- nazwa i ikona ------------------------------------------------------

    @staticmethod
    def item_name(item):
        """Nazwa bez doklejonej ikony — to, co trafia do pliku."""
        icon = item.data(0, ICON_DATA) or ""
        text = item.text(0)
        return text[len(icon):].strip() if icon and text.startswith(icon) else text

    @staticmethod
    def set_label(item, name, icon=""):
        item.setData(0, ICON_DATA, icon)
        item.setText(0, f"{icon} {name}" if icon else name)

    # --- zapis i odczyt drzewa ---------------------------------------------

    def _serialize(self, item):
        node = {"name": self.item_name(item)}
        if item.data(0, ICON_DATA):
            node["icon"] = item.data(0, ICON_DATA)
        if item.data(0, COLOR_DATA):
            node["color"] = item.data(0, COLOR_DATA)
        if item.type() == CONNECTION_TYPE:
            node["connection"] = item.data(0, CONNECTION_DATA)
        else:
            if item.data(0, GROUP_DATA):
                node["defaults"] = item.data(0, GROUP_DATA)
            node["children"] = [
                self._serialize(item.child(i)) for i in range(item.childCount())
            ]
        return node

    def nodes(self):
        """Całe drzewo jako lista słowników — to samo, co ląduje w pliku."""
        root = self.topLevelItem(0)
        return [self._serialize(root.child(i)) for i in range(root.childCount())]

    def group_defaults(self, item):
        """Scalone ustawienia grup od korzenia do `item` — bliższa grupa wygrywa."""
        chain = []
        while item is not None:
            if item.type() != CONNECTION_TYPE and item.data(0, GROUP_DATA):
                chain.append(item.data(0, GROUP_DATA))
            item = item.parent()
        merged = {}
        for defaults in reversed(chain):
            merged.update(defaults)
        return merged

    def refresh_inheritance(self):
        """Każdemu połączeniu wpisuje ustawienia jego grup — po każdej zmianie drzewa
        (przeniesienie, edycja grupy), stąd wołane z `save()` i po wczytaniu."""
        self.credentials = credentials.load()[0]
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            if item.type() == CONNECTION_TYPE and isinstance(item.data(0, CONNECTION_DATA), dict):
                data = ConnectionData.wrap(item.data(0, CONNECTION_DATA))
                data.defaults = self.group_defaults(item.parent())
                self._apply_connection(item, data)  # dymek z loginem z grupy
            it += 1

    def _edit_group(self, item):
        dialog = GroupDialog(self, item.data(0, GROUP_DATA) or {})
        if dialog.exec() == QDialog.Accepted:
            item.setData(0, GROUP_DATA, dialog.values() or None)
            self.save()

    def save(self):
        """Zrzuca całe drzewo do JSON. Wołane po każdej zmianie."""
        self.refresh_inheritance()
        nodes = self.nodes()
        try:
            credentials.atomic_write_text(
                CONFIG_FILE, json.dumps(nodes, indent=2, ensure_ascii=False)
            )
        except OSError as error:
            QMessageBox.warning(
                self, t("err_save_title"), t("err_save_body", error)
            )
        self.saved.emit()

    def _build(self, parent, node):
        name = str(node.get("name", t("unnamed")))
        default_icon = "" if "connection" in node else GROUP_ICON
        if "connection" in node:
            item = QTreeWidgetItem(parent, [], CONNECTION_TYPE)
            self._apply_connection(item, node["connection"])
        else:
            item = QTreeWidgetItem(parent, [])
            if node.get("defaults"):
                item.setData(0, GROUP_DATA, dict(node["defaults"]))
            for child in node.get("children", []):
                self._build(item, child)
            item.setExpanded(True)
        self.set_label(item, name, str(node.get("icon", "") or default_icon))
        if node.get("color"):
            self.set_color(item, str(node["color"]))

    def load(self):
        self.credentials = credentials.load()[0]
        if not CONFIG_FILE.exists():
            return
        root = self.topLevelItem(0)
        try:
            nodes = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for node in nodes:
                self._build(root, node)
            self.refresh_inheritance()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            # Uszkodzonego pliku (zly JSON albo zla struktura wewnatrz) nie
            # nadpisujemy w ciszy — odkladamy kopie, zeby dalo sie odzyskac
            # wpisy recznie. Czesciowo zbudowane galezie tez wywalamy, zeby
            # nie zostac z ucietym drzewem bez ostrzezenia.
            root.takeChildren()
            backup = CONFIG_FILE.with_suffix(".json.bak")
            try:
                CONFIG_FILE.replace(backup)
            except OSError:
                backup = None
            QMessageBox.warning(
                self,
                t("err_load_title"),
                t("err_load_body", error)
                + (t("err_load_backup", backup) if backup else ""),
            )

    # --- eksport i import ---------------------------------------------------

    def export_to(self, path):
        """Ten sam format co connections.json — plik da się wprost podmienić."""
        credentials.atomic_write_text(
            path, json.dumps(self.nodes(), indent=2, ensure_ascii=False)
        )

    def import_from(self, path, replace):
        """Wczytuje drzewo z pliku: `replace` zastępuje wszystko, inaczej dopisuje.

        Zwraca liczbę wczytanych gałęzi najwyższego poziomu.
        """
        nodes = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(nodes, list):
            raise ValueError(t("err_not_a_list"))
        root = self.topLevelItem(0)
        if replace:
            root.takeChildren()
        for node in nodes:
            self._build(root, node)
        root.setExpanded(True)
        self.save()
        return len(nodes)

    def import_ssh_config(self, path=None):
        """Wpisy z `~/.ssh/config` jako nowa grupa. Zwraca liczbę połączeń.

        Parser jest w Paramiko (`SSHConfig`), więc nie piszemy własnego —
        rozumie `Include`, `Match` i skróty, których nasz i tak by nie ogarnął.
        """
        path = Path(path) if path else Path.home() / ".ssh" / "config"
        if not path.exists():
            return None  # brak pliku to nie błąd, tylko inny komunikat
        config = paramiko.SSHConfig.from_path(str(path))
        connections = []
        for name in sorted(config.get_hostnames()):
            if "*" in name or "?" in name:
                continue  # wzorzec, nie host — nie ma czego otwierać
            entry = config.lookup(name)
            data = {
                "name": name,
                "host": entry.get("hostname", name),
                "port": int(entry.get("port", SSH_PORT)),
                "username": entry.get("user", ""),
                "protocol": "ssh",
            }
            keys = entry.get("identityfile")
            if keys:
                data["key_file"] = str(Path(keys[0]).expanduser())
            connections.append(data)
        if not connections:
            return 0
        group = QTreeWidgetItem(self.topLevelItem(0), [])
        self.set_label(group, t("ssh_config_group"), GROUP_ICON)
        for data in connections:
            self._apply_connection(QTreeWidgetItem(group, [], CONNECTION_TYPE), data)
        group.setExpanded(True)
        self.save()
        return len(connections)

    def import_items(self, items, group_name):
        """[(folder, połączenie)] z `importers` jako nowa grupa. Zwraca liczbę połączeń."""
        if not items:
            return 0
        self._build(self.topLevelItem(0), {"name": group_name, "children": importers.nodes(items)})
        self.save()
        return len(items)

    def _drop_allowed(self, item, on_item):
        # Upuszczenie w pustym miejscu zrobiłoby element najwyższego poziomu,
        # obok korzenia — wtedy `save()` by go zgubił.
        # Połączenie nie jest grupą, więc nic nie może pod nie wejść.
        if item is None:
            return False
        return not (on_item and item.type() == CONNECTION_TYPE)

    def dropEvent(self, event):
        on_item = self.dropIndicatorPosition() == QTreeWidget.OnItem
        if not self._drop_allowed(self.itemAt(event.position().toPoint()), on_item):
            event.ignore()
            return
        super().dropEvent(event)
        self.save()

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        menu = QMenu(self)
        menu.addAction(t("menu_new_group"), lambda: self._add_group(item))
        menu.addAction(t("menu_new_connection"), lambda: self._add_connection(item))
        if item is not None and item is not self.topLevelItem(0):
            menu.addSeparator()
            if item.type() == CONNECTION_TYPE:
                menu.addAction(t("menu_edit_connection"), lambda: self._edit_connection(item))
                menu.addAction(t("menu_duplicate"), lambda: self._duplicate(item))
                pinned = item.data(0, CONNECTION_DATA).get("pinned")
                menu.addAction(t("menu_unpin" if pinned else "menu_pin"), lambda: self._toggle_pin(item))
            else:
                menu.addAction(t("menu_rename"), lambda: self._rename_group(item))
                menu.addAction(t("menu_group_settings"), lambda: self._edit_group(item))
            menu.addAction(t("menu_icon"), lambda: self._pick_icon(item))
            menu.addAction(t("menu_color"), lambda: self._pick_color(item))
            if item.data(0, COLOR_DATA):
                menu.addAction(t("menu_no_color"), lambda: self._clear_color(item))
            menu.addSeparator()
            menu.addAction(t("menu_delete"), lambda: self._remove_item(item))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _apply_connection(self, item, data):
        """Wpisuje dane połączenia do elementu drzewa (etykieta, tooltip, dane)."""
        data = ConnectionData.wrap(data)  # inaczej data() oddawałoby kopie, patrz wyżej
        item.setData(0, CONNECTION_DATA, data)
        cred = credentials.find(self.credentials, data.get("credential"))
        user = cred.get("username", "") if cred else data.get("username", "")
        item.setToolTip(
            0,
            f"{user}@{data.get('host', '')}:{data.get('port', 22)}"
            + (t("tip_credential", cred["name"]) if cred else "")
            + (t("tip_password_saved") if data.get("password") else "")
            + ("\n" + " ".join("#" + tag for tag in data["tags"]) if data.get("tags") else "")
            + (f"\n\n{data['notes']}" if data.get("notes") else ""),
        )
        self.set_label(item, data["name"], item.data(0, ICON_DATA) or "")

    def _edit_connection(self, item):
        data = item.data(0, CONNECTION_DATA)
        dialog = ConnectionDialog(self, data, inherited=data.defaults)
        if dialog.exec() != QDialog.Accepted:
            return
        self.credentials = credentials.load()[0]
        # W miejscu, nie nowy słownik: otwarta zakładka trzyma ten sam obiekt
        # (`origin`, `session.conn`) — nowy odciąłby ją od drzewa, a jej
        # zakładki SFTP/tunele zapisywałyby się do słownika, którego nikt nie zapisze.
        data = item.data(0, CONNECTION_DATA)
        data.clear()
        data.update(dialog.values())
        self._apply_connection(item, data)
        self.save()

    def _duplicate(self, item):
        """Kopia połączenia obok oryginału — szybsze niż przeklikanie formularza."""
        # Głęboka kopia: płytka dzieliła z oryginałem listy `bookmarks`/`tunnels`.
        data = copy.deepcopy(item.data(0, CONNECTION_DATA))
        data["name"] = t("copy_suffix", data.get("name", ""))
        data.pop("pinned", None)  # kopia nie wskakuje sama na Home
        clone = QTreeWidgetItem(item.parent(), [], CONNECTION_TYPE)
        clone.setData(0, ICON_DATA, item.data(0, ICON_DATA))
        self._apply_connection(clone, data)
        if item.data(0, COLOR_DATA):
            self.set_color(clone, item.data(0, COLOR_DATA))
        self.save()
        return clone

    def _toggle_pin(self, item):
        """Przypięte połączenia stoją na górze listy Home, przed ostatnio używanymi."""
        data = item.data(0, CONNECTION_DATA)
        if data.pop("pinned", False) is False:
            data["pinned"] = True
        self.save()

    def filter(self, query):
        """Chowa wpisy, które nie pasują; grupa zostaje, gdy coś w niej pasuje."""
        root = self.topLevelItem(0)
        for i in range(root.childCount()):
            self._filter_item(root.child(i), query.strip().lower())

    def _filter_item(self, item, query):
        """Zwraca True, gdy element (albo cokolwiek pod nim) pasuje do zapytania."""
        data = item.data(0, CONNECTION_DATA) or {}
        haystack = self.item_name(item).lower() + " " + search_text(data)
        hit = not query or query in haystack
        for i in range(item.childCount()):
            # Bez `or hit` po prawej krótkie spięcie pominęłoby chowanie dzieci.
            hit = self._filter_item(item.child(i), query) or hit
        item.setHidden(not hit)
        if query and hit and item.childCount():
            item.setExpanded(True)
        return hit

    def _rename_group(self, item):
        name, ok = QInputDialog.getText(
            self, t("dlg_rename_title"), t("dlg_group_name"), text=self.item_name(item)
        )
        if not ok or not name:
            return
        self.set_label(item, name, item.data(0, ICON_DATA) or "")
        self.save()

    @staticmethod
    def set_color(item, color):
        """Kolor tekstu elementu i wszystkiego, co pod nim (pusty = domyślny)."""
        item.setData(0, COLOR_DATA, color or None)
        brush = QBrush(QColor(color)) if color else QBrush()
        item.setForeground(0, brush)
        for i in range(item.childCount()):
            ConnectionTree.set_color(item.child(i), color)

    def _pick_color(self, item):
        current = QColor(item.data(0, COLOR_DATA) or "#ffffff")
        color = QColorDialog.getColor(current, self, t("dlg_group_color"))
        if not color.isValid():
            return
        self.set_color(item, color.name())
        self.save()

    def _clear_color(self, item):
        self.set_color(item, "")
        self.save()

    def _pick_icon(self, item):
        choices = ICONS + [t("icon_none")]
        current = item.data(0, ICON_DATA) or ""
        icon, ok = QInputDialog.getItem(
            self,
            t("dlg_icon_title"),
            t("dlg_icon_prompt"),
            choices,
            choices.index(current) if current in choices else len(choices) - 1,
            False,
        )
        if not ok:
            return
        self.set_label(item, self.item_name(item), "" if icon == choices[-1] else icon)
        self.save()

    def _add_group(self, parent_item):
        name, ok = QInputDialog.getText(self, t("menu_new_group"), t("dlg_group_name"))
        if not ok or not name:
            return
        parent_item = parent_item or self.topLevelItem(0)
        self.set_label(QTreeWidgetItem(parent_item, []), name, GROUP_ICON)
        parent_item.setExpanded(True)
        self.save()

    def _add_connection(self, parent_item):
        parent_item = parent_item or self.topLevelItem(0)
        if parent_item.type() == CONNECTION_TYPE:
            parent_item = parent_item.parent()  # klik na połączeniu = nowe obok niego
        dialog = ConnectionDialog(self, inherited=self.group_defaults(parent_item))
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.values()
        self.credentials = credentials.load()[0]
        item = QTreeWidgetItem(parent_item, [], CONNECTION_TYPE)
        self._apply_connection(item, data)
        parent_item.setExpanded(True)
        self.save()

    def _remove_item(self, item):
        parent = item.parent()
        if parent is None:
            return
        body = (
            t("confirm_delete_group_body", self.item_name(item), item.childCount())
            if item.childCount() else t("confirm_delete_body", self.item_name(item))
        )
        if QMessageBox.question(
            self, t("confirm_delete_group_title"), body,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        parent.removeChild(item)
        self.save()

    def keyPressEvent(self, event):
        """Delete usuwa, F2 edytuje połączenie / zmienia nazwę grupy."""
        item = self.currentItem()
        if item is not None and item.parent() is not None:
            if event.key() == Qt.Key_Delete:
                self._remove_item(item)
                return
            if event.key() == Qt.Key_F2:
                if item.type() == CONNECTION_TYPE:
                    self._edit_connection(item)
                else:
                    self._rename_group(item)
                return
        super().keyPressEvent(event)

    # --- kropka statusu (żywy/martwy serwer) --------------------------------

    def refresh_tooltips(self):
        """Po zmianach w menedżerze poświadczeń — dymki pokazują nazwę/login konta."""
        self.credentials = credentials.load()[0]
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            if item.type() == CONNECTION_TYPE:
                self._apply_connection(item, item.data(0, CONNECTION_DATA))
            it += 1

    def status_targets(self):
        """{id(item): (host, port)} dla wszystkich zapisanych połączeń."""
        targets = {}
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            if item.type() == CONNECTION_TYPE:
                data = item.data(0, CONNECTION_DATA) or {}
                host = data.get("host")
                if host:
                    port = data.get("port") or (RDP_PORT if data.get("protocol") == "rdp" else 22)
                    targets[id(item)] = (host, int(port))
            it += 1
        return targets

    def apply_status(self, results):
        """Nakłada wynik `_TreeStatusCheck` (id(item) -> bool) na ikony."""
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            if id(item) in results:
                online = results[id(item)]
                item.setIcon(0, status_icon(online))
                item.setToolTip(0, t("status_online") if online else t("status_offline"))
            it += 1


def tree_connections(tree):
    """Wszystkie zapisane połączenia z drzewa, płasko (żywe słowniki)."""
    found = []
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        item = it.value()
        if item.type() == CONNECTION_TYPE:
            found.append(item.data(0, CONNECTION_DATA) or {})
        it += 1
    return found


def home_order(connections):
    """Przypięte na górze, potem ostatnio używane; nigdy nieotwierane w kolejności drzewa."""
    return sorted(connections, key=lambda d: (not d.get("pinned"), -d.get("last_used", 0)))


class HomeTab(QWidget):
    """Pulpit startowy — pierwsza, niezamykalna zakładka (wzorem MobaXterm)."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.addStretch()

        title = QLabel(t("app_title"))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel(t("home_subtitle"))
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        buttons = QHBoxLayout()
        buttons.addStretch()
        quick_btn = QPushButton(t("home_quick_btn"))
        quick_btn.clicked.connect(main_window._quick_connect)
        buttons.addWidget(quick_btn)
        saved_btn = QPushButton(t("home_saved_btn"))
        saved_btn.clicked.connect(lambda: main_window.tree._add_connection(None))
        buttons.addWidget(saved_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        # Kafelki monitoringu w tle (monitor.py) — zwykła lista w trybie ikon,
        # bez własnego widżetu; schowane, gdy nic nie jest monitorowane.
        self.tiles_title = QLabel(t("home_monitor_title"))
        self.tiles_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.tiles_title)
        self.tiles = QListWidget()
        self.tiles.setViewMode(QListWidget.IconMode)
        self.tiles.setResizeMode(QListWidget.Adjust)
        self.tiles.setMovement(QListWidget.Static)
        self.tiles.setGridSize(QSize(220, 64))
        self.tiles.setWordWrap(True)
        self.tiles.setSpacing(4)
        self.tiles.setMaximumHeight(160)
        self.tiles.itemActivated.connect(self._open_item)
        self.tiles.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tiles.customContextMenuRequested.connect(self._tile_menu)
        layout.addWidget(self.tiles)

        # Wyszukiwarka zapisanych połączeń — po nazwie, hoście i użytkowniku.
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("home_search_placeholder"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self._open_first)
        layout.addWidget(self.search)

        self.results = QListWidget()
        # Samo itemActivated — na Windows dwuklik też je emituje, a podpięte
        # dodatkowo itemDoubleClicked otwierało połączenie (i pytanie o hasło) dwa razy.
        self.results.itemActivated.connect(self._open_item)
        layout.addWidget(self.results)
        layout.addStretch()
        main_window.tree.saved.connect(self.refresh)
        main_window.tree.saved.connect(self.refresh_tiles)
        self.refresh()
        self.refresh_tiles()

    def showEvent(self, event):
        # Lista mogła się zmienić, gdy Home był schowany.
        super().showEvent(event)
        self.refresh()
        self.refresh_tiles()

    def connections(self):
        return tree_connections(self.main_window.tree)

    @staticmethod
    def matches(data, query):
        query = query.strip().lower()
        if not query:
            return True
        return query in search_text(data)

    def refresh(self):
        self.results.clear()
        for data in home_order(self.connections()):
            if not self.matches(data, self.search.text()):
                continue
            label = f"{data.get('name', '')} — {data.get('username', '')}@{data.get('host', '')}"
            if data.get("pinned"):
                label = "📌 " + label
            if data.get("last_used"):
                used = time.strftime("%Y-%m-%d %H:%M", time.localtime(data["last_used"]))
                label += "   " + t("home_last_used", used)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, data)
            self.results.addItem(item)

    def refresh_tiles(self):
        """Kafelek na każde monitorowane połączenie, kolor wg ostatniego wyniku."""
        self.tiles.clear()
        results = self.main_window.monitor_results
        threshold = alert_threshold()
        for data in self.connections():
            if not data.get("monitor"):
                continue
            key = monitor.target_key(data)
            result = results.get(key)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, data)
            if result is None:
                item.setText(f"{data.get('name', '')}\n{t('monitor_waiting')}")
            else:
                state = monitor.status(result, threshold)
                item.setText(f"{data.get('name', '')}\n{monitor.summary(result)}")
                color = QColor(monitor.COLORS[state])
                color.setAlpha(110)  # półprzezroczysty — czytelny w obu motywach
                item.setBackground(QBrush(color))
                tip = [result.get("error", ""), result.get("cert_error", ""), monitor.history_text(key)]
                item.setToolTip("\n".join(line for line in tip if line))
            self.tiles.addItem(item)
        visible = self.tiles.count() > 0
        self.tiles.setVisible(visible)
        self.tiles_title.setVisible(visible)

    def _open_item(self, item):
        self.main_window._open_connection_tab(item.data(Qt.UserRole))

    def _tile_menu(self, point):
        item = self.tiles.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction(t("monitor_history_menu"), lambda: self.history_dialog(item.data(Qt.UserRole)))
        menu.exec(self.tiles.viewport().mapToGlobal(point))

    def history_dialog(self, data, show=True):
        """Duży wykres CPU/RAM z `monitor.db` (24 h / 7 dni / 30 dni) + eksport CSV."""
        key = monitor.target_key(data)
        name = data.get("name", key)
        dialog = QDialog(self)
        dialog.setWindowTitle(t("monitor_history_title", name))
        span = QComboBox()
        for label, hours in (
            ("monitor_range_day", 24),
            ("monitor_range_week", 24 * 7),
            ("monitor_range_month", 24 * monitor.KEEP_DAYS),
        ):
            span.addItem(t(label), hours)
        export = QPushButton(t("monitor_export_csv"))
        top = QHBoxLayout()
        top.addWidget(span)
        top.addStretch()
        top.addWidget(export)
        graph = graphs.StatsGraph(dialog)
        graph.setFixedSize(576, 200)
        legend = QLabel(t("monitor_history_legend", graphs.CPU_COLOR, graphs.RAM_COLOR))
        summary = QLabel()
        layout = QVBoxLayout(dialog)
        layout.addLayout(top)
        layout.addWidget(graph)
        layout.addWidget(legend)
        layout.addWidget(summary)

        def show_range():
            hours = span.currentData()
            try:
                # Najwyżej punkt na piksel — 30 dni to ~8600 próbek, uśredniane w bazie.
                points = monitor.samples(key, hours, buckets=graph.width())
            except sqlite3.Error:
                points = []  # baza zablokowana zapisem rundy — pokaż „brak pomiarów”
            graph.step = max(1, graph.width() // max(1, len(points)))
            graph.set_history(points)
            graph.setVisible(bool(points))
            legend.setVisible(bool(points))
            summary.setText(
                monitor.history_text(key, hours, span.currentText()) if points
                else t("monitor_history_empty")
            )

        def save_csv():
            try:
                text = monitor.export_csv(key, span.currentData())
            except sqlite3.Error as error:
                QMessageBox.warning(dialog, t("monitor_export_csv"), str(error))
                return
            safe = re.sub(r"[^\w.-]+", "_", name).strip("_") or "historia"
            save_text(dialog, text, f"{safe}.csv", t("monitor_export_csv"), t("csv_filter"))

        span.currentIndexChanged.connect(show_range)
        export.clicked.connect(save_csv)
        show_range()
        dialog.span, dialog.graph, dialog.summary = span, graph, summary  # dla selftestu
        if show:
            dialog.exec()
        return dialog

    def _open_first(self):
        if self.results.count():
            self._open_item(self.results.item(0))


def menu_entries(menu_bar, skip=()):
    """Akcje z paska menu jako („Menu › Akcja”, akcja) — dla palety poleceń.

    Z podmenu rekurencyjnie; separatory, podmenu same w sobie i akcje
    wyłączone pomijane. `skip` = teksty akcji do pominięcia (sama paleta).
    """
    found = []

    def walk(menu, prefix):
        for action in menu.actions():
            if action.isSeparator() or not action.isEnabled():
                continue
            text = action.text().replace("&", "").rstrip("…").strip()
            if action.menu():
                walk(action.menu(), f"{prefix}{text} › ")
            elif action.text() not in skip:
                found.append((prefix + text, action))

    for top in menu_bar.actions():
        if top.menu():
            walk(top.menu(), top.text().replace("&", "") + " › ")
    return found


def palette_matches(haystack, query):
    """Każde słowo zapytania musi wystąpić gdzieś w tekście (kolejność dowolna)."""
    haystack = haystack.lower()
    return all(word in haystack for word in query.lower().split())


class CommandPalette(QDialog):
    """Ctrl+Shift+P: jedno pole po połączeniach, akcjach menu i skryptach.

    Skrypty i Programy to zwykłe akcje z paska menu, więc nie ma osobnej
    listy do utrzymywania — nowy wpis w menu sam trafia do palety.
    Wybrana akcja odpala się dopiero po zamknięciu okna (`chosen`), żeby jej
    własne dialogi nie wisiały pod paletą.
    """

    def __init__(self, parent, entries):
        super().__init__(parent)
        self.entries = entries  # [(etykieta, tekst do szukania, funkcja)]
        self.chosen = None
        self.setWindowTitle(t("menu_command_palette").rstrip("…"))
        self.resize(560, 380)
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText(t("palette_placeholder"))
        self.query.textChanged.connect(self.refresh)
        self.query.returnPressed.connect(self._choose_current)
        layout.addWidget(self.query)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._choose)
        layout.addWidget(self.results)
        self.refresh()

    def refresh(self):
        self.results.clear()
        for label, haystack, run in self.entries:
            if palette_matches(haystack, self.query.text()):
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, run)
                self.results.addItem(item)
        self.results.setCurrentRow(0)

    def keyPressEvent(self, event):
        # Strzałki z pola tekstowego przesuwają wybór na liście pod nim.
        if event.key() in (Qt.Key_Up, Qt.Key_Down) and self.results.count():
            step = -1 if event.key() == Qt.Key_Up else 1
            row = max(0, min(self.results.count() - 1, self.results.currentRow() + step))
            self.results.setCurrentRow(row)
            return
        super().keyPressEvent(event)

    def _choose_current(self):
        if self.results.currentItem():
            self._choose(self.results.currentItem())

    def _choose(self, item):
        self.chosen = item.data(Qt.UserRole)
        self.accept()


STATS_SEPARATOR = "   |   "  # tak `format_stats` skleja części paska


def dashboard_row(name, stats):
    """Tekst paska statystyk -> komórki wiersza tabeli (serwer + 6 kolumn).

    Czysta funkcja na gotowym tekście z `format_stats`, bez drugiego liczenia.
    Etykieta z przodu („CPU”, „dysk”) wylatuje, bo stoi już w nagłówku;
    sieć (↓/↑) zostaje w całości. Inny tekst (koniec sesji RDP, brak /proc)
    idzie w całości do drugiej kolumny.
    """
    parts = stats.split(STATS_SEPARATOR)
    if len(parts) != 6:
        return [name, stats, "", "", "", "", ""]
    return [name] + [p if i == 3 else p.split(" ", 1)[-1] for i, p in enumerate(parts)]


def over_threshold(text, threshold):
    """Pierwszy „NN%” w komórce na progu albo ponad nim."""
    match = re.search(r"(\d+)%", text)
    return bool(match) and int(match.group(1)) >= threshold


class StatsDashboard(QDialog):
    """Tabela statystyk wszystkich otwartych sesji naraz, nie tylko aktywnej.

    `_StatsPoller` już liczy to per zakładka (`SessionTab.last_stats`) — okno
    tylko odczytuje gotowy tekst co 2 s, bez własnego odpytywania serwerów.
    Komórki są przepisywane w miejscu, nie czyszczone — inaczej odświeżenie
    gubiło przewinięcie i zaznaczenie.
    """

    def __init__(self, parent, main_window):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle(t("dashboard_title"))
        self.resize(900, 300)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            t("dashboard_server"), "CPU", "RAM", t("stats_disk"),
            t("dashboard_network"), t("stats_uptime"), t("stats_users"),
        ])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.empty = QLabel(t("dashboard_no_sessions"))
        layout.addWidget(self.empty)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def refresh(self):
        tabs = self.main_window.tabs
        rows = []
        for i in range(tabs.count()):
            widget = tabs.widget(i)
            stats = getattr(widget, "last_stats", "")
            if stats:
                rows.append(dashboard_row(getattr(widget, "tab_name", tabs.tabText(i)), stats))
        self.empty.setVisible(not rows)
        self.table.setRowCount(len(rows))
        threshold = alert_threshold()
        hot = QBrush(QColor(220, 50, 50, 90))  # półprzezroczysty — czytelny w obu motywach
        for row, cells in enumerate(rows):
            for column, text in enumerate(cells):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setBackground(
                    hot if column in (1, 2, 3) and over_threshold(text, threshold) else QBrush()
                )

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(t("app_title"))
        self.resize(1000, 650)

        self.tree = ConnectionTree()
        # itemActivated = dwuklik ORAZ Enter (dwuklik sam w sobie pomijał klawiaturę).
        self.tree.itemActivated.connect(self._on_item_activated)
        self.tree.saved.connect(self._refresh_tab_texts)  # np. zmienione środowisko

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._show_current_stats)
        self.tabs.tabBarClicked.connect(self._on_tab_bar_clicked)
        self.tabs.currentChanged.connect(self._clear_activity)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)

        # Monitoring w tle (monitor.py): ostatni wynik i stan per host:port.
        self.monitor_results = {}
        self._monitor_states = {}
        self._monitor_timer = None  # startuje z main(), jak status drzewa
        self._monitor_round = None

        # "Home": pulpit startowy, zawsze pierwsza zakładka, bez przycisku zamknięcia.
        home_index = self.tabs.addTab(HomeTab(self), t("tab_home"))
        self.tabs.tabBar().setTabButton(home_index, QTabBar.RightSide, None)

        # "+" jako ostatnia zakładka w pasku (jak nowa karta w przeglądarce) —
        # nie jak zwykła zakładka: klik ma otwierać dialog, a nie stawać się aktywny.
        self._plus_tab = QWidget()
        plus_index = self.tabs.addTab(self._plus_tab, "+")
        self.tabs.tabBar().setTabButton(plus_index, QTabBar.RightSide, None)
        self.tabs.setTabToolTip(plus_index, t("dlg_quick_title"))

        # Drzewo z polem filtru nad nim — chowanie z menu dotyczy całej kolumny,
        # więc do splittera idzie kontener, nie samo drzewo.
        self.tree_filter = QLineEdit()
        self.tree_filter.setPlaceholderText(t("tree_filter_placeholder"))
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.textChanged.connect(self.tree.filter)
        self.tree_panel = QWidget()
        tree_layout = QVBoxLayout(self.tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self.tree_filter)
        tree_layout.addWidget(self.tree)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree_panel)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 750])

        self.setCentralWidget(splitter)
        self.splitter = splitter
        self._servers = {}  # uruchomione serwery wbudowane: etykieta -> obiekt
        self._build_menu()
        self._build_sidebar()
        TerminalHighlighter.enabled = i18n.settings().value("highlighting", True, type=bool)
        SshTerminal.timestamps = i18n.settings().value("timestamps", False, type=bool)
        self._build_shortcuts()
        # Dolny pasek: statystyki serwera z aktywnej zakładki, wykres CPU/RAM z prawej.
        self.statusBar().showMessage(t("status_idle"))
        self.stats_graph = graphs.StatsGraph()
        self.statusBar().addPermanentWidget(self.stats_graph)
        self._restore_layout()
        self._update_check = None  # wątek startuje z main(), nie w testach
        self._status_timer = None  # tak samo — --selftest nie ma chodzić po sieci
        self._status_check = None
        self._lock_timer = None  # tak samo — event filter startuje z main()
        self._locked = False

    # --- aktualizacja ------------------------------------------------------

    def check_updates(self):
        """Pytanie o nowszą wersję leci w tle — sieć nie może opóźniać okna."""
        self._update_check = update.UpdateCheck(self)
        self._update_check.outdated.connect(self._offer_update)
        self._update_check.start()

    def _offer_update(self, revision):
        answer = QMessageBox.question(self, t("update_title"), t("update_body", revision))
        if answer != QMessageBox.Yes:
            return
        error = in_background(self, update.pull)  # git pull: do 60 s, nie na wątku GUI
        if error:
            QMessageBox.warning(self, t("update_title"), t("update_failed", error))
        else:
            QMessageBox.information(self, t("update_title"), t("update_done"))

    # --- status serwerów w drzewie -----------------------------------------

    def start_status_polling(self):
        """Wołane z `main()`, nie z `__init__` — inaczej `--selftest` chodziłby po sieci."""
        if i18n.settings().value("tree_status_enabled", True, type=bool):
            self._start_status_timer()

    def _start_status_timer(self):
        interval = int(i18n.settings().value("tree_status_interval", STATUS_INTERVAL_DEFAULT))
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._poll_status_once)
        self._status_timer.start(interval * 1000)
        self._poll_status_once()  # od razu, nie czekaj na pierwszy odstęp

    def _poll_status_once(self):
        """Cele zbiera GUI thread (bezpieczny odczyt drzewa); gniazda idą w tle."""
        targets = self.tree.status_targets()
        if not targets:
            return
        self._status_check = _TreeStatusCheck(targets)
        self._status_check.result.connect(self.tree.apply_status)
        self._status_check.start()

    def _stop_status_timer(self):
        if self._status_timer:
            self._status_timer.stop()
            self._status_timer = None
        if self._status_check:
            self._status_check.wait()  # inaczej Qt wywala proces przy zamykaniu
            self._status_check = None

    # --- monitoring serwerów w tle (monitor.py) -----------------------------

    def start_monitoring(self):
        """Wołane z `main()`, nie z `__init__` — `--selftest` nie ma chodzić po sieci."""
        interval = int(i18n.settings().value("monitor_interval", monitor.INTERVAL_DEFAULT))
        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(self._monitor_once)
        self._monitor_timer.start(interval * 1000)
        # Nowo zaznaczony serwer ma dostać kafelek od razu, nie po 5 minutach.
        self.tree.saved.connect(self._monitor_new)
        self._monitor_once()  # od razu, nie czekaj na pierwszy odstęp

    def _monitor_targets(self):
        """Cele z odszyfrowanymi hasłami — zbierane na wątku GUI (odczyt drzewa)."""
        targets = {}
        for conn in tree_connections(self.tree):
            if not conn.get("monitor") or not conn.get("host"):
                continue
            auth = credentials.effective_auth(conn)
            protocol = conn.get("protocol", "ssh")
            targets[monitor.target_key(conn)] = {
                "name": conn.get("name", conn["host"]),
                "protocol": protocol,
                "host": conn["host"],
                "port": int(conn.get("port") or (RDP_PORT if protocol == "rdp" else SSH_PORT)),
                "jump_host": conn.get("jump_host"),
                "username": auth["username"],
                "key_file": auth["key_file"],
                "password": decrypt_password(auth["password"]) if auth["password"] else "",
                "passphrase": decrypt_password(auth["passphrase"]) if auth["passphrase"] else "",
                "tls_port": conn.get("tls_port"),
            }
        return targets

    def _monitor_once(self):
        if self._monitor_round and self._monitor_round.isRunning():
            return  # poprzednia runda jeszcze trwa — nie dokładamy drugiej
        targets = self._monitor_targets()
        if not targets:
            return
        self._monitor_names = {key: target["name"] for key, target in targets.items()}
        self._monitor_round = monitor.MonitorRound(targets)
        self._monitor_round.done.connect(self._on_monitor_done)
        self._monitor_round.start()

    def _monitor_new(self):
        if any(key not in self.monitor_results for key in self._monitor_targets()):
            self._monitor_once()

    def _on_monitor_done(self, results):
        threshold = alert_threshold()
        alerts = []
        for key, result in results.items():
            state = monitor.status(result, threshold)
            text = monitor.alert_text(
                self._monitor_names.get(key, key), self._monitor_states.get(key), state, result
            )
            if text:
                notify.notify(t("monitor_title"), text)
                alerts.append(text)
            self._monitor_states[key] = state
        token, chat = telegram_config()
        if alerts and token and chat:
            notify.telegram_async(token, chat, "\n".join(alerts))  # jedna wiadomość na rundę
        self.monitor_results.update(results)
        self.tabs.widget(0).refresh_tiles()

    def _stop_monitoring(self):
        if self._monitor_timer:
            self._monitor_timer.stop()
        if self._monitor_round:
            # Zrywa połączenia w locie — okno zamyka się od razu, nie po CHECK_TIMEOUT.
            self._monitor_round.cancel()
            self._monitor_round.wait()

    # --- blokada okna po bezczynności --------------------------------------

    def start_lock_watch(self):
        """Wołane z `main()`, nie z `__init__` — inaczej `--selftest` instalowałby
        globalny event filter i mógłby się zablokować sam sobie."""
        QApplication.instance().installEventFilter(self)
        if i18n.settings().value("lock_enabled", False, type=bool):
            self._arm_lock_timer()

    def eventFilter(self, obj, event):
        if (
            not self._locked
            and event.type() in _ACTIVITY_EVENTS
            and i18n.settings().value("lock_enabled", False, type=bool)
        ):
            self._arm_lock_timer()
        return super().eventFilter(obj, event)

    def _arm_lock_timer(self):
        if self._lock_timer is None:
            self._lock_timer = QTimer(self)
            self._lock_timer.setSingleShot(True)
            self._lock_timer.timeout.connect(self._lock_now)
        minutes = int(i18n.settings().value("lock_timeout", LOCK_TIMEOUT_DEFAULT))
        self._lock_timer.start(minutes * 60 * 1000)

    def _lock_now(self):
        if self._locked or not i18n.settings().value("lock_enabled", False, type=bool):
            return
        self._locked = True
        _LockDialog(self).exec()
        self._locked = False
        self._arm_lock_timer()  # odliczanie od nowa po odblokowaniu

    def _toggle_lock(self, on):
        if on and not i18n.settings().value("lock_pin_hash"):
            pin, ok = QInputDialog.getText(
                self, t("lock_set_pin_title"), t("lock_set_pin_prompt"), QLineEdit.Password
            )
            if not ok or len(pin) < 4:
                if ok:
                    QMessageBox.warning(self, t("lock_set_pin_title"), t("lock_pin_too_short"))
                return False
            i18n.settings().setValue("lock_pin_hash", _hash_pin(pin))
        i18n.settings().setValue("lock_enabled", on)
        if on:
            self._arm_lock_timer()
        elif self._lock_timer:
            self._lock_timer.stop()
        return True

    # --- układ okna między uruchomieniami ---------------------------------

    def _restore_layout(self):
        """Rozmiar okna i podział splittera z poprzedniej sesji."""
        stored = i18n.settings()
        geometry = stored.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        sizes = stored.value("splitter")
        if sizes:
            # QSettings oddaje listę tekstów, nie liczb.
            self.splitter.setSizes([int(value) for value in sizes])

    def _save_layout(self):
        stored = i18n.settings()
        stored.setValue("geometry", self.saveGeometry())
        stored.setValue("splitter", self.splitter.sizes())
        stored.setValue("open_sessions", json.dumps(self.open_session_keys()))

    def open_session_keys(self):
        """Otwarte zakładki z drzewa jako (nazwa, host, port) — do przywrócenia po starcie.

        Szybkie połączenia (bez wpisu w drzewie) pomijane — nie ma czego otworzyć.
        ponytail: siatka (split.py) wraca jako zwykłe zakładki, bez układu.
        """
        keys = []
        for i in range(self.tabs.count()):
            origin = getattr(self.tabs.widget(i), "origin", None)
            if origin:
                key = [origin.get("name"), origin.get("host"), origin.get("port")]
                if key not in keys:  # zduplikowana sesja wraca raz
                    keys.append(key)
        return keys

    def restore_sessions(self):
        """Wołane z `main()` — `--selftest` nie ma się łączyć z serwerami."""
        stored = i18n.settings()
        if not stored.value("restore_sessions", True, type=bool):
            return
        try:
            keys = json.loads(stored.value("open_sessions", "[]") or "[]")
        except ValueError:
            return
        connections = tree_connections(self.tree)
        for key in keys:
            conn = next(
                (c for c in connections if [c.get("name"), c.get("host"), c.get("port")] == key),
                None,
            )
            if conn is not None:  # usunięte w międzyczasie — pomijamy po cichu
                self._open_connection_tab(conn)

    def _build_menu(self):
        """Pasek menu u góry (wzorem MobaXterm), z akcjami znanymi już z menu drzewa."""
        menu = self.menuBar()

        connection_menu = menu.addMenu(t("menu_connection"))
        connection_menu.addAction(t("menu_new_group_dots"), lambda: self.tree._add_group(self.tree.currentItem()))
        connection_menu.addAction(t("menu_new_connection_dots"), lambda: self.tree._add_connection(self.tree.currentItem()))
        connection_menu.addSeparator()
        connection_menu.addAction(t("menu_export"), self._export_connections)
        connection_menu.addAction(t("menu_import"), self._import_connections)
        connection_menu.addAction(t("menu_import_ssh_config"), self._import_ssh_config)
        other = connection_menu.addMenu(t("menu_import_other"))
        for label, source in IMPORT_SOURCES:
            other.addAction(t(label), lambda s=source, l=label: self._import_from_program(s, l))
        connection_menu.addSeparator()
        connection_menu.addAction(t("menu_quit"), self.close)

        view_menu = menu.addMenu(t("menu_view"))
        self.toggle_tree_action = QAction(t("menu_connection_list"), self, checkable=True, checked=True)
        self.toggle_tree_action.toggled.connect(self.tree_panel.setVisible)
        view_menu.addAction(self.toggle_tree_action)
        view_menu.addAction(t("menu_save_log"), self._save_session_log)
        view_menu.addSeparator()
        # Ustawienia w jednym oknie zamiast kilkunastu pozycji tutaj.
        view_menu.addAction(t("menu_settings"), self._open_settings, QKeySequence("Ctrl+Shift+S"))
        view_menu.addAction(t("menu_command_palette"), self._open_palette, QKeySequence("Ctrl+Shift+P"))

        # „Serwery wbudowane" — daemony po naszej stronie, wzorem MobaXterm.
        servers_menu = menu.addMenu(t("menu_servers"))
        for spec in SERVERS:
            action = QAction(t(spec["label"]), self, checkable=True)
            action.triggered.connect(lambda _checked, s=spec, a=action: self._toggle_server(s, a))
            servers_menu.addAction(action)
        servers_menu.addSeparator()
        servers_menu.addAction(t("menu_stop_all"), self._stop_servers)

        # Skrypty użytkownika z `scripts.json` dochodzą do listy przed zbudowaniem
        # menu — inaczej dopisane wpisy nie miałyby gdzie się pokazać.
        error = load_user_scripts()
        if error:
            QMessageBox.warning(self, t("menu_scripts"), t("err_user_scripts", error))
        scripts_menu = menu.addMenu(t("menu_scripts"))
        for script in SCRIPTS:
            scripts_menu.addAction(script_label(script), lambda s=script: self._run_script(s))

        # Dodatkowe programy: jedna lista, żeby kolejny narzędziowy dodatek
        # był jednym wpisem, a nie kolejnym menu do zbudowania.
        tools_menu = menu.addMenu(t("menu_tools"))
        for label, handler in TOOLS:
            tools_menu.addAction(t(label), getattr(self, handler))

        # Biblioteka poleceń (makra z Ustawień): menu = paleta (Ctrl+Shift+P)
        # i skróty za darmo — skrót akcji menu działa w całym oknie.
        self.snippet_menu = menu.addMenu(t("menu_snippets"))
        self._fill_snippet_menu()

        help_menu = menu.addMenu(t("menu_help"))
        help_menu.addAction(t("menu_shortcuts"), self._show_shortcuts)
        help_menu.addAction(t("menu_about"), self._show_about)

    def palette_entries(self):
        """Połączenia (po nazwie, hoście, loginie, #tagu) + wszystkie akcje menu."""
        entries = []
        for data in home_order(tree_connections(self.tree)):
            label = f"🖥 {data.get('name', '')} — {data.get('username', '')}@{data.get('host', '')}"
            entries.append((label, search_text(data), lambda d=data: self._open_connection_tab(d)))
        for label, action in menu_entries(self.menuBar(), skip={t("menu_command_palette")}):
            entries.append((label, label, action.trigger))
        return entries

    def _open_palette(self):
        palette = CommandPalette(self, self.palette_entries())
        if palette.exec() and palette.chosen:
            palette.chosen()

    def _export_connections(self):
        path, _ = QFileDialog.getSaveFileName(
            self, t("dlg_export_title"), t("export_default_name"), t("json_filter")
        )
        if not path:
            return
        try:
            self.tree.export_to(path)
        except OSError as error:
            QMessageBox.warning(self, t("export_short"), t("err_export", error))
            return
        QMessageBox.information(
            self,
            t("export_short"),
            t("export_done", path),
        )

    def _import_connections(self):
        path, _ = QFileDialog.getOpenFileName(self, t("dlg_import_title"), "", t("json_filter"))
        if not path:
            return
        answer = QMessageBox.question(
            self,
            t("import_short"),
            t("import_question"),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.No,
        )
        if answer == QMessageBox.Cancel:
            return
        try:
            count = self.tree.import_from(path, answer == QMessageBox.Yes)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            QMessageBox.warning(self, t("import_short"), t("err_import", error))
            return
        QMessageBox.information(self, t("import_short"), t("import_done", count))

    # --- okno Ustawień -----------------------------------------------------

    def current_settings(self):
        stored = i18n.settings()
        return {
            "highlighting": TerminalHighlighter.enabled,
            "timestamps": SshTerminal.timestamps,
            "session_log": session_log_enabled(),
            "restore_sessions": stored.value("restore_sessions", True, type=bool),
            "font": terminal_font(),
            "scrollback": scrollback(),
            "triggers": triggers_text(),
            "macros": macros_text(),
            "theme": terminal_theme(),
            "dark_mode": stored.value("dark_mode", False, type=bool),
            "language": i18n.language(),
            "tree_status": stored.value("tree_status_enabled", True, type=bool),
            "tree_status_interval": int(
                stored.value("tree_status_interval", STATUS_INTERVAL_DEFAULT)
            ),
            "alerts": alerts_enabled(),
            "alert_threshold": alert_threshold(),
            "monitor_interval": int(stored.value("monitor_interval", monitor.INTERVAL_DEFAULT)),
            "telegram_token": telegram_config()[0],
            "telegram_chat": telegram_config()[1],
            "lock": stored.value("lock_enabled", False, type=bool),
            "lock_timeout": int(stored.value("lock_timeout", LOCK_TIMEOUT_DEFAULT)),
            "pin_set": bool(stored.value("lock_pin_hash")),
        }

    def _open_settings(self):
        dialog = settings.SettingsDialog(
            self, self.current_settings(), TERMINAL_THEMES, i18n.LANGUAGES
        )
        if dialog.exec() == QDialog.Accepted:
            self.apply_settings(dialog.values())

    def apply_settings(self, new):
        """Stosuje tylko to, co się zmieniło — np. restart odpytywania statusu
        albo przerysowanie wszystkich terminali tylko wtedy, gdy trzeba."""
        old = self.current_settings()
        stored = i18n.settings()
        changed = {key for key in new if new[key] != old.get(key)}
        sessions = [
            self.tabs.widget(i) for i in range(self.tabs.count())
            if isinstance(self.tabs.widget(i), SessionTab)
        ]

        if "highlighting" in changed:
            self._toggle_highlighting(new["highlighting"])
        SshTerminal.timestamps = new["timestamps"]
        stored.setValue("highlighting", new["highlighting"])
        stored.setValue("timestamps", new["timestamps"])
        set_session_log_enabled(new["session_log"])  # dotyczy sesji otwartych od teraz
        stored.setValue("restore_sessions", new["restore_sessions"])
        if "font" in changed:
            set_terminal_font(new["font"])
            for session in sessions:
                session.terminal.setFont(new["font"])
        if "scrollback" in changed:
            set_scrollback(new["scrollback"])
            for session in sessions:
                session.terminal.document().setMaximumBlockCount(new["scrollback"])
        if "triggers" in changed:
            set_triggers(new["triggers"])
        if "macros" in changed:
            set_macros(new["macros"])
            for session in sessions:
                session.set_macros(parse_macros(new["macros"]))
            self._fill_snippet_menu()
        if "theme" in changed:
            set_terminal_theme(new["theme"])
            for session in sessions:
                apply_terminal_theme(session.terminal)
        if "dark_mode" in changed:
            apply_dark_mode(new["dark_mode"])

        set_alerts_enabled(new["alerts"])
        set_alert_threshold(new["alert_threshold"])
        stored.setValue("monitor_interval", new["monitor_interval"])
        if changed & {"telegram_token", "telegram_chat"}:
            set_telegram_config(new["telegram_token"], new["telegram_chat"])
        if "monitor_interval" in changed and self._monitor_timer:
            self._monitor_timer.start(new["monitor_interval"] * 1000)

        stored.setValue("tree_status_interval", new["tree_status_interval"])
        stored.setValue("tree_status_enabled", new["tree_status"])
        if changed & {"tree_status", "tree_status_interval"}:
            self._stop_status_timer()
            if new["tree_status"]:
                self._start_status_timer()

        if new.get("new_pin"):
            stored.setValue("lock_pin_hash", _hash_pin(new["new_pin"]))
        stored.setValue("lock_timeout", new["lock_timeout"])
        if changed & {"lock", "lock_timeout"}:
            self._toggle_lock(new["lock"])

        if "language" in changed:
            # Przebudowa całego okna zabiłaby otwarte sesje SSH — stąd restart.
            i18n.save(new["language"])
            QMessageBox.information(self, t("menu_language"), t("lang_restart"))

    def _toggle_highlighting(self, on):
        """Kolorowanie tekstu w terminalu — wspólne dla wszystkich zakładek."""
        TerminalHighlighter.enabled = on
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, SessionTab):
                widget.terminal.highlighter.rehighlight()

    def _toggle_server(self, spec, action):
        """Menu działa jak przełącznik: uruchom / zatrzymaj wybrany daemon."""
        running = self._servers.pop(spec["label"], None)
        if running:
            running.stop()
            action.setChecked(False)
            self.statusBar().showMessage(t("srv_stopped_one", running.label), 5000)
            return

        directory = QFileDialog.getExistingDirectory(self, t("srv_dir_prompt"))
        if not directory:
            action.setChecked(False)
            return
        port, ok = QInputDialog.getInt(
            self, t(spec["label"]), t("fld_port"), spec["port"], 1, 65535
        )
        if not ok:
            action.setChecked(False)
            return

        # Katalog jest widoczny dla calej sieci lokalnej, dopoki nie ograniczymy
        # go do konkretnego adresu — podpowiadamy host biezacej sesji SSH/RDP.
        current = self._current_session()
        suggested_ip = getattr(getattr(current, "terminal", None), "host", "") or ""
        allowed_ip, ok = QInputDialog.getText(
            self, t(spec["label"]), t("srv_allowed_ip_prompt"), text=suggested_ip
        )
        if not ok:
            action.setChecked(False)
            return
        allowed_ip = allowed_ip.strip() or None

        kwargs = {"allowed_ip": allowed_ip}
        if spec["cls"] is TftpShare:
            kwargs["allow_write"] = QMessageBox.question(
                self, t(spec["label"]), t("srv_allow_write_prompt"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) == QMessageBox.Yes

        try:
            server = spec["cls"](directory, port, **kwargs)
        except OSError as error:
            action.setChecked(False)
            QMessageBox.warning(
                self,
                t(spec["label"]),
                t("srv_start_error", port, error),
            )
            return
        self._servers[spec["label"]] = server
        action.setChecked(True)
        self._show_server_started(spec, server, directory)

    def _show_server_started(self, spec, server, directory):
        """HTTP dostaje dwa przyciski kopiujące gotową komendę pobierania."""
        if not isinstance(server, HttpShare):
            QMessageBox.information(self, t(spec["label"]), t("srv_running", server.url, directory))
            return
        box = QMessageBox(self)
        box.setWindowTitle(t(spec["label"]))
        box.setText(t("srv_running", server.url, directory))
        wget_btn = box.addButton(t("srv_copy_wget"), QMessageBox.ActionRole)
        curl_btn = box.addButton(t("srv_copy_curl"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        clicked = box.clickedButton()
        if clicked is wget_btn:
            QApplication.clipboard().setText(wget_command(server.url))
        elif clicked is curl_btn:
            QApplication.clipboard().setText(curl_command(server.url))

    def _stop_servers(self):
        for server in self._servers.values():
            server.stop()
        self._servers.clear()
        for action in self.menuBar().findChildren(QAction):
            if action.isCheckable() and action.text() in [t(s["label"]) for s in SERVERS]:
                action.setChecked(False)
        self.statusBar().showMessage(t("srv_all_stopped"), 5000)

    def _run_script(self, script):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("scripts_short"), t("scripts_need_session"))
            return
        run_script(self, session.terminal.client, script)

    def _build_shortcuts(self):
        """Ctrl+Tab / Ctrl+Shift+Tab i Ctrl+1..9 — przełączanie zakładek.

        Skrót aplikacji łapie klawisz zanim dojdzie do terminala; bez tego
        Ctrl+Tab poleciałby do powłoki jako zwykły tabulator.
        """
        for keys, step in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self._cycle_tab(s))
        # Ctrl+Shift, nie samo Ctrl — Ctrl+N/W/F/T powłoka używa sama
        # (historia, kasowanie słowa, wyszukiwanie w terminalu).
        for keys, handler in (
            ("Ctrl+Shift+N", lambda: self.tree._add_connection(self.tree.currentItem())),
            ("Ctrl+Shift+T", self._quick_connect),
            ("Ctrl+Shift+W", lambda: self._close_tab(self.tabs.currentIndex())),
            ("Ctrl+Shift+F", self._focus_filter),
            ("Ctrl+Shift+E", self._toggle_compose),
        ):
            QShortcut(QKeySequence(keys), self, activated=handler)
        for number in range(1, 10):
            QShortcut(
                QKeySequence(f"Ctrl+{number}"), self,
                activated=lambda n=number: self._go_to_tab(n - 1),
            )

    def _focus_filter(self):
        self.tree_panel.setVisible(True)
        self.toggle_tree_action.setChecked(True)
        self.tree_filter.setFocus()
        self.tree_filter.selectAll()

    def tab_order(self):
        """Zakładki, po których wolno chodzić — bez „+" na końcu i bez schowanych w siatce."""
        return [
            i for i in range(self.tabs.count())
            if self.tabs.widget(i) is not self._plus_tab and self.tabs.isTabVisible(i)
        ]

    def _cycle_tab(self, step):
        order = self.tab_order()
        if len(order) < 2:
            return
        current = self.tabs.currentIndex()
        position = order.index(current) if current in order else 0
        self.tabs.setCurrentIndex(order[(position + step) % len(order)])

    def _go_to_tab(self, position):
        order = self.tab_order()
        if position < len(order):
            self.tabs.setCurrentIndex(order[position])

    def _build_sidebar(self):
        """Pionowy pasek ikon po lewej, wzorem MobaXterm (Sessions/Tools/…)."""
        sidebar = QToolBar(t("sidebar"))
        sidebar.setMovable(False)
        sidebar.setFloatable(False)
        sidebar.setOrientation(Qt.Vertical)
        sidebar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        sidebar.addAction(self.toggle_tree_action)
        self.addToolBar(Qt.LeftToolBarArea, sidebar)

    def _show_shortcuts(self):
        QMessageBox.information(self, t("menu_shortcuts"), t("shortcuts_body"))

    def _show_about(self):
        QMessageBox.information(
            self, t("about_title"), t("about_body")
        )

    def _current_session(self):
        """Zakładka na wierzchu, a w siatce — sesja ostatnio klikniętego terminala."""
        widget = self.tabs.currentWidget()
        if isinstance(widget, split.SplitTab):
            return widget.focused_session()
        return widget

    def _show_stats(self, widget, text):
        """Pasek pokazuje tylko serwer, którego zakładka jest na wierzchu."""
        if widget is self._current_session():
            self.statusBar().showMessage(text)
            self.stats_graph.set_history(getattr(widget, "history", None))

    def _show_current_stats(self, _index=None):
        widget = self._current_session()
        self.statusBar().showMessage(getattr(widget, "last_stats", "") or t("status_idle"))
        self.stats_graph.set_history(getattr(widget, "history", None))

    def _on_item_activated(self, item, _column):
        if item.type() != CONNECTION_TYPE:
            return
        self._open_connection_tab(item.data(0, CONNECTION_DATA))

    def _open_connection_tab(self, conn, duplicate=False):
        # Po samym słowniku połączenia, nie po nazwie — dwa „web01” w różnych
        # grupach to dwa różne serwery, a nie jedna zakładka. `duplicate` =
        # „Duplikuj sesję” z menu zakładki: świadomie drugi raz to samo.
        for i in range(0 if duplicate else self.tabs.count()):
            widget = self.tabs.widget(i)
            if getattr(widget, "origin", None) is conn:
                # Sesja w siatce ma schowaną zakładkę — pokazujemy siatkę.
                self.tabs.setCurrentWidget(getattr(widget, "split", None) or widget)
                return

        # `conn` to żywy słownik z drzewa — znacznik trafia do connections.json.
        conn["last_used"] = int(time.time())
        self.tree.save()

        # Konto współdzielone (jeśli wskazane i istnieje) albo własne pola połączenia.
        auth = credentials.effective_auth(conn)
        password = decrypt_password(auth["password"]) if auth["password"] else None
        passphrase = decrypt_password(auth["passphrase"]) if auth["passphrase"] else None

        # RDP nie pyta nas o hasło: bez zapisanego kontrolka poprosi sama.
        if conn.get("protocol", "ssh") == "rdp":
            self._open_rdp_tab({**conn, "username": auth["username"]}, password, origin=conn)
            return

        # Zapisane hasło odszyfrowujemy, w przeciwnym razie pytamy.
        # Puste = logowanie kluczem z agenta lub ~/.ssh.
        if password is None:
            password, ok = QInputDialog.getText(
                self,
                t("dlg_auth_title"),
                t("dlg_auth_body", auth["username"], conn["host"]),
                QLineEdit.Password,
            )
            if not ok:
                return

        self._connect_and_add_tab(conn, password, passphrase, auth)

    def _open_scanner(self):
        """Skaner sieci; wybrany host wchodzi wprost do formularza połączenia."""
        scanner.ScannerDialog(self, self._connect_to_found).exec()

    def _wake_on_lan(self):
        scanner.wake_dialog(self)

    def _check_certificate(self):
        scanner.cert_dialog(self)

    def _import_from_program(self, source, label, path=None):
        """PuTTY z rejestru, reszta z pliku wskazanego w oknie (`path` — dla testów)."""
        title = t(label)
        try:
            if source == "putty":
                items = importers.read_putty()
            else:
                if path is None:
                    path, _ = QFileDialog.getOpenFileName(self, title, "", t(f"filter_{source}"))
                    if not path:
                        return
                # utf-8-sig: CSV z Excela i pliki MobaXterm bywają z BOM.
                text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
                items = getattr(importers, f"parse_{source}")(text)
        except (OSError, ValueError, ET.ParseError) as error:
            QMessageBox.warning(self, title, t("import_other_failed", error))
            return
        count = self.tree.import_items(items, t("import_group", title.rstrip("…")))
        QMessageBox.information(
            self, title, t("import_other_done", count) if count else t("import_other_none")
        )

    def _import_ssh_config(self):
        try:
            count = self.tree.import_ssh_config()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, t("menu_import_ssh_config"), t("err_ssh_config", error))
            return
        if count is None:
            message = t("ssh_config_missing")
        else:
            message = t("ssh_config_done", count) if count else t("ssh_config_none")
        QMessageBox.information(self, t("menu_import_ssh_config"), message)

    # --- widok terminala ---------------------------------------------------

    def _open_dashboard(self):
        StatsDashboard(self, self).exec()

    def _open_log_tail(self):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("logtail_title"), t("tunnels_need_session"))
            return
        path, ok = QInputDialog.getText(self, t("logtail_title"), t("logtail_prompt"))
        if not ok or not path.strip():
            return
        path = path.strip()
        tab = logtail.LogTab(session.terminal.client, path)
        self._add_tab(tab, t("log_tab_title", Path(path).name))

    def _manage_services(self):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("services_title"), t("tunnels_need_session"))
            return
        services.ServiceDialog(self, session.terminal.client).exec()

    def _open_disks(self):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("disks_title"), t("tunnels_need_session"))
            return
        disks.DiskDialog(self, session.terminal.client).exec()

    def _open_processes(self):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("processes_title"), t("tunnels_need_session"))
            return
        processes.ProcessDialog(self, session.terminal.client).exec()

    def _open_docker(self):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("docker_title"), t("tunnels_need_session"))
            return
        containers.DockerDialog(self, session.terminal.client).exec()

    def _manage_known_hosts(self):
        KnownHostsDialog(self).exec()

    def _manage_credentials(self):
        credentials.CredentialManager(self, self.tree.nodes).exec()
        self.tree.refresh_tooltips()  # nazwa/login konta mogły się zmienić

    def _open_multirun(self):
        targets = [
            (self.tabs.widget(i).tab_name, self.tabs.widget(i).terminal.client)
            for i in range(self.tabs.count())
            if isinstance(self.tabs.widget(i), SessionTab)
        ]
        if not targets:
            QMessageBox.information(self, t("multirun_title"), t("scripts_need_session"))
            return
        multirun.MultiRunDialog(self, targets).exec()

    def _open_logsearch(self):
        targets = [
            (self.tabs.widget(i).tab_name, self.tabs.widget(i).terminal.client)
            for i in range(self.tabs.count())
            if isinstance(self.tabs.widget(i), SessionTab)
        ]
        if not targets:
            QMessageBox.information(self, t("logsearch_title"), t("scripts_need_session"))
            return
        logsearch.LogSearchDialog(self, targets).exec()

    def _open_patches(self):
        targets = [
            (self.tabs.widget(i).tab_name, self.tabs.widget(i).terminal.client)
            for i in range(self.tabs.count())
            if isinstance(self.tabs.widget(i), SessionTab)
        ]
        if not targets:
            QMessageBox.information(self, t("patches_title"), t("scripts_need_session"))
            return
        patches.PatchDialog(self, targets).exec()

    def _open_split(self):
        """Siatka 2–4 terminali (split.py); ich zakładki chowają się na ten czas."""
        sessions = [
            self.tabs.widget(i) for i in range(self.tabs.count())
            if isinstance(self.tabs.widget(i), SessionTab) and self.tabs.widget(i).split is None
        ]
        if len(sessions) < 2:
            QMessageBox.information(self, t("split_title"), t("split_need_sessions"))
            return
        picker = split.SplitPicker(self, sessions, self._current_session())
        if picker.exec() != QDialog.Accepted:
            return
        chosen = picker.chosen()
        grid = split.SplitTab(chosen, self._on_split_released)
        grid.focus_changed.connect(self._show_current_stats)
        for session in chosen:
            self.tabs.setTabVisible(self.tabs.indexOf(session), False)
        self._add_tab(grid, t("split_tab", len(chosen)))
        chosen[0].terminal.setFocus()

    def _on_split_released(self, grid, sessions):
        for session in sessions:
            self.tabs.setTabVisible(self.tabs.indexOf(session), True)
        index = self.tabs.indexOf(grid)
        if index >= 0:
            self.tabs.removeTab(index)
        grid.deleteLater()
        self.tabs.setCurrentWidget(sessions[0])

    def _open_keygen(self):
        # Wgranie do authorized_keys wymaga sesji, ale generowanie i zapis — nie;
        # bez otwartej zakładki przycisk wgrania jest tylko wyszarzony.
        session = self._current_session()
        client = session.terminal.client if isinstance(session, SessionTab) else None
        keygen.KeyGenDialog(self, client).exec()

    def _save_session_log(self):
        """Zapis tego, co widać w terminalu aktywnej zakładki.

        ponytail: zapisujemy bufor okna (5000 linii z `setMaximumBlockCount`),
        a nie strumień od początku sesji — pełny zapis to „Zapisuj wszystkie
        sesje” w Ustawieniach (`SshTerminal.start_log`).
        """
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("menu_save_log"), t("log_need_session"))
            return
        path = save_text(
            self, session.terminal.toPlainText(), t("log_default_name"), t("menu_save_log")
        )
        if path:
            QMessageBox.information(self, t("menu_save_log"), t("log_saved", path))

    def _connect_to_found(self, host, protocol, name):
        self._quick_connect({"host": host, "protocol": protocol, "name": name or host})

    def _quick_connect(self, data=None):
        """Połączenie „na szybko” z przycisku + — nie trafia do drzewa/pliku."""
        # `clicked` z przycisku podaje `False` jako pierwszy argument — stąd `or None`.
        dialog = ConnectionDialog(self, data or None)
        dialog.setWindowTitle(t("dlg_quick_title"))
        dialog.save_password.setVisible(False)
        if dialog.exec() != QDialog.Accepted:
            return
        conn = dialog.values()
        if conn.get("credential"):
            self._open_connection_tab(conn)  # hasło z konta, jak przy wpisie z drzewa
            return
        conn.pop("password", None)  # tymczasowe połączenie nic nie zapisuje
        conn.pop("passphrase", None)
        password = dialog.password.text() or None
        if conn["protocol"] == "rdp":
            self._open_rdp_tab(conn, password)
            return
        self._connect_and_add_tab(conn, password, dialog.passphrase.text() or None)

    def _open_rdp_tab(self, conn, password, origin=None):
        """None = sesja poszła do osobnego mstsc albo się nie udała."""
        tab = open_rdp(self, conn, password)
        if tab is None:
            return
        tab.session_ended.connect(lambda text, w=tab: self._show_stats(w, text))
        self._add_tab(tab, conn["name"], origin)

    def _connect_and_add_tab(self, conn, password, passphrase=None, auth=None):
        # `auth` osobno, nie wmieszane w `conn` — `conn` to żywy słownik z drzewa
        # (zakładki SFTP, tunele zapisują się przez niego), kopia by je gubiła.
        auth = auth or conn
        # Okno postępu; None = anulowano lub błąd (komunikat już się pokazał).
        terminal = connect_with_progress(
            self, conn["host"], conn["port"], auth["username"], password,
            auth.get("key_file"), passphrase, conn.get("jump_host"),
            bool(conn.get("forward_agent")),
        )
        if terminal is None:
            return

        # Zakładki katalogów SFTP siedzą wprost w danych połączenia — panel
        # dopisuje do tej samej listy, a `tree.save()` zrzuca ją do pliku.
        session = SessionTab(
            terminal, bookmarks=conn.setdefault("bookmarks", []), on_change=self.tree.save
        )
        session.conn = conn
        session.reconnect_args = dict(
            host=conn["host"], port=conn["port"], username=auth["username"],
            password=password, key_file=auth.get("key_file"), passphrase=passphrase,
            jump_host=conn.get("jump_host"), forward_agent=bool(conn.get("forward_agent")),
        )
        session.startup = conn.get("startup")
        session.terminal.start_log(conn["name"])
        session.terminal.confirm_send = lambda c=conn, s=session: self._confirm_prod(c, s)
        session.compose_to_all.connect(self._send_to_all_sessions)
        session.terminal.stats_changed.connect(
            lambda text, w=session: self._show_stats(w, text)
        )
        session.terminal.activity.connect(lambda w=session: self._mark_activity(w))
        self._add_tab(session, conn["name"], conn)
        self._restore_tunnels(session, conn)
        session.terminal.send_startup(conn.get("startup"))
        session.terminal.setFocus()

    # --- tunele SSH --------------------------------------------------------

    def _restore_tunnels(self, session, conn):
        """Zapisane tunele wstają razem z sesją; nieudane zbieramy w jeden komunikat."""
        failed = []
        for text in conn.get("tunnels", []):
            spec = tunnels.parse_tunnel(text)
            error = session.open_tunnel(spec) if spec else t("tunnel_bad")
            if error:
                failed.append(f"{text} — {error}")
        if failed:
            QMessageBox.warning(
                self, t("tunnel_title"), t("tunnel_restore_failed", "\n".join(failed))
            )

    def _manage_tunnels(self):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            QMessageBox.information(self, t("tunnel_title"), t("tunnels_need_session"))
            return
        tunnels.TunnelDialog(self, session, lambda: self._save_tunnels(session)).exec()

    def _save_tunnels(self, session):
        conn = getattr(session, "conn", None)
        if conn is None:
            return  # połączenie tymczasowe — nie ma czego zapisywać
        conn["tunnels"] = [tunnels.format_tunnel(spec) for spec in session.tunnels]
        self.tree.save()

    def _add_tab(self, widget, name, origin=None):
        """Nowa karta wchodzi PRZED "+", żeby "+" zawsze zostawało ostatnie.

        `origin` = słownik połączenia z drzewa — po nim `_open_connection_tab`
        poznaje, że zakładka już jest otwarta.
        """
        widget.origin = origin
        widget.tab_name = name  # nazwa bez znacznika aktywności „● ”
        widget.has_activity = False
        index = self.tabs.insertTab(self.tabs.count() - 1, widget, name)
        self._update_tab_text(widget)  # kolor środowiska
        if origin:
            self.tabs.setTabToolTip(
                index, f"{origin.get('username', '')}@{origin.get('host', '')}:{origin.get('port', '')}"
            )
        self.tabs.setCurrentIndex(index)
        return index

    def _on_tab_bar_clicked(self, index):
        """"+" nie jest zwykłą zakładką — klik otwiera dialog, a nie ją aktywuje."""
        if self.tabs.widget(index) is not self._plus_tab:
            return
        self.tabs.setCurrentIndex(index - 1)  # "+" jest zawsze ostatnie
        self._quick_connect()

    # --- menu i znacznik aktywności zakładki ------------------------------

    def _update_tab_text(self, widget):
        index = self.tabs.indexOf(widget)
        if index >= 0:
            mark = "● " if widget.has_activity else ""
            terminal = getattr(widget, "terminal", None)
            if terminal is not None and terminal.read_only:
                mark += "🔒 "
            self.tabs.setTabText(index, mark + widget.tab_name)
            env = (getattr(widget, "origin", None) or {}).get("environment")
            # Niepoprawny QColor = kolor z palety, czyli zwykła zakładka.
            self.tabs.tabBar().setTabTextColor(index, QColor(ENV_COLORS.get(env, "")))

    def _refresh_tab_texts(self):
        """Po zapisie drzewa — np. zmienione środowisko otwartego połączenia."""
        for i in range(self.tabs.count()):
            if hasattr(self.tabs.widget(i), "tab_name"):
                self._update_tab_text(self.tabs.widget(i))

    def _confirm_prod(self, conn, session):
        """Pytanie przed wysłaniem do produkcji; inne środowiska przechodzą od razu."""
        if conn.get("environment") != "prod":
            return True
        self.tabs.setCurrentWidget(getattr(session, "split", None) or session)
        return QMessageBox.question(
            self, t("prod_confirm_title"), t("prod_confirm_body", session.tab_name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

    def _mark_activity(self, widget):
        """Wyjście w zakładce, która nie jest na wierzchu -> „● ” przed nazwą."""
        if getattr(widget, "has_activity", True) or widget is self.tabs.currentWidget():
            return
        widget.has_activity = True
        self._update_tab_text(widget)

    def _clear_activity(self, _index=None):
        widget = self.tabs.currentWidget()
        if getattr(widget, "has_activity", False):
            widget.has_activity = False
            self._update_tab_text(widget)

    def _tab_menu(self, pos):
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        widget = self.tabs.widget(index)
        if index <= 0 or widget is self._plus_tab:
            return  # Home, "+" i puste miejsce paska
        menu = QMenu(self)
        menu.addAction(t("tab_close_others"), lambda: self._close_other_tabs(widget))
        menu.addAction(t("tab_rename"), lambda: self._rename_tab(widget))
        origin = getattr(widget, "origin", None)
        duplicate = menu.addAction(
            t("tab_duplicate"), lambda: self._open_connection_tab(origin, duplicate=True)
        )
        duplicate.setEnabled(origin is not None)  # szybkie połączenie nie ma wpisu w drzewie
        if isinstance(widget, SessionTab):
            menu.addAction(t("tab_open_log"), lambda: self._open_log_from(widget))
            read_only = menu.addAction(t("tab_read_only"))
            read_only.setCheckable(True)
            read_only.setChecked(widget.terminal.read_only)
            read_only.toggled.connect(lambda on: self._set_read_only(widget, on))
            menu.addAction(t("tab_compose"), widget.toggle_compose)
        menu.exec(bar.mapToGlobal(pos))

    def _fill_snippet_menu(self):
        self.snippet_menu.clear()
        for label, command, shortcut in parse_macros(macros_text()):
            action = self.snippet_menu.addAction(label, lambda c=command: self._send_snippet(c))
            action.setStatusTip(command)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
        self.snippet_menu.addSeparator()
        self.snippet_menu.addAction(t("menu_snippets_edit"), self._open_settings)

    def _send_snippet(self, command):
        session = self._current_session()
        if not isinstance(session, SessionTab):
            self.statusBar().showMessage(t("scripts_need_session"), 5000)
            return
        session.send_macro(command)

    def _send_to_all_sessions(self, data):
        """Pole polecenia z „do wszystkich” — przez `send_text`, więc tylko do odczytu działa."""
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, SessionTab):
                widget.terminal.send_text(data)

    def _toggle_compose(self):
        session = self.tabs.currentWidget()
        if isinstance(session, SessionTab):
            session.toggle_compose()

    def _set_read_only(self, session, on):
        session.terminal.read_only = on
        self._update_tab_text(session)
        self.statusBar().showMessage(t("read_only_on" if on else "read_only_off"), 5000)

    def _open_log_from(self, session):
        self.tabs.setCurrentWidget(session)  # `_open_log_tail` bierze aktywną zakładkę
        self._open_log_tail()

    def _close_other_tabs(self, keep):
        # Od końca, bez Home (0) i "+" (ostatnie) — indeksy nie przesuwają się pod nami.
        for i in range(self.tabs.count() - 2, 0, -1):
            if self.tabs.widget(i) is not keep:
                self._close_tab(i)

    def _rename_tab(self, widget):
        name, ok = QInputDialog.getText(
            self, t("tab_rename"), t("tab_rename_prompt"), text=widget.tab_name
        )
        if ok and name.strip():
            widget.tab_name = name.strip()
            self._update_tab_text(widget)

    def _close_tab(self, index):
        widget = self.tabs.widget(index)
        if index == 0 or widget is self._plus_tab:
            return  # "Home" i "+" nie mają przycisku zamknięcia, ale na wszelki wypadek
        if isinstance(widget, split.SplitTab):
            widget.release()  # zamknięcie siatki = „Rozdziel”, sesje zostają
            return
        if getattr(widget, "split", None):
            widget.split.release()  # terminal musi wrócić, zanim sesja zniknie
            index = self.tabs.indexOf(widget)
        self.tabs.removeTab(index)
        if hasattr(widget, "close_session"):  # SessionTab albo RdpTab
            widget.close_session()
        widget.deleteLater()

    def closeEvent(self, event):
        self._save_layout()
        if self._update_check:
            self._update_check.wait()  # inaczej Qt wywala proces przy zamykaniu
        self._stop_status_timer()
        self._stop_monitoring()
        if self._lock_timer:
            self._lock_timer.stop()
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if hasattr(widget, "close_session"):
                widget.close_session()
        self._stop_servers()
        wait_for_pending()  # anulowane łączenia; inaczej Qt wywala proces
        super().closeEvent(event)


def selftest():
    """Sprawdza logikę drzewa, zakładek i zapisu — bez sieci i bez okna."""
    import rdp
    import servers
    import ssh_terminal
    import tunnels as tunnels_module
    import tempfile

    i18n.use("en")  # testy sprawdzaja napisy domyslnego jezyka
    from PySide6.QtWidgets import QWidget

    global CONFIG_FILE
    app = QApplication.instance() or QApplication([])

    # Nie dotykaj prawdziwego pliku użytkownika.
    with tempfile.TemporaryDirectory() as tmp:
        CONFIG_FILE = Path(tmp) / "connections.json"

        tree = ConnectionTree()
        root = tree.topLevelItem(0)
        group = QTreeWidgetItem(root, ["Produkcja"])
        conn_data = {"name": "srv-01", "host": "10.0.0.1", "port": 2222, "username": "admin"}
        saved = QTreeWidgetItem(group, ["srv-01"], CONNECTION_TYPE)
        saved.setData(0, CONNECTION_DATA, conn_data)
        tree.save()
        assert CONFIG_FILE.exists(), "plik konfiguracji nie powstał"

        # Dziedziczenie z grupy: puste pole połączenia bierze wartość z najbliższej
        # grupy, własne wygrywa; do pliku idzie tylko to, co własne.
        outer = QTreeWidgetItem(root, ["Klient"])
        outer.setData(0, GROUP_DATA, {"username": "deploy", "environment": "prod",
                                      "jump_host": "bastion:2200"})
        inner = QTreeWidgetItem(outer, ["Bazy"])
        inner.setData(0, GROUP_DATA, {"username": "dba", "credential": "konto-1"})
        heir = QTreeWidgetItem(inner, [], CONNECTION_TYPE)
        tree._apply_connection(heir, {"name": "db-01", "host": "10.1.0.1", "port": 22})
        own = QTreeWidgetItem(inner, [], CONNECTION_TYPE)
        tree._apply_connection(own, {"name": "db-02", "host": "10.1.0.2", "port": 22,
                                     "username": "root", "environment": "test"})
        tree.save()
        heir_data, own_data = heir.data(0, CONNECTION_DATA), own.data(0, CONNECTION_DATA)
        assert heir_data.get("username") == "dba", "bliższa grupa ma wygrać"
        assert heir_data.get("jump_host") == "bastion:2200" and heir_data.get("environment") == "prod"
        assert heir_data.get("credential") == "konto-1"
        assert own_data.get("username") == "root" and own_data.get("environment") == "test"
        assert own_data.get("credential") is None, "konto grupy przykryło własny login"
        assert "username" not in dict(heir_data), "wartość z grupy nie może stać się własną"
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        stored_outer = next(node for node in stored if node["name"] == "Klient")
        assert stored_outer["defaults"]["username"] == "deploy"
        assert "username" not in stored_outer["children"][0]["children"][0]["connection"]
        # Po wczytaniu z pliku dziedziczenie działa tak samo.
        reloaded = ConnectionTree()
        reloaded.load()
        found = {d["name"]: d for d in tree_connections(reloaded)}
        assert found["db-01"].get("username") == "dba" and found["db-01"].get("environment") == "prod"
        # Formularz: wartość grupy jako podpowiedź, nie wpisana — zapis jej nie utrwala.
        form = ConnectionDialog(None, heir_data, inherited=heir_data.defaults)
        assert form.username.text() == "" and "dba" in form.username.placeholderText()
        assert form.values()["username"] == "" and "jump_host" not in form.values()
        # Przeniesienie do innej grupy = nowe ustawienia po zapisie.
        inner.removeChild(heir)
        root.addChild(heir)
        tree.save()
        assert heir_data.get("username") is None and heir_data.get("environment") is None
        # Okno grupy oddaje tylko wypełnione pola.
        group_form = GroupDialog(None, {"username": "deploy"})
        group_form.jump_host.setText("  ")
        assert group_form.values() == {"username": "deploy"}, group_form.values()
        root.removeChild(heir)
        root.removeChild(outer)
        tree.save()

        # Kropka statusu: cel bierze host/port z połączenia, wynik trafia na ikonę.
        targets = tree.status_targets()
        assert targets[id(saved)] == ("10.0.0.1", 2222), "zły cel sprawdzania statusu"
        tree.apply_status({id(saved): True})
        assert not saved.icon(0).isNull(), "brak kropki statusu po odpowiedzi online"
        tree.apply_status({id(saved): False})
        assert not saved.icon(0).isNull(), "brak kropki statusu po odpowiedzi offline"
        rdp_conn = QTreeWidgetItem(group, ["rdp-01"], CONNECTION_TYPE)
        rdp_conn.setData(0, CONNECTION_DATA, {"name": "rdp-01", "host": "10.0.0.2", "protocol": "rdp"})
        assert tree.status_targets()[id(rdp_conn)] == ("10.0.0.2", RDP_PORT), "brak domyślnego portu RDP"

        # Nowe drzewo = symulacja restartu aplikacji.
        reloaded = ConnectionTree()
        reloaded_root = reloaded.topLevelItem(0)
        assert reloaded_root.childCount() == 1, "grupa nie przetrwała restartu"
        reloaded_group = reloaded_root.child(0)
        assert reloaded.item_name(reloaded_group) == "Produkcja"
        # Grupa bez własnej ikony dostaje domyślny folder.
        assert reloaded_group.data(0, ICON_DATA) == GROUP_ICON, "brak ikony folderu"
        assert reloaded_group.childCount() == 1, "połączenie nie przetrwało restartu"
        reloaded_conn = reloaded_group.child(0)
        assert reloaded_conn.type() == CONNECTION_TYPE, "typ elementu zgubiony"
        assert reloaded_conn.data(0, CONNECTION_DATA) == conn_data, "dane połączenia zmienione"

        # Ikona jest doklejana do etykiety, ale w pliku nazwa zostaje czysta.
        ConnectionTree.set_label(group, "Produkcja", "🗂️")
        tree.save()
        with_icon = ConnectionTree()
        icon_group = with_icon.topLevelItem(0).child(0)
        assert with_icon.item_name(icon_group) == "Produkcja", "ikona zjadła nazwę"

        # Kolor grupy schodzi na wszystko, co w niej siedzi, i przeżywa restart.
        ConnectionTree.set_color(group, "#ff0000")
        assert saved.foreground(0).color().name() == "#ff0000", "kolor nie zszedł na dziecko"
        tree.save()
        colored_tree = ConnectionTree()
        colored = colored_tree.topLevelItem(0).child(0)
        assert colored.data(0, COLOR_DATA) == "#ff0000", "kolor nie przetrwał restartu"
        assert colored.child(0).foreground(0).color().name() == "#ff0000"
        ConnectionTree.set_color(group, "")
        assert saved.data(0, COLOR_DATA) is None, "czyszczenie koloru nie zeszło na dziecko"
        assert icon_group.data(0, ICON_DATA) == "🗂️", "ikona nie przetrwała restartu"
        assert icon_group.text(0).endswith("Produkcja")

        # Edycja połączenia: nowe dane muszą trafić do etykiety i do pliku.
        with_icon._apply_connection(
            icon_group.child(0),
            {"name": "srv-99", "host": "10.0.0.9", "port": 22, "username": "root"},
        )
        with_icon.save()
        after_edit = ConnectionTree()
        edited = after_edit.topLevelItem(0).child(0).child(0)
        assert edited.data(0, CONNECTION_DATA)["host"] == "10.0.0.9", "edycja nie zapisana"
        assert edited.text(0) == "srv-99"

        # Edycja przez formularz zmienia dane w miejscu — otwarta zakładka
        # (`origin`, `session.conn`) dalej wskazuje na słownik z drzewa.
        live = edited.data(0, CONNECTION_DATA)
        original_exec, original_values = ConnectionDialog.exec, ConnectionDialog.values
        ConnectionDialog.exec = lambda self: QDialog.Accepted
        ConnectionDialog.values = lambda self: {
            "name": "srv-100", "host": "10.0.0.10", "port": 22, "username": "root",
        }
        try:
            after_edit._edit_connection(edited)
        finally:
            ConnectionDialog.exec, ConnectionDialog.values = original_exec, original_values
        assert edited.data(0, CONNECTION_DATA) is live, "edycja odcięła otwartą zakładkę od drzewa"
        assert live["host"] == "10.0.0.10" and edited.text(0) == "srv-100"

        # Uszkodzony plik nie może wywalić aplikacji ani zniknąć bez śladu.
        CONFIG_FILE.write_text("{to nie jest json", encoding="utf-8")
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        broken = ConnectionTree()
        assert broken.topLevelItem(0).childCount() == 0
        assert CONFIG_FILE.with_suffix(".json.bak").exists(), "brak kopii uszkodzonego pliku"

        # Poprawny JSON, ale zla struktura wewnatrz (polaczenie bez "name")
        # rowniez nie moze wywalic calej aplikacji przy starcie.
        CONFIG_FILE.with_suffix(".json.bak").unlink()
        CONFIG_FILE.write_text(
            json.dumps([{"name": "x", "connection": {"host": "h"}}]), encoding="utf-8"
        )
        malformed = ConnectionTree()
        assert malformed.topLevelItem(0).childCount() == 0, "drzewo nie zostalo wyczyszczone"
        assert CONFIG_FILE.with_suffix(".json.bak").exists(), "brak kopii zlej struktury"

        # Eksport i import: ten sam format co plik konfiguracyjny.
        export_file = Path(tmp) / "eksport.json"
        tree.export_to(export_file)
        assert json.loads(export_file.read_text(encoding="utf-8")) == tree.nodes()

        empty = ConnectionTree()
        empty.topLevelItem(0).takeChildren()
        assert empty.import_from(export_file, replace=True) == 1
        imported = empty.topLevelItem(0).child(0)
        assert empty.item_name(imported) == "Produkcja"
        assert imported.child(0).data(0, CONNECTION_DATA) == conn_data, "import zgubił dane"

        # Dopisanie (replace=False) nie kasuje tego, co już jest.
        empty.import_from(export_file, replace=False)
        assert empty.topLevelItem(0).childCount() == 2, "dopisywanie skasowało istniejące"

        try:
            empty.import_from(export_file.with_name("brak.json"), replace=True)
            raise AssertionError("brak pliku musi się zgłosić wyjątkiem")
        except OSError:
            pass

        # Zla struktura wewnatrz importowanego pliku (nie tylko zly JSON) ma
        # sie zglosic wyjatkiem, ktory _import_connections() umie obslozyc —
        # ten sam blad co przy starcie z zepsutym connections.json (patrz wyzej).
        bad_import = Path(tmp) / "zly-import.json"
        bad_import.write_text(json.dumps([{"connection": "nie-slownik"}]), encoding="utf-8")
        try:
            empty.import_from(bad_import, replace=True)
            raise AssertionError("zla struktura musi sie zglosic wyjatkiem")
        except (KeyError, TypeError, AttributeError):
            pass

        print("persystencja: OK")

    # Celowo nieistniejąca ścieżka: reszta testu nie może ruszyć pliku użytkownika.
    CONFIG_FILE = Path(tempfile.gettempdir()) / "nie-istnieje-selftest.json"
    window = MainWindow()
    root = window.tree.topLevelItem(0)

    group = QTreeWidgetItem(root, ["Produkcja"])
    data = {"name": "srv-01", "host": "10.0.0.1", "port": 22, "username": "admin"}
    conn = QTreeWidgetItem(group, [data["name"]], CONNECTION_TYPE)
    window.tree._apply_connection(conn, data)
    data = conn.data(0, CONNECTION_DATA)
    # Żywy słownik: dwa odczyty to ten sam obiekt, zmiana zostaje w drzewie.
    assert conn.data(0, CONNECTION_DATA) is data, "drzewo oddaje kopię zamiast słownika"
    data["last_used"] = 1
    assert conn.data(0, CONNECTION_DATA)["last_used"] == 1, "zmiana w danych połączenia przepadła"
    del data["last_used"]

    assert conn.type() == CONNECTION_TYPE
    assert group.type() != CONNECTION_TYPE
    assert conn.data(0, CONNECTION_DATA)["host"] == "10.0.0.1"

    # Wyszukiwarka na Home: dopasowanie po nazwie, hoście i użytkowniku.
    probe = {"name": "Serwer WWW", "host": "10.0.0.1", "username": "admin"}
    assert HomeTab.matches(probe, ""), "puste zapytanie ma przepuszczać wszystko"
    assert HomeTab.matches(probe, "www"), "brak dopasowania po nazwie"
    assert HomeTab.matches(probe, "10.0.0"), "brak dopasowania po hoście"
    assert HomeTab.matches(probe, "ADMIN"), "wyszukiwanie ma ignorować wielkość liter"
    assert not HomeTab.matches(probe, "baza"), "fałszywe dopasowanie"

    # Kafelki monitoringu: tylko połączenia z "monitor", kolor wg wyniku.
    # Baza historii w temp — selftest nie zostawia monitor.db w katalogu.
    import tempfile
    real_db, monitor.DB_FILE = monitor.DB_FILE, Path(tempfile.mkdtemp()) / "m.db"
    try:
        home = window.tabs.widget(0)
        home.refresh_tiles()
        assert home.tiles.count() == 0, "bez monitorowanych połączeń kafelków nie ma"
        data["monitor"] = True
        home.refresh_tiles()
        assert home.tiles.count() == 1 and "srv-01" in home.tiles.item(0).text()
        assert t("monitor_waiting") in home.tiles.item(0).text(), "przed pierwszą rundą"
        assert "10.0.0.1:22" in window._monitor_targets()
        window._monitor_names = {"10.0.0.1:22": "srv-01"}
        window._on_monitor_done({"10.0.0.1:22": {"ok": True, "cpu": 5, "mem": 10, "disk": 20}})
        assert "CPU 5%" in home.tiles.item(0).text(), home.tiles.item(0).text()
        assert window._monitor_states["10.0.0.1:22"] == monitor.GREEN
        assert home.tiles.item(0).data(Qt.UserRole) is data, "dwuklik kafelka = to połączenie"
        dialog = ConnectionDialog(None, data)
        assert dialog.monitor.isChecked() and dialog.values().get("monitor") is True
        dialog.monitor.setChecked(False)
        assert "monitor" not in dialog.values(), "odznaczenie ma zdjąć monitoring"
        assert "tls_port" not in dialog.values(), "puste pole TLS = bez sprawdzania"
        dialog.tls_port.setValue(443)
        assert dialog.values()["tls_port"] == 443
        data["tls_port"] = 443
        assert window._monitor_targets()["10.0.0.1:22"]["tls_port"] == 443
        reopened = ConnectionDialog(None, data)
        assert reopened.tls_port.value() == 443, "port TLS wraca przy edycji"
        # Historia kafelka: pusta baza -> komunikat, z próbkami -> wykres;
        # przełącznik okresu (24 h / 7 / 30 dni) przelicza wykres.
        empty = home.history_dialog(data, show=False)
        assert empty.graph.isHidden() and empty.summary.text() == t("monitor_history_empty")
        monitor.record({"10.0.0.1:22": {"ok": True, "cpu": 5, "mem": 10}})
        monitor.record({"10.0.0.1:22": {"ok": True, "cpu": 50, "mem": 60}}, now=time.time() - 3 * 86400)
        filled = home.history_dialog(data, show=False)
        assert not filled.graph.isHidden() and filled.graph.history == [(5, 10)], filled.graph.history
        filled.span.setCurrentIndex(1)  # 7 dni: dochodzi próbka sprzed 3 dni
        assert filled.graph.history == [(50, 60), (5, 10)], filled.graph.history
        assert filled.span.currentText() in filled.summary.text()
    finally:
        monitor.DB_FILE = real_db
        data.pop("monitor", None)
        data.pop("tls_port", None)
        window.monitor_results.clear()
        window._monitor_states.clear()

    # "Home" (pierwsza) i "+" (ostatnia) to stałe zakładki bez przycisku zamknięcia.
    assert window.tabs.count() == 2, "startowe zakładki: Home i +"
    assert window.tabs.tabBar().tabButton(0, QTabBar.RightSide) is None, "Home nie może mieć X"
    assert window.tabs.tabBar().tabButton(1, QTabBar.RightSide) is None, "+ nie może mieć X"
    window._close_tab(0)
    window._close_tab(1)
    assert window.tabs.count() == 2, "Home i + nie mogą dać się zamknąć"

    # Klik w "+" ma otworzyć dialog (tu podmieniony), a nie zostać aktywną zakładką.
    quick_connect_calls = []
    window._quick_connect = lambda: quick_connect_calls.append(True)
    window.tabs.setCurrentIndex(0)
    window._on_tab_bar_clicked(1)  # "+" jest zawsze ostatnie
    assert quick_connect_calls == [True], "klik w + nie otworzył dialogu"
    assert window.tabs.currentIndex() == 0, "+ nie może zostać aktywną zakładką"

    window._on_item_activated(group, 0)
    assert window.tabs.count() == 2, "grupa nie powinna otwierać zakładki"

    # Istniejąca zakładka o tej nazwie musi zostać wybrana, zanim padnie
    # pytanie o hasło — inaczej dwuklik łączyłby się drugi raz.
    window._add_tab(QWidget(), "srv-01", data)
    window._on_item_activated(conn, 0)  # prawdziwa droga dwukliku, przez item.data()
    assert window.tabs.count() == 3, "ponowne otwarcie nie może duplikować zakładki"
    assert window.tabs.widget(window.tabs.count() - 1) is window._plus_tab, "+ musi zostać ostatnie"

    # Znacznik aktywności: tylko nieaktywna zakładka, znika po przełączeniu.
    first = window.tabs.currentWidget()
    second = QWidget()
    window._add_tab(second, "srv-02")
    window._mark_activity(second)
    assert window.tabs.tabText(window.tabs.indexOf(second)) == "srv-02", "aktywna zakładka bez znacznika"
    window._mark_activity(first)
    assert window.tabs.tabText(window.tabs.indexOf(first)) == "● srv-01", "brak znacznika aktywności"
    window.tabs.setCurrentWidget(first)
    assert window.tabs.tabText(window.tabs.indexOf(first)) == "srv-01", "znacznik nie zniknął"
    window._mark_activity(second)
    second.tab_name = "nowa"
    window._update_tab_text(second)
    assert window.tabs.tabText(window.tabs.indexOf(second)) == "● nowa", "zmiana nazwy zgubiła znacznik"
    second.terminal = SshTerminal.__new__(SshTerminal)  # atrapa: sama flaga
    window._set_read_only(second, True)
    assert window.tabs.tabText(window.tabs.indexOf(second)) == "● 🔒 nowa", "brak kłódki"
    window._set_read_only(second, False)
    assert window.tabs.tabText(window.tabs.indexOf(second)) == "● nowa"
    window._show_current_stats()  # komunikat o trybie zasłoniłby test paska niżej
    window._close_other_tabs(first)
    assert window.tabs.count() == 3 and window.tabs.widget(1) is first, "zamknij pozostałe"
    assert window.tabs.widget(2) is window._plus_tab, "+ musi zostać ostatnie"

    # Paleta poleceń: połączenia z drzewa i akcje z menu (także Programy
    # i skrypty), bez samej palety; słowa zapytania w dowolnej kolejności.
    labels = [label for label, _, _ in window.palette_entries()]
    assert any("srv-01" in label for label in labels), labels
    assert any(label.endswith(t("menu_settings").rstrip("…")) for label in labels), labels
    assert any(t("menu_tools") in label for label in labels), "brak menu Programy w palecie"
    assert not any(t("menu_command_palette").rstrip("…") in label for label in labels), \
        "paleta nie może podpowiadać samej siebie"
    assert palette_matches("Programy › Skaner sieci", "skan prog")
    assert not palette_matches("Programy › Skaner sieci", "dyski")
    ran = []
    palette = CommandPalette(window, [("A", "alfa", lambda: ran.append("a")),
                                      ("B", "beta", lambda: ran.append("b"))])
    palette.query.setText("bet")
    assert palette.results.count() == 1
    palette._choose_current()
    palette.chosen()
    assert ran == ["b"], ran

    # Home: przypięte na górze, potem ostatnio używane.
    order = home_order([{"name": "a", "last_used": 5}, {"name": "b", "pinned": True},
                        {"name": "c", "last_used": 9}, {"name": "d"}])
    assert [d["name"] for d in order] == ["b", "c", "a", "d"], order

    # Dashboard: tekst paska -> komórki; inny tekst w całości w drugiej kolumnie.
    row = dashboard_row("web", STATS_SEPARATOR.join([
        "CPU 95%", "RAM 1 GB / 2 GB (50%)", "dysk 40% (wolne 3 GB)",
        "↓ 1 KB/s  ↑ 2 KB/s", "uptime 3 d", "zalogowani: 2",
    ]))
    assert row == ["web", "95%", "1 GB / 2 GB (50%)", "40% (wolne 3 GB)", "↓ 1 KB/s  ↑ 2 KB/s", "3 d", "2"], row
    assert dashboard_row("rdp", "Sesja zakończona") == ["rdp", "Sesja zakończona", "", "", "", "", ""]
    assert over_threshold("95%", 90) and not over_threshold("1 GB / 2 GB (50%)", 90)
    assert not over_threshold("—", 90), "brak liczby to nie alarm"

    # Przeciąganie: korzeń nie odjeżdża, połączenie nie przyjmuje dzieci.
    assert not root.flags() & Qt.ItemIsDragEnabled, "korzeń musi zostać na miejscu"
    assert not window.tree._drop_allowed(None, False), "pusty obszar to nie cel"
    assert not window.tree._drop_allowed(conn, True), "połączenie nie może być grupą"
    assert window.tree._drop_allowed(conn, False), "obok połączenia wolno"
    assert window.tree._drop_allowed(group, True), "do grupy wolno"

    # Sam ruch elementu między grupami musi przetrwać zapis i odczyt.
    inna = QTreeWidgetItem(root, ["Testy"])
    group.removeChild(conn)
    inna.addChild(conn)
    assert conn.parent() is inna and group.childCount() == 0

    # Szyfrowanie haseł: tylko Windows, wynik nie może być czytelny gołym okiem.
    if CAN_STORE_PASSWORDS:
        stored = encrypt_password("tajne hasło")
        assert "tajne" not in stored, "hasło leży w pliku otwartym tekstem"
        assert decrypt_password(stored) == "tajne hasło", "odszyfrowanie nie działa"
        assert decrypt_password("bmllIGRwYXBp") is None, "śmieci muszą dać None"
        print("szyfrowanie haseł: OK")

    # Menu i pasek boczny: akcja "Lista połączeń" musi realnie chować drzewo.
    assert window.menuBar().actions(), "brak paska menu"
    assert any(a.text() == t("menu_tools") for a in window.menuBar().actions()), "brak menu Programy"

    # Skaner: wiersz z pingu i MAC z ARP muszą trafić do jednego wiersza tabeli.
    chosen = []
    dialog = scanner.ScannerDialog(window, lambda *args: chosen.append(args))
    dialog._add_row({"ip": "10.0.0.7", "name": "serwer", "ports": [22], "services": "SSH"})
    dialog._add_row({"ip": "10.0.0.7", "mac": "AA:BB:CC:DD:EE:FF"})
    dialog._add_row({"ip": "10.0.0.9", "mac": "11:22:33:44:55:66"})  # cichy host z ARP
    assert dialog.table.rowCount() == 1, "MAC nie może zakładać nowego wiersza"
    assert dialog.table.item(0, 2).text() == "AA:BB:CC:DD:EE:FF"
    assert dialog._copy(0, 1) == "10.0.0.7", "kopiowanie jednej kolumny"
    assert dialog._copy(0).split("	") == ["serwer", "10.0.0.7", "AA:BB:CC:DD:EE:FF", "SSH"],         "kopiowanie calego wiersza"
    dialog._open_default(dialog.table.item(0, 1))
    assert chosen == [("10.0.0.7", "ssh", "serwer")], chosen
    dialog.close()
    window.toggle_tree_action.setChecked(False)
    assert window.tree_panel.isHidden(), "toggle w menu/pasku bocznym nie ukrywa drzewa"
    window.toggle_tree_action.setChecked(True)

    # Dolny pasek: bez zakładek i dla obcego widgetu nie może się wywalić.
    assert window.statusBar().currentMessage() == t("status_idle")
    window._show_current_stats()
    assert window.statusBar().currentMessage() == t("status_idle")
    window._show_stats(QWidget(), "statystyki obcej zakładki")
    assert window.stats_graph.isHidden(), "wykres bez historii ma być schowany"
    window.tabs.currentWidget().history = [(10.0, 20.0), (15.0, 22.0)]
    window._show_current_stats()
    assert not window.stats_graph.isHidden(), "wykres aktywnej zakładki się nie pokazał"
    del window.tabs.currentWidget().history
    window._show_current_stats()
    assert window.statusBar().currentMessage() == t("status_idle"), "pasek pokazał nie tę zakładkę"

    # Wybór języka: menu i napisy muszą realnie się przełączać.
    assert window.tabs.tabText(0) == "🏠 Home", window.tabs.tabText(0)
    i18n.use("pl")
    assert ConnectionDialog().windowTitle() == "Połączenie SSH", "dialog nie idzie z i18n"
    i18n.use("en")
    assert ConnectionDialog().windowTitle() == "SSH connection"

    # Formularz: protokół steruje portem, tytułem i polem klucza.
    dialog = ConnectionDialog()
    assert dialog.values()["protocol"] == "ssh", "SSH ma zostać domyślne"
    assert dialog.port.value() == SSH_PORT
    dialog.protocol.setCurrentIndex(dialog.protocol.findData("rdp"))
    assert dialog.port.value() == RDP_PORT, "RDP ma swój port domyślny"
    assert dialog.values()["protocol"] == "rdp"
    assert dialog.windowTitle() == "RDP connection", dialog.windowTitle()

    # Ręcznie wpisanego portu przełącznik protokołu nie może nadpisać.
    custom = ConnectionDialog()
    custom.port.setValue(2222)
    custom.protocol.setCurrentIndex(custom.protocol.findData("rdp"))
    assert custom.port.value() == 2222, "własny port musi przeżyć zmianę protokołu"

    # Klucz prywatny zapisuje się tylko dla SSH.
    # Konto współdzielone: formularz nie trzyma własnych haseł, pola są wyszarzone,
    # a łączenie bierze login z konta. Plik kont w temp — nie w profilu użytkownika.
    real_credentials = credentials.CREDENTIALS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        credentials.CREDENTIALS_FILE = Path(tmp) / "credentials.json"
        try:
            credentials.save([{"id": "c1", "name": "admin", "username": "root"}])
            shared = ConnectionDialog(data={"host": "h", "username": "jan", "credential": "c1"})
            assert shared.credential.currentData() == "c1" and not shared.username.isEnabled()
            values = shared.values()
            assert values["credential"] == "c1" and "password" not in values, values
            assert credentials.effective_auth(values)["username"] == "root"
            shared.credential.setCurrentIndex(0)
            assert shared.username.isEnabled() and "credential" not in shared.values()
            gone = ConnectionDialog(data={"host": "h", "credential": "usuniete"})
            assert gone.credential.currentData() is None, "usunięte konto = własne pola"
        finally:
            credentials.CREDENTIALS_FILE = real_credentials

    keyed = ConnectionDialog(data={"host": "h", "key_file": "C:/klucze/id_rsa"})
    assert keyed.values()["key_file"] == "C:/klucze/id_rsa"
    keyed.protocol.setCurrentIndex(keyed.protocol.findData("rdp"))
    assert "key_file" not in keyed.values(), "RDP nie używa klucza SSH"

    # Haslo klucza to osobne pole niz haslo konta i zapisuje sie osobno.
    if CAN_STORE_PASSWORDS:
        keyed.protocol.setCurrentIndex(keyed.protocol.findData("ssh"))
        keyed.password.setText("haslo konta")
        keyed.passphrase.setText("haslo klucza")
        keyed.save_password.setChecked(True)
        values = keyed.values()
        assert decrypt_password(values["password"]) == "haslo konta", values
        assert decrypt_password(values["passphrase"]) == "haslo klucza", values
        # Wczytanie z powrotem musi rozdzielic je tak samo.
        again = ConnectionDialog(data=values)
        assert again.password.text() == "haslo konta"
        assert again.passphrase.text() == "haslo klucza", "passphrase wpadl w pole hasla"
        keyed.protocol.setCurrentIndex(keyed.protocol.findData("rdp"))
        assert "passphrase" not in keyed.values(), "RDP nie ma klucza SSH"

    # Tunele: menu Programy musi je wystawiac, a bez sesji nie moze sie wywalic.
    assert any(label == "menu_tunnels" for label, _ in TOOLS), TOOLS
    window.tabs.setCurrentIndex(0)  # Home to nie sesja SSH
    shown = []
    QMessageBox.information = staticmethod(lambda *a, **k: shown.append(a[-1]))
    window._manage_tunnels()
    assert shown == [t("tunnels_need_session")], shown

    # Podglad logu i menedzer uslug: to samo — bez sesji nie moga sie wywalic.
    assert any(label == "menu_logtail" for label, _ in TOOLS), TOOLS
    assert any(label == "menu_services" for label, _ in TOOLS), TOOLS
    shown.clear()
    window._open_log_tail()
    assert shown == [t("tunnels_need_session")], shown
    shown.clear()
    window._manage_services()
    assert shown == [t("tunnels_need_session")], shown

    # Panel dysków: bez sesji nie może się wywalić; generator kluczy działa nawet bez.
    assert any(label == "menu_disks" for label, _ in TOOLS), TOOLS
    assert any(label == "menu_keygen" for label, _ in TOOLS), TOOLS
    assert any(label == "menu_known_hosts" for label, _ in TOOLS), TOOLS
    shown.clear()
    window._open_disks()
    assert shown == [t("tunnels_need_session")], shown
    shown.clear()
    window._open_processes()
    assert shown == [t("tunnels_need_session")], shown
    assert any(label == "menu_patches" for label, _ in TOOLS), TOOLS
    shown.clear()
    window._open_patches()
    assert shown == [t("scripts_need_session")], shown
    assert any(label == "menu_logsearch" for label, _ in TOOLS), TOOLS
    shown.clear()
    window._open_logsearch()
    assert shown == [t("scripts_need_session")], shown
    assert any(label == "menu_docker" for label, _ in TOOLS), TOOLS
    shown.clear()
    window._open_docker()
    assert shown == [t("tunnels_need_session")], shown

    # Tagi: parsowanie i wyszukiwanie po #tagu (drzewo i Home tym samym tekstem).
    assert parse_tags(" Prod, #db,, prod ") == ["prod", "db"]
    assert "#db" in search_text({"name": "x", "tags": ["db"]})
    assert HomeTab.matches({"name": "x", "tags": ["klient-a"]}, "#klient")
    assert not HomeTab.matches({"name": "x"}, "#klient")

    # Biblioteka poleceń: menu „Polecenia” z makr (skrót z [ ]) — trafia też do palety;
    # bez sesji komunikat zamiast wyjątku.
    stored_macros = i18n.settings().value("macros")
    try:
        set_macros("Restart [Ctrl+Alt+R] = systemctl restart {{usługa}}\nUptime = uptime")
        window._fill_snippet_menu()
        actions = [a for a in window.snippet_menu.actions() if not a.isSeparator()]
        assert [a.text() for a in actions[:2]] == ["Restart", "Uptime"], [a.text() for a in actions]
        assert actions[0].shortcut().toString() == "Ctrl+Alt+R"
        labels = [label for label, _ in menu_entries(window.menuBar())]
        assert any(label.endswith("› Uptime") for label in labels), labels
        window.tabs.setCurrentIndex(0)
        window._send_snippet("uptime")
        assert window.statusBar().currentMessage() == t("scripts_need_session")
    finally:
        if stored_macros is None:
            i18n.settings().remove("macros")
        else:
            i18n.settings().setValue("macros", stored_macros)
        window._fill_snippet_menu()

    # Okno Ustawień: bieżące wartości wchodzą, te same wychodzą; PIN pilnowany.
    assert any(a.text() == t("menu_settings") for a in window.findChildren(QAction))
    current = window.current_settings()
    dialog = settings.SettingsDialog(window, current, TERMINAL_THEMES, i18n.LANGUAGES)
    values = dialog.values()
    for key, value in values.items():
        if key != "new_pin":
            assert value == current[key], (key, value, current[key])
    dialog.new_pin.setText("12")
    assert dialog.pin_error() == t("lock_pin_too_short")
    dialog.new_pin.setText("")
    dialog._pin_set = False
    dialog.lock.setChecked(True)
    assert dialog.pin_error() == t("settings_pin_required"), "blokada bez PIN-u"
    dialog.lock.setChecked(False)
    assert dialog.pin_error() is None
    dialog.deleteLater()

    # Notatki i polecenia startowe: startowe tylko dla SSH, notatki dla obu.
    extra = ConnectionDialog(
        data={"host": "h", "startup": "cd /var/log", "notes": "serwer klienta X"}
    )
    values = extra.values()
    assert values["startup"] == "cd /var/log", values
    assert values["notes"] == "serwer klienta X"
    extra.protocol.setCurrentIndex(extra.protocol.findData("rdp"))
    assert "startup" not in extra.values(), "RDP nie ma powloki do karmienia"
    assert extra.values()["notes"] == "serwer klienta X", "notatki dotycza obu protokolow"

    # Przekazanie agenta SSH: opt-in, tylko SSH, wraca przy edycji.
    agent = ConnectionDialog(data={"host": "h"})
    assert "forward_agent" not in agent.values()
    agent.forward_agent.setChecked(True)
    assert agent.values()["forward_agent"] is True
    agent_again = ConnectionDialog(data=agent.values())
    assert agent_again.forward_agent.isChecked()
    agent.protocol.setCurrentIndex(agent.protocol.findData("rdp"))
    assert "forward_agent" not in agent.values(), "RDP nie ma agenta SSH"

    # Środowisko: zapis w formularzu, kolor zakładki, pytanie tylko dla produkcji.
    env_form = ConnectionDialog(data={"host": "h"})
    assert "environment" not in env_form.values()
    env_form.environment.setCurrentIndex(env_form.environment.findData("prod"))
    prod_conn = env_form.values()
    assert prod_conn["environment"] == "prod"
    env_again = ConnectionDialog(data=prod_conn)
    assert env_again.environment.currentData() == "prod", "środowisko wraca przy edycji"
    prod_tab = QWidget()
    index = window._add_tab(prod_tab, "prod-01", prod_conn)
    assert window.tabs.tabBar().tabTextColor(index).name() == ENV_COLORS["prod"]
    prod_conn["environment"] = "dev"
    window._refresh_tab_texts()
    assert window.tabs.tabBar().tabTextColor(index).name() == ENV_COLORS["dev"]
    assert window._confirm_prod(prod_conn, prod_tab), "nie-produkcja nie pyta"
    window.tabs.removeTab(index)

    # Przywracanie sesji: otwarte zakładki z drzewa zapisane jako (nazwa, host,
    # port); po starcie otwierane z drzewa, usunięte pomijane, wyłączone = nic.
    window._add_tab(QWidget(), data["name"], data)
    window._add_tab(QWidget(), "szybkie")  # szybkie połączenie — bez wpisu w drzewie
    keys = window.open_session_keys()
    assert keys == [["srv-01", "10.0.0.1", 22]], keys
    stored_sessions = {k: i18n.settings().value(k) for k in ("open_sessions", "restore_sessions")}
    opened = []
    real_open = window._open_connection_tab
    window._open_connection_tab = opened.append
    try:
        i18n.settings().setValue("open_sessions", json.dumps(keys + [["usuniety", "x", 22]]))
        window.restore_sessions()
        assert opened == [data], opened
        i18n.settings().setValue("restore_sessions", False)
        window.restore_sessions()
        assert len(opened) == 1, "wyłączone przywracanie coś otworzyło"
    finally:
        window._open_connection_tab = real_open
        for key, value in stored_sessions.items():
            if value is None:
                i18n.settings().remove(key)
            else:
                i18n.settings().setValue(key, value)
    for i in range(window.tabs.count() - 2, 0, -1):
        window.tabs.removeTab(i)

    # Edycja polaczenia nie moze skasowac zakladek SFTP ani tuneli - dawniej
    # values() budowalo slownik tylko z pol formularza i gubilo reszte.
    with_extras = ConnectionDialog(
        data={"host": "h", "bookmarks": ["/var/log"], "tunnels": [{"local": 8080}]}
    )
    saved = with_extras.values()
    assert saved["bookmarks"] == ["/var/log"], "edycja skasowala zakladki SFTP"
    assert saved["tunnels"] == [{"local": 8080}], "edycja skasowala tunele"

    # Blokada po bezczynności — QSettings tego testu nie mogą zostać w realnym
    # profilu użytkownika (inaczej --selftest włączyłby blokadę z PIN-em "1234"
    # w prawdziwej aplikacji), stąd zapis/odtworzenie na wejściu i wyjściu.
    _stored_lock = {
        key: i18n.settings().value(key)
        for key in ("lock_enabled", "lock_pin_hash", "lock_timeout")
    }
    try:
        assert _hash_pin("1234") == _hash_pin("1234")
        assert _hash_pin("1234") != _hash_pin("4321")

        # Za krótki PIN albo Cancel nie mogą włączyć blokady.
        QInputDialog.getText = staticmethod(lambda *a, **k: ("ab", True))
        window._toggle_lock(True)
        assert not i18n.settings().value("lock_enabled", False, type=bool), "krotki PIN wlaczyl blokade"
        QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
        window._toggle_lock(True)
        assert not i18n.settings().value("lock_enabled", False, type=bool), "Cancel wlaczyl blokade"

        # Poprawny PIN włącza blokadę i zbroi timer.
        QInputDialog.getText = staticmethod(lambda *a, **k: ("1234", True))
        window._toggle_lock(True)
        assert i18n.settings().value("lock_enabled", False, type=bool), "poprawny PIN nie wlaczyl blokady"
        assert i18n.settings().value("lock_pin_hash") == _hash_pin("1234")
        assert window._lock_timer.isActive(), "wlaczenie blokady musi zazbroic timer"

        # Ekran blokady: zly PIN nie odblokowuje, dobry — tak; Escape/X nie zamykają.
        dialog = _LockDialog(window)
        dialog.pin_field.setText("0000")
        dialog._try_unlock()
        assert not dialog._unlocked, "zly PIN nie moze odblokowac"
        close_event = QCloseEvent()
        dialog.closeEvent(close_event)
        assert not close_event.isAccepted(), "blokada nie moze zamknac sie bez PIN-u"
        dialog.reject()  # Escape — nie ma prawa nic zrobic
        assert not dialog._unlocked
        dialog.pin_field.setText("1234")
        dialog._try_unlock()
        assert dialog._unlocked, "dobry PIN musi odblokowac"
        dialog.deleteLater()

        # Wyłączenie zatrzymuje timer.
        window._toggle_lock(False)
        assert not i18n.settings().value("lock_enabled", False, type=bool)
        assert not window._lock_timer.isActive(), "wylaczenie blokady musi zdjac timer"
    finally:
        for key, value in _stored_lock.items():
            if value is None:
                i18n.settings().remove(key)
            else:
                i18n.settings().setValue(key, value)
        if window._lock_timer:
            window._lock_timer.stop()

    # Duplikat: kopia obok oryginalu, z tymi samymi danymi i inna nazwa.
    window.tree._toggle_pin(conn)
    assert conn.data(0, CONNECTION_DATA)["pinned"] is True, "przypięcie"
    clone = window.tree._duplicate(conn)
    assert clone.parent() is conn.parent()
    assert clone.data(0, CONNECTION_DATA)["host"] == data["host"]
    assert clone.data(0, CONNECTION_DATA)["name"] != data["name"], "kopia musi sie odroznic"
    assert "pinned" not in clone.data(0, CONNECTION_DATA), "kopia nie może być przypięta"
    data.setdefault("bookmarks", []).append("/tylko-oryginal")
    assert "/tylko-oryginal" not in clone.data(0, CONNECTION_DATA).get("bookmarks", []),         "kopia dzieli listę zakładek z oryginałem"
    data["bookmarks"].remove("/tylko-oryginal")
    clone.parent().removeChild(clone)
    window.tree._toggle_pin(conn)
    assert "pinned" not in conn.data(0, CONNECTION_DATA), "odpięcie"

    # Filtr drzewa: grupa zostaje widoczna, gdy pasuje cokolwiek w srodku.
    window.tree.filter("srv-01")
    assert not conn.isHidden() and not conn.parent().isHidden()
    window.tree.filter("nie-ma-takiego")
    assert conn.isHidden() and conn.parent().isHidden(), "nic nie pasuje = wszystko schowane"
    window.tree.filter("10.0.0.1")
    assert not conn.isHidden(), "filtr ma lapac takze host"
    window.tree.filter("")
    assert not conn.isHidden(), "puste pole odslania wszystko"

    # Skróty do zakładek: "+" nie może być celem przełączania.
    window.tabs.insertTab(window.tabs.count() - 1, QWidget(), "druga")
    order = window.tab_order()
    assert window.tabs.count() - 1 == len(order), "+ nie moze byc w kolejce zakladek"
    window.tabs.setCurrentIndex(0)
    window._cycle_tab(1)
    assert window.tabs.currentIndex() == order[1], window.tabs.currentIndex()
    window._cycle_tab(-1)
    assert window.tabs.currentIndex() == order[0]
    window._cycle_tab(-1)
    assert window.tabs.currentIndex() == order[-1], "cofanie z pierwszej ma zawijac na ostatnia"
    window._go_to_tab(0)
    assert window.tabs.currentIndex() == order[0]
    window._go_to_tab(50)  # numer poza zakresem ma być bez efektu, nie wyjątkiem
    assert window.tabs.currentIndex() == order[0]

    # Import z ~/.ssh/config: wpisy wchodza jako grupa, wzorce odpadaja.
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config"
        config.write_text(
            # `Host *` na koncu, tak jak w prawdziwym pliku: w ssh_config wygrywa
            # pierwsza napotkana wartosc, wiec wzorzec ma tylko uzupelniac.
            "Host bastion\n  HostName 10.0.0.1\n  User admin\n  Port 2222\n\n"
            "Host www\n  HostName 10.0.0.2\n\n"
            "Host *\n  User domyslny\n",
            encoding="utf-8",
        )
        fresh = ConnectionTree()
        assert fresh.import_ssh_config(config) == 2, "wzorzec Host * nie jest polaczeniem"
        group = fresh.topLevelItem(0).child(fresh.topLevelItem(0).childCount() - 1)
        entries = {
            fresh.item_name(group.child(i)): group.child(i).data(0, CONNECTION_DATA)
            for i in range(group.childCount())
        }
        assert entries["bastion"]["port"] == 2222, entries
        assert entries["bastion"]["username"] == "admin"
        assert entries["www"]["host"] == "10.0.0.2"
        assert entries["www"]["username"] == "domyslny", "Host * ma dawac wartosci domyslne"
        assert fresh.import_ssh_config(config.with_name("brak")) is None, "brak pliku = None"

        # Import z innych programów: plik -> nowa grupa z podgrupami, komunikat z liczbą;
        # zły plik = ostrzeżenie, nie wyjątek.
        assert [s for _, s in IMPORT_SOURCES] == ["putty", "mobaxterm", "mremoteng", "csv"]
        listing = Path(tmp) / "serwery.csv"
        listing.write_text("name;host;group\nweb;10.0.0.1;Klient/Prod\nad;10.0.0.5;\n",
                           encoding="utf-8-sig")
        shown.clear()
        tree_root = window.tree.topLevelItem(0)
        before = tree_root.childCount()
        window._import_from_program("csv", "import_csv", listing)
        assert shown == [t("import_other_done", 2)], shown
        imported = tree_root.child(before)
        assert window.tree.item_name(imported) == t("import_group", t("import_csv").rstrip("…"))
        prod = imported.child(0).child(0)
        assert window.tree.item_name(prod) == "Prod" and prod.child(0).data(0, CONNECTION_DATA)["host"] == "10.0.0.1"
        tree_root.removeChild(imported)
        broken = Path(tmp) / "zly.xml"
        broken.write_text("<to nie jest xml", encoding="utf-8")
        warned, real_warning = [], QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[-1]))
        try:
            window._import_from_program("mremoteng", "import_mremoteng", broken)
        finally:
            QMessageBox.warning = real_warning
        assert len(warned) == 1 and tree_root.childCount() == before, "zły plik coś dodał"

    # Podział ekranu: zakładki sesji chowają się, Ctrl+Tab je omija, a
    # zamknięcie siatki to „Rozdziel” — sesje wracają, nic się nie rozłącza.
    fakes = []
    for name in ("s1", "s2"):
        fake = QWidget()
        fake.split, fake.terminal = None, ssh_terminal.OfflineTerminal()
        fake.splitter = QSplitter(fake)
        fake.splitter.addWidget(QWidget())
        fake.splitter.addWidget(fake.terminal)
        window._add_tab(fake, name)
        fakes.append(fake)
    grid = split.SplitTab(fakes, window._on_split_released)
    for fake in fakes:
        window.tabs.setTabVisible(window.tabs.indexOf(fake), False)
    window._add_tab(grid, "grid")
    assert window._current_session() is fakes[0], "w siatce narzędzia biorą sesję z siatki"
    order = window.tab_order()
    assert all(window.tabs.indexOf(f) not in order for f in fakes), "schowane w Ctrl+Tab"
    window._close_tab(window.tabs.indexOf(grid))
    assert window.tabs.indexOf(grid) == -1 and fakes[0].split is None
    assert all(window.tabs.isTabVisible(window.tabs.indexOf(f)) for f in fakes)
    for fake in fakes:
        window._close_tab(window.tabs.indexOf(fake))

    i18n.selftest()

    importers.selftest()
    ssh_terminal.selftest()
    i18n.use("en")  # ssh_terminal.selftest() bawi się językiem
    rdp.selftest()
    servers.selftest()
    update.selftest()
    scanner.selftest()
    notify.selftest()
    tunnels_module.selftest()
    logtail.selftest()
    services.selftest()
    containers.selftest()
    processes.selftest()
    multirun.selftest()
    patches.selftest()
    logsearch.selftest()
    transfers.selftest()
    sftp.selftest()
    graphs.selftest()
    credentials.selftest()
    split.selftest()
    monitor.selftest()
    del app
    print("main selftest OK")


def main():
    app = QApplication(sys.argv)
    i18n.load()  # przed zbudowaniem okna — napisy czytane są raz
    if i18n.settings().value("dark_mode", False, type=bool):
        app.setPalette(dark_palette())
    window = MainWindow()
    window.show()
    window.check_updates()
    window.start_status_polling()
    window.start_monitoring()
    window.start_lock_watch()
    # Po pokazaniu okna — łączenie z oknami postępu ma się dziać nad gotowym oknem.
    QTimer.singleShot(0, window.restore_sessions)
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
