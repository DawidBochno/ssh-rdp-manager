"""Generator kluczy SSH — ssh-keygen po naszej stronie.

Paramiko już jest zależnością (terminal, SFTP) i ma generator kluczy, więc
zamiast wywoływać zewnętrzny `ssh-keygen.exe` (może go nie być w PATH) piszemy
plik prywatny sami. Wgranie do `authorized_keys` idzie tym samym `_try_command`
co skrypty administracyjne i menedżer usług.
"""

import paramiko
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from i18n import t
from ssh_terminal import _try_command

# Nazwa w menu -> funkcja generująca. Ed25519 jest krótszy i szybszy do
# wygenerowania niż RSA, ale nie każdy stary serwer go rozumie — stąd oba.
KEY_TYPES = {
    "RSA 4096": lambda: paramiko.RSAKey.generate(4096),
    "RSA 2048": lambda: paramiko.RSAKey.generate(2048),
    "Ed25519": lambda: paramiko.Ed25519Key.generate(),
}


def public_line(key, comment=""):
    """Linia jak w `id_rsa.pub`: nazwa typu, base64, opcjonalny komentarz."""
    line = f"{key.get_name()} {key.get_base64()}"
    return f"{line} {comment}" if comment else line


def install_command(pub_line):
    """Polecenie dopisujące klucz do `~/.ssh/authorized_keys` (Linux)."""
    quoted = pub_line.replace("'", "'\\''")
    return (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"echo '{quoted}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    )


class KeyGenDialog(QDialog):
    """Wygeneruj parę kluczy, zapisz prywatny lokalnie, wgraj publiczny jednym klikiem."""

    def __init__(self, parent, client=None):
        super().__init__(parent)
        self.client = client  # None = brak aktywnej sesji SSH, przycisk wgrania wyłączony
        self.key = None
        self.setWindowTitle(t("keygen_title"))
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(t("keygen_type")))
        self.type_box = QComboBox()
        self.type_box.addItems(list(KEY_TYPES))
        row.addWidget(self.type_box)
        generate = QPushButton(t("keygen_generate"))
        generate.clicked.connect(self._generate)
        row.addWidget(generate)
        layout.addLayout(row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText(t("keygen_placeholder"))
        layout.addWidget(self.output)

        actions = QHBoxLayout()
        save = QPushButton(t("keygen_save"))
        save.clicked.connect(self._save_private)
        actions.addWidget(save)
        copy = QPushButton(t("keygen_copy_pub"))
        copy.clicked.connect(self._copy_public)
        actions.addWidget(copy)
        self.install_button = QPushButton(t("keygen_install"))
        self.install_button.clicked.connect(self._install)
        self.install_button.setEnabled(self.client is not None)
        actions.addWidget(self.install_button)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _generate(self):
        self.key = KEY_TYPES[self.type_box.currentText()]()
        self.output.setPlainText(public_line(self.key))

    def _save_private(self):
        if not self.key:
            QMessageBox.information(self, t("keygen_title"), t("keygen_no_key"))
            return
        path, _ = QFileDialog.getSaveFileName(self, t("keygen_save"), "id_key")
        if not path:
            return
        try:
            self.key.write_private_key_file(path)
        except OSError as error:
            QMessageBox.warning(self, t("keygen_title"), str(error))
            return
        QMessageBox.information(self, t("keygen_title"), t("keygen_saved", path))

    def _copy_public(self):
        if not self.key:
            QMessageBox.information(self, t("keygen_title"), t("keygen_no_key"))
            return
        QGuiApplication.clipboard().setText(self.output.toPlainText())

    def _install(self):
        if not self.key or not self.client:
            return
        text = _try_command(self.client, install_command(self.output.toPlainText().strip()))
        if text is None:
            QMessageBox.warning(self, t("keygen_title"), t("keygen_install_failed"))
        else:
            QMessageBox.information(self, t("keygen_title"), t("keygen_installed"))


def selftest():
    key = paramiko.RSAKey.generate(1024)  # szybciej niż 4096 — test sprawdza tylko format
    line = public_line(key, "test@host")
    assert line.startswith("ssh-rsa ") and line.endswith("test@host"), line

    cmd = install_command(line)
    assert "mkdir -p ~/.ssh" in cmd and "authorized_keys" in cmd, cmd

    tricky = public_line(key, "o'brien")
    quoted_cmd = install_command(tricky)
    assert "o'\\''brien" in quoted_cmd, quoted_cmd  # apostrof bezpiecznie zamknięty
    print("keygen selftest OK")


if __name__ == "__main__":
    selftest()
