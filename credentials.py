"""Poświadczenia: szyfrowanie haseł (DPAPI) i konta współdzielone przez wiele połączeń.

Konto (login + hasło + klucz + hasło klucza) żyje w `credentials.json`,
połączenie trzyma tylko jego `id` w polu `"credential"`. Zmiana hasła na
koncie = zmiana na wszystkich połączeniach naraz. Usunięte konto nie psuje
połączenia — wraca ono do własnych pól z formularza.
"""

import base64
import ctypes
import json
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from i18n import t

# Hasła szyfrujemy DPAPI: klucz jest przypisany do konta Windows,
# więc plik skopiowany na inny komputer jest bezużyteczny.
CAN_STORE_PASSWORDS = sys.platform == "win32"

CREDENTIALS_FILE = Path(__file__).with_name("credentials.json")
AUTH_KEYS = ("username", "password", "key_file", "passphrase")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(func, data):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI refused the operation")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt_password(text):
    """Szyfruje hasło dla bieżącego konta Windows; zwraca tekst do JSON-a."""
    blob = _dpapi(ctypes.windll.crypt32.CryptProtectData, text.encode("utf-8"))
    return base64.b64encode(blob).decode("ascii")


def decrypt_password(stored):
    """Odwrotność `encrypt_password`. Cudze lub uszkodzone dane = None."""
    try:
        blob = _dpapi(ctypes.windll.crypt32.CryptUnprotectData, base64.b64decode(stored))
    except (OSError, ValueError):
        return None
    return blob.decode("utf-8")


# --- magazyn kont -------------------------------------------------------------


def load(path=None):
    """-> (lista kont, tekst błędu). Uszkodzony plik idzie do `.json.bak`, jak
    `connections.json` — inaczej najbliższy zapis nadpisałby go w ciszy."""
    path = Path(path or CREDENTIALS_FILE)
    if not path.exists():
        return [], ""
    try:
        creds = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(creds, list) or not all(
            isinstance(c, dict) and c.get("id") and c.get("name") for c in creds
        ):
            raise ValueError(t("err_not_a_list"))
    except (OSError, ValueError) as error:
        try:
            path.replace(path.with_suffix(".json.bak"))
        except OSError:
            pass
        return [], str(error)
    return creds, ""


def save(creds, path=None):
    Path(path or CREDENTIALS_FILE).write_text(
        json.dumps(creds, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def find(creds, cred_id):
    return next((c for c in creds if c["id"] == cred_id), None) if cred_id else None


def effective_auth(conn, creds=None):
    """Login/hasło/klucz do łączenia: z konta, jeśli połączenie je wskazuje i
    konto istnieje, inaczej z samego połączenia. Hasła zostają zaszyfrowane."""
    if creds is None:
        creds = load()[0]
    source = find(creds, conn.get("credential")) or conn
    return {key: source.get(key, "") for key in AUTH_KEYS}


def usage_count(nodes, cred_id):
    """Ile połączeń w drzewie (format `connections.json`) wskazuje to konto."""
    count = 0
    for node in nodes:
        if node.get("connection", {}).get("credential") == cred_id:
            count += 1
        count += usage_count(node.get("children", []), cred_id)
    return count


# --- okna ------------------------------------------------------------------------


class CredentialDialog(QDialog):
    """Jedno konto: nazwa, login, hasło, klucz prywatny, hasło klucza."""

    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle(t("cred_title"))
        self._data = data or {}
        self.name = QLineEdit(self._data.get("name", ""))
        self.username = QLineEdit(self._data.get("username", ""))
        self.password = QLineEdit(self._secret("password"))
        self.password.setEchoMode(QLineEdit.Password)
        self.key_file = QLineEdit(self._data.get("key_file", ""))
        browse = QPushButton(t("btn_browse"))
        browse.clicked.connect(self._pick_key_file)
        key_box = QWidget()
        key_layout = QHBoxLayout(key_box)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_file)
        key_layout.addWidget(browse)
        self.passphrase = QLineEdit(self._secret("passphrase"))
        self.passphrase.setEchoMode(QLineEdit.Password)
        self.passphrase.setToolTip(t("tip_passphrase"))
        # Bez DPAPI nie ma gdzie bezpiecznie trzymać haseł — konto niesie wtedy
        # tylko login i klucz, hasło padnie przy łączeniu jak dotąd.
        for field in (self.password, self.passphrase):
            field.setEnabled(CAN_STORE_PASSWORDS)
            if not CAN_STORE_PASSWORDS:
                field.setToolTip(t("tip_save_password_windows_only"))

        form = QFormLayout(self)
        form.addRow(t("fld_name"), self.name)
        form.addRow(t("fld_user"), self.username)
        form.addRow(t("fld_password"), self.password)
        form.addRow(t("fld_key_file"), key_box)
        form.addRow(t("fld_passphrase"), self.passphrase)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _secret(self, key):
        stored = self._data.get(key)
        return (decrypt_password(stored) or "") if stored else ""

    def _pick_key_file(self):
        path, _ = QFileDialog.getOpenFileName(self, t("dlg_key_file"), "", t("filter_all_files"))
        if path:
            self.key_file.setText(path)

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, t("err_missing_data_title"), t("cred_need_name"))
            return
        super().accept()

    def values(self):
        data = {
            "id": self._data.get("id") or uuid.uuid4().hex,
            "name": self.name.text().strip(),
            "username": self.username.text().strip(),
        }
        if self.key_file.text().strip():
            data["key_file"] = self.key_file.text().strip()
        if CAN_STORE_PASSWORDS and self.password.text():
            data["password"] = encrypt_password(self.password.text())
        if CAN_STORE_PASSWORDS and self.passphrase.text():
            data["passphrase"] = encrypt_password(self.passphrase.text())
        return data


