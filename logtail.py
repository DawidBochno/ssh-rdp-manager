"""Podgląd logu na żywo w osobnej zakładce: `tail -f` na osobnym kanale.

Idzie osobnym kanałem (`exec_command`), tak jak `_StatsPoller` i skrypty
administracyjne — nie koliduje z powłoką w zakładce sesji i znika razem
z zamknięciem karty. Czyta go ten sam `_Reader`, którego używa terminal.
"""
import re
import shlex
from collections import deque

from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from i18n import t
from ssh_terminal import (
    _Reader,
    install_find,
    pending_lines,
    scrollback,
    strip_ansi,
    terminal_font,
)

TAIL_LINES = 200  # ile linii wstecz przy otwarciu, zanim zacznie się "na żywo"


def filter_lines(lines, pattern):
    """Linie pasujące do wzorca (puste = wszystkie). Zła regex = brak filtrowania.

    Czysta funkcja — stąd asercje w `selftest()`.
    """
    if not pattern:
        return lines
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return lines
    return [line for line in lines if rx.search(line)]


class LogTab(QWidget):
    """Zawartość zakładki: pole filtru nad polem tekstowym z wynikiem `tail -f`."""

    def __init__(self, client, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.lines = deque(maxlen=scrollback())
        self._tail = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(t("logtail_filter_placeholder"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._render)
        layout.addWidget(self.filter_edit)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(terminal_font())
        install_find(self.output)
        layout.addWidget(self.output)

        self.channel = None
        self.reader = None
        try:
            _, stdout, _ = client.exec_command(
                f"tail -n {TAIL_LINES} -f {shlex.quote(path)}", timeout=None
            )
        except Exception as error:
            self.output.setPlainText(t("err_prefix", error))
            return
        self.channel = stdout.channel
        self.reader = _Reader(self.channel)
        self.reader.received.connect(self._on_data)
        self.reader.finished_session.connect(self._on_closed)
        self.reader.start()

    def _on_data(self, text):
        complete, self._tail = pending_lines(self._tail, strip_ansi(text))
        if not complete:
            return
        self.lines.extend(complete.split("\n"))
        self._render()

    def _render(self):
        shown = filter_lines(list(self.lines), self.filter_edit.text().strip())
        scrollbar = self.output.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self.output.setPlainText("\n".join(shown))
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _on_closed(self):
        # ponytail: diagnoza bledu ograniczona do kodu wyjscia — pelne
        # wyciagniecie stderr wymagaloby osobnej petli odczytu obok _Reader,
        # a najczestszy przypadek (zly plik) i tak konczy sie szybko i pusto.
        status = 0
        try:
            status = self.channel.recv_exit_status()
        except Exception:
            pass
        if status != 0 and not self.lines:
            self.output.setPlainText(t("err_prefix", f"tail -f (exit {status})"))
            return
        self.lines.append(t("session_closed"))
        self._render()

    def close_session(self):
        if self.channel and not self.channel.closed:
            self.channel.close()
        if self.reader:
            self.reader.wait(2000)


def selftest():
    from PySide6.QtWidgets import QApplication
    import i18n

    QApplication.instance() or QApplication([])
    i18n.use("en")

    assert filter_lines(["abc", "def"], "") == ["abc", "def"]
    assert filter_lines(["error: x", "ok: y"], "error") == ["error: x"]
    assert filter_lines(["a", "b"], "[") == ["a", "b"], "zla regex ma nie filtrowac"
    assert filter_lines(["ABC", "def"], "abc") == ["ABC"], "filtr ignoruje wielkosc liter"
    print("logtail selftest OK")


if __name__ == "__main__":
    selftest()
