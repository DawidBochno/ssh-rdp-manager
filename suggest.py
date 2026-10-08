"""Podpowiadanie poleceń z historii wszystkich sesji (ROADMAP #12).

Historia to polecenia wpisane w dowolnym terminalu SSH, wspólna dla sesji,
w `QSettings["command_history"]` (ostatnie `HISTORY_LIMIT`, bez powtórzeń).

Co trafia do historii — ostrożnie, bo w terminalu pisze się też hasła:
- linia śledzona znak po znaku (`InputTracker`); strzałki, Tab, Ctrl+coś
  w środku = nie wiemy, co jest w linii po stronie powłoki — nic nie zapisujemy,
- tylko jeśli linia jest **widoczna** w terminalu (echo). Hasło przy `sudo`
  czy `passwd` nie ma echa, więc nie przejdzie,
- linia ze spacją na początku pomijana — ten sam zwyczaj co `HISTCONTROL=ignorespace`.

Okienko: lista pod kursorem, gdy wpisany początek pasuje (min. `MIN_PREFIX`
znaki). ↓ wchodzi w listę, ↑/↓ wybiera, Enter/Tab wstawia resztę polecenia
(bez uruchamiania), Esc chowa. Bez wyboru klawisze lecą do powłoki jak zawsze.
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget

import i18n

HISTORY_LIMIT = 500
MIN_PREFIX = 2
MAX_SHOWN = 8


def add_command(history, line):
    """Historia z dopisanym poleceniem na końcu (najnowsze ostatnie), bez powtórzeń."""
    command = line.strip()
    if not command or line.startswith(" "):
        return history
    return ([c for c in history if c != command] + [command])[-HISTORY_LIMIT:]


def suggestions(history, prefix, limit=MAX_SHOWN):
    """Najnowsze polecenia zaczynające się od `prefix` (bez identycznego)."""
    if len(prefix.strip()) < MIN_PREFIX:
        return []
    found = [c for c in reversed(history) if c.startswith(prefix) and c != prefix]
    return found[:limit]


def load_history():
    try:
        found = json.loads(i18n.settings().value("command_history", "[]") or "[]")
    except ValueError:
        return []
    return [c for c in found if isinstance(c, str)] if isinstance(found, list) else []


def record(line):
    i18n.settings().setValue("command_history", json.dumps(add_command(load_history(), line)))


def enabled():
    return i18n.settings().value("suggest_commands", True, type=bool)


class InputTracker:
    """Bieżąca linia wpisywana do powłoki, z tego, co poszło przez `send_text`.

    `line` = None, gdy nie wiadomo, co jest w linii (strzałki, Tab, wklejony
    `\\x1b`…) — aż do Entera albo Ctrl+C.
    """

    def __init__(self):
        self.line = ""

    def feed(self, data):
        """Przetwarza wysłane dane; zwraca listę zakończonych Enterem linii (pewnych)."""
        done = []
        if not isinstance(data, str):
            self.line = None
            return done
        for char in data:
            if char == "\r":
                if self.line is not None:
                    done.append(self.line)
                self.line = ""
            elif char in "\x03\x15":  # Ctrl+C, Ctrl+U = pusta linia
                self.line = ""
            elif self.line is None:
                continue
            elif char in "\x7f\b":
                self.line = self.line[:-1]
            elif char.isprintable():
                self.line += char
            else:
                self.line = None  # sekwencja sterująca — linia po stronie powłoki nieznana
        return done


class SuggestPopup(QListWidget):
    """Lista podpowiedzi — dziecko terminala, bez fokusu (klawisze obsługuje terminal)."""

    def __init__(self, terminal):
        super().__init__(terminal)
        self.terminal = terminal
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.itemClicked.connect(lambda item: self.accept())
        self.hide()

    def refresh(self, prefix):
        """Pokazuje pasujące polecenia albo chowa listę."""
        found = suggestions(load_history(), prefix) if prefix and enabled() else []
        if not found:
            self.hide()
            return
        self.clear()
        self.addItems(found)
        self.setCurrentRow(-1)
        rect = self.terminal.cursorRect()
        row = self.sizeHintForRow(0)
        width = min(max(self.sizeHintForColumn(0) + 12, 200), self.terminal.width() - 20)
        height = row * len(found) + 4
        y = rect.bottom() + 2
        if y + height > self.terminal.viewport().height():
            y = max(0, rect.top() - height - 2)  # brak miejsca pod kursorem — nad nim
        x = min(rect.left(), max(0, self.terminal.width() - width - 20))
        self.setGeometry(x, y, width, height)
        self.show()
        self.raise_()

    def accept(self):
        item = self.currentItem()
        self.hide()
        if item is not None:
            self.terminal.accept_suggestion(item.text())

    def handle_key(self, key):
        """True = klawisz zużyty przez listę, nie idzie do powłoki."""
        if self.isHidden():
            return False
        row = self.currentRow()
        if key == Qt.Key_Escape:
            self.hide()
            return True
        if key == Qt.Key_Down:
            self.setCurrentRow(min(row + 1, self.count() - 1))
            return True
        if key == Qt.Key_Up and row >= 0:
            self.setCurrentRow(row - 1)  # z pierwszej pozycji: wyjście z listy (-1)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab) and row >= 0:
            self.accept()
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Up):
            self.hide()  # bez wyboru Enter i ↑ działają po staremu
        return False


def selftest():
    history = []
    for line in ("ls -la", "systemctl status nginx", " mysql -pTajne", "ls -la", "", "sudo apt update"):
        history = add_command(history, line)
    assert history == ["systemctl status nginx", "ls -la", "sudo apt update"], history
    assert suggestions(history, "s") == [], "jeden znak to za mało"
    assert suggestions(history, "sy") == ["systemctl status nginx"]
    assert suggestions(history, "ls -la") == [], "identyczne nie jest podpowiedzią"
    assert suggestions(["su a", "su b"], "su") == ["su b", "su a"], "najnowsze pierwsze"
    many = []
    for i in range(HISTORY_LIMIT + 5):
        many = add_command(many, f"echo {i}")
    assert len(many) == HISTORY_LIMIT and many[-1] == f"echo {HISTORY_LIMIT + 4}"

    tracker = InputTracker()
    assert tracker.feed("lss") == [] and tracker.feed("\x7f") == [] and tracker.line == "ls"
    assert tracker.feed(" -l\r") == ["ls -l"] and tracker.line == ""
    tracker.feed("cd /va\t")  # Tab = dopełnienie po stronie powłoki — nie wiemy co
    assert tracker.feed("\r") == [], "linia po Tab nie może trafić do historii"
    tracker.feed("abc\x1b[D")  # strzałka w lewo
    assert tracker.line is None
    tracker.feed("\x03")
    assert tracker.feed("pwd\rwhoami\r") == ["pwd", "whoami"], "wklejone kilka linii"
    assert tracker.feed(b"\x1b") == [] and tracker.line is None
    print("suggest selftest OK")


if __name__ == "__main__":
    selftest()