class CredentialManager(QDialog):
    """Lista kont z dodawaniem, edycją i usuwaniem. `nodes()` -> drzewo połączeń
    (do liczenia, ile połączeń korzysta z konta)."""

    def __init__(self, parent, nodes):
        super().__init__(parent)
        self.setWindowTitle(t("menu_credentials").rstrip("…"))
        self.resize(460, 320)
        self.nodes = nodes
        self.creds, error = load()
        if error:
            QMessageBox.warning(self, t("err_load_title"), t("cred_load_failed", error))

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._edit())
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        for text, handler in (
            (t("cred_add"), self._add), (t("cred_edit"), self._edit), (t("cred_delete"), self._delete)
        ):
            buttons.addButton(text, QDialogButtonBox.ActionRole).clicked.connect(handler)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        nodes = self.nodes()
        for cred in self.creds:
            used = usage_count(nodes, cred["id"])
            item = QListWidgetItem(
                t("cred_row", cred["name"], cred.get("username", ""), used)
            )
            item.setData(Qt.UserRole, cred["id"])
            self.list.addItem(item)

    def _current(self):
        item = self.list.currentItem()
        return find(self.creds, item.data(Qt.UserRole)) if item else None

    def _store(self):
        try:
            save(self.creds)
        except OSError as error:
            QMessageBox.warning(self, t("err_save_title"), t("err_save_body", error))
        self._refresh()

    def _add(self):
        dialog = CredentialDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.creds.append(dialog.values())
            self._store()

    def _edit(self):
        cred = self._current()
        if cred is None:
            return
        dialog = CredentialDialog(self, cred)
        if dialog.exec() == QDialog.Accepted:
            self.creds[self.creds.index(cred)] = dialog.values()
            self._store()

    def _delete(self):
        cred = self._current()
        if cred is None:
            return
        used = usage_count(self.nodes(), cred["id"])
        if QMessageBox.question(
            self, t("cred_delete"), t("cred_delete_confirm", cred["name"], used)
        ) != QMessageBox.Yes:
            return
        self.creds.remove(cred)
        self._store()


def selftest():
    """Magazyn, rozwiązywanie konta i liczenie użyć — bez GUI i bez DPAPI."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "credentials.json"
        assert load(path) == ([], "")
        creds = [{"id": "a1", "name": "admin", "username": "root", "key_file": "C:/k"}]
        save(creds, path)
        assert load(path) == (creds, "")

        # Uszkodzony plik: pusta lista + błąd, a oryginał odłożony, nie skasowany.
        path.write_text('[{"name": "bez id"}]', encoding="utf-8")
        loaded, error = load(path)
        assert loaded == [] and error and path.with_suffix(".json.bak").exists()

    own = {"username": "jan", "password": "blob-jana", "credential": "a1"}
    auth = effective_auth(own, creds)
    assert auth == {"username": "root", "password": "", "key_file": "C:/k", "passphrase": ""}, auth
    # Usunięte konto: połączenie wraca do własnych pól.
    assert effective_auth(own, [])["password"] == "blob-jana"
    assert effective_auth({"username": "x"}, creds)["username"] == "x"

    nodes = [
        {"name": "g", "children": [
            {"name": "s1", "connection": {"credential": "a1"}},
            {"name": "s2", "connection": {}},
        ]},
        {"name": "s3", "connection": {"credential": "a1"}},
    ]
    assert usage_count(nodes, "a1") == 2 and usage_count(nodes, "zz") == 0
    print("credentials selftest OK")
