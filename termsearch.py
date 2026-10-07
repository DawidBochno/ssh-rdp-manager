"""Szukanie w treści wszystkich otwartych terminali naraz (ROADMAP #41).

Przeszukiwany jest bufor terminala (scrollback), czyli to, co widać po
przewinięciu — nie ekran programów pełnoekranowych (htop, vim), bo ten idzie
osobno (`AltScreenView`). Okno nie jest modalne: wynik przenosi do zakładki
i zaznacza trafienie, a okno zostaje do kolejnych skoków.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from i18n import t

MIN_QUERY = 2
MAX_RESULTS = 500


def search_lines(texts, query, limit=MAX_RESULTS):
    """[(nr źródła, nr linii, linia)] z linii zawierających `query` (bez wielkości liter)."""
    needle = query.lower()
    if len(needle.strip()) < MIN_QUERY:
        return []
    found = []
    for source, text in enumerate(texts):
        for number, line in enumerate(text.split("\n")):
            if needle in line.lower():
                found.append((source, number, line))
                if len(found) >= limit:
                    return found
    return found


def select_hit(terminal, line_number, query):
    """Zaznacza trafienie w terminalu i przewija do niego."""
    block = terminal.document().findBlockByNumber(line_number)
    if not block.isValid():
        return False
    column = max(0, block.text().lower().find(query.lower()))
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + column)
    cursor.setPosition(block.position() + column + len(query), QTextCursor.KeepAnchor)
    terminal.setTextCursor(cursor)
    terminal.centerCursor()
    return True


class TerminalSearch(QDialog):
    """`sessions()` -> [(nazwa, terminal)], `show_session(terminal)` przełącza zakładkę."""

    def __init__(self, parent, sessions, show_session):
        super().__init__(parent)
        self.setWindowTitle(t("termsearch_title"))
        self.resize(720, 420)
        self.sessions, self.show_session = sessions, show_session
        self._found = []  # [(terminal, nr linii)] w kolejności listy
        self.query = QLineEdit()
        self.query.setPlaceholderText(t("termsearch_hint"))
        self.query.setClearButtonEnabled(True)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._open)
        self.summary = QLabel()
        layout = QVBoxLayout(self)
        layout.addWidget(self.query)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.summary)
        # Po krótkiej przerwie w pisaniu, nie po każdym znaku — bufory bywają duże.
        self._timer = QTimer(self, singleShot=True, interval=250, timeout=self.search)
        self.query.textChanged.connect(lambda: self._timer.start())

    def search(self):
        query = self.query.text()
        open_now = self.sessions()
        hits = search_lines([terminal.toPlainText() for _, terminal in open_now], query)
        self.results.clear()
        self._found = []
        for source, number, line in hits:
            name, terminal = open_now[source]
            item = QListWidgetItem(f"{name}  ›  {line.strip()}")
            item.setToolTip(line)
            self.results.addItem(item)
            self._found.append((terminal, number))
        limit = t("termsearch_limit", MAX_RESULTS) if len(hits) >= MAX_RESULTS else ""
        self.summary.setText(t("termsearch_count", len(hits)) + limit if query else "")

    def _open(self, item):
        terminal, number = self._found[self.results.row(item)]
        self.show_session(terminal)
        select_hit(terminal, number, self.query.text())


def selftest():
    from PySide6.QtWidgets import QApplication, QPlainTextEdit

    import i18n

    app = QApplication.instance() or QApplication([])
    i18n.use("en")
    texts = ["$ uptime\nload average: 0.1\n$ systemctl status nginx", "nginx: OK\nERROR Nginx down"]
    assert search_lines(texts, "n") == [], "jeden znak to za mało"
    assert search_lines(texts, "nginx") == [(0, 2, "$ systemctl status nginx"), (1, 0, "nginx: OK"),
                                            (1, 1, "ERROR Nginx down")]
    assert len(search_lines(texts, "nginx", limit=2)) == 2

    first, second = QPlainTextEdit(texts[0]), QPlainTextEdit(texts[1])
    shown = []
    dialog = TerminalSearch(None, lambda: [("web", first), ("db", second)], shown.append)
    dialog.query.setText("NGINX")
    dialog.search()
    assert dialog.results.count() == 3 and dialog.results.item(1).text().startswith("db")
    dialog._open(dialog.results.item(2))
    assert shown == [second] and second.textCursor().selectedText() == "Nginx", \
        second.textCursor().selectedText()
    assert not select_hit(first, 99, "x"), "linia spoza bufora (scrollback ją uciął)"
    dialog.deleteLater()
    del app
    print("termsearch selftest OK")


if __name__ == "__main__":
    selftest()
