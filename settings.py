"""Okno Ustawień — zastępuje kilkanaście pozycji menu Widok.

Samo okno nie zna `MainWindow` ani `QSettings`: dostaje słownik bieżących
wartości (`MainWindow.current_settings()`), oddaje słownik nowych (`values()`),
a stosuje je `MainWindow.apply_settings()`. Dzięki temu da się je sprawdzić
w selftescie bez klikania i bez dotykania profilu użytkownika.
"""
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from i18n import t

MIN_PIN = 4


def _spin(value, low, high, step=1, suffix=""):
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    spin.setValue(int(value))
    spin.setSuffix(suffix)
    return spin


class SettingsDialog(QDialog):
    def __init__(self, parent, current, themes, languages):
        super().__init__(parent)
        self.setWindowTitle(t("settings_title"))
        self.resize(520, 420)
        self._font = current["font"]
        self._pin_set = current["pin_set"]
        tabs = QTabWidget()

        # --- Terminal ---
        self.highlighting = QCheckBox(t("menu_highlighting"))
        self.highlighting.setChecked(current["highlighting"])
        self.timestamps = QCheckBox(t("menu_timestamps"))
        self.timestamps.setChecked(current["timestamps"])
        self.font_button = QPushButton()
        self.font_button.clicked.connect(self._pick_font)
        self._show_font()
        self.scrollback = _spin(current["scrollback"], 100, 200000, 500)
        self.triggers = QPlainTextEdit(current["triggers"])
        self.triggers.setPlaceholderText(t("triggers_hint"))
        self.triggers.setToolTip(t("triggers_hint"))
        form = self._tab(tabs, t("settings_tab_terminal"))
        form.addRow(self.highlighting)
        form.addRow(self.timestamps)
        form.addRow(t("settings_font"), self.font_button)
        form.addRow(t("settings_scrollback"), self.scrollback)
        form.addRow(t("settings_triggers"), self.triggers)

        # --- Wygląd ---
        self.theme = QComboBox()
        self.theme.addItems(list(themes))
        self.theme.setCurrentText(current["theme"])
        self.dark_mode = QCheckBox(t("menu_dark_mode"))
        self.dark_mode.setChecked(current["dark_mode"])
        self.language = QComboBox()
        for code, name in languages.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(current["language"])))
        form = self._tab(tabs, t("settings_tab_appearance"))
        form.addRow(t("menu_theme"), self.theme)
        form.addRow(self.dark_mode)
        form.addRow(t("menu_language"), self.language)
        form.addRow(QLabel(t("settings_language_note")))

        # --- Powiadomienia i status ---
        self.tree_status = QCheckBox(t("menu_tree_status"))
        self.tree_status.setChecked(current["tree_status"])
        self.tree_status_interval = _spin(current["tree_status_interval"], 10, 3600, 10, " s")
        self.alerts = QCheckBox(t("menu_alerts"))
        self.alerts.setChecked(current["alerts"])
        self.alert_threshold = _spin(current["alert_threshold"], 1, 100, 1, " %")
        form = self._tab(tabs, t("settings_tab_notifications"))
        form.addRow(self.tree_status)
        form.addRow(t("settings_status_interval"), self.tree_status_interval)
        form.addRow(self.alerts)
        form.addRow(t("settings_alert_threshold"), self.alert_threshold)
        self.tree_status.toggled.connect(self.tree_status_interval.setEnabled)
        self.alerts.toggled.connect(self.alert_threshold.setEnabled)

        # --- Bezpieczeństwo ---
        self.lock = QCheckBox(t("menu_lock"))
        self.lock.setChecked(current["lock"])
        self.lock_timeout = _spin(current["lock_timeout"], 1, 240, 1, " min")
        self.new_pin = QLineEdit()
        self.new_pin.setEchoMode(QLineEdit.Password)
        self.new_pin.setPlaceholderText(
            t("settings_pin_keep") if self._pin_set else t("settings_pin_required")
        )
        form = self._tab(tabs, t("settings_tab_security"))
        form.addRow(self.lock)
        form.addRow(t("settings_lock_timeout"), self.lock_timeout)
        form.addRow(t("settings_new_pin"), self.new_pin)
        self.lock.toggled.connect(self.lock_timeout.setEnabled)

        # Stan wyszarzenia od razu zgodny z checkboxami, nie dopiero po kliknięciu.
        self.tree_status_interval.setEnabled(self.tree_status.isChecked())
        self.alert_threshold.setEnabled(self.alerts.isChecked())
        self.lock_timeout.setEnabled(self.lock.isChecked())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    @staticmethod
    def _tab(tabs, title):
        page = QWidget()
        tabs.addTab(page, title)
        return QFormLayout(page)

    def _show_font(self):
        self.font_button.setText(f"{self._font.family()}, {self._font.pointSize()} pt")

    def _pick_font(self):
        font, ok = QFontDialog.getFont(self._font, self, t("settings_font"))
        if ok:
            self._font = font
            self._show_font()

    def pin_error(self):
        """Tekst błędu PIN-u albo None. Osobno od `accept`, żeby dało się testować."""
        pin = self.new_pin.text()
        if pin and len(pin) < MIN_PIN:
            return t("lock_pin_too_short")
        if self.lock.isChecked() and not pin and not self._pin_set:
            return t("settings_pin_required")
        return None

    def accept(self):
        error = self.pin_error()
        if error:
            QMessageBox.warning(self, t("settings_title"), error)
            return
        super().accept()

    def values(self):
        return {
            "highlighting": self.highlighting.isChecked(),
            "timestamps": self.timestamps.isChecked(),
            "font": self._font,
            "scrollback": self.scrollback.value(),
            "triggers": self.triggers.toPlainText(),
            "theme": self.theme.currentText(),
            "dark_mode": self.dark_mode.isChecked(),
            "language": self.language.currentData(),
            "tree_status": self.tree_status.isChecked(),
            "tree_status_interval": self.tree_status_interval.value(),
            "alerts": self.alerts.isChecked(),
            "alert_threshold": self.alert_threshold.value(),
            "lock": self.lock.isChecked(),
            "lock_timeout": self.lock_timeout.value(),
            "new_pin": self.new_pin.text(),
        }
