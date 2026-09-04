"""Powiadomienia systemowe — dymek z zasobnika, gdy coś długiego się skończy.

`QSystemTrayIcon` jest w PySide6, więc żadnej nowej zależności. Ikona powstaje
raz, przy pierwszym powiadomieniu: tworzenie jej w `main()` zabierałoby miejsce
w zasobniku także wtedy, gdy nic nigdy nie zawiadomi.

ponytail: bez kolejki i bez historii — dymek pokazuje ostatnią rzecz i tyle.
"""

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


def selftest():
    app = QApplication.instance() or QApplication([])
    # Bez zasobnika (serwer bez pulpitu) `notify` ma milczeć, a nie się wywalić.
    assert notify("test", "tresc") in (True, False)
    del app
    print("notify selftest OK")


if __name__ == "__main__":
    selftest()
