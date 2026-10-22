"""Asystent AI w terminalu (Claude): wyjaśnia błąd albo log i proponuje polecenia.

Nic nie idzie do sieci bez kliknięcia „Zapytaj”. Kontekst (zaznaczenie albo ostatnie
linie terminala) widać w oknie i można go poprawić przed wysłaniem — w wyjściu bywają
hasła czy adresy. Proponowane polecenie trafia do terminala przez `send_text()` **bez
Entera**: tryb tylko do odczytu i pytanie na produkcji działają jak przy pisaniu,
a uruchamia zawsze człowiek.

API Claude przez `urllib` (bez nowej zależności); klucz w `QSettings` pod DPAPI,
jak token Telegrama.
"""

import json
import re
import urllib.error
import urllib.request

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import i18n
from credentials import CAN_STORE_PASSWORDS, decrypt_password, encrypt_password
from i18n import t
from ssh_terminal import in_background

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")  # pierwszy = domyślny
CONTEXT_LINES = 60  # tyle ostatnich linii terminala idzie bez zaznaczenia
MAX_TOKENS = 2048
TIMEOUT = 120  # sekundy — dłuższa odpowiedź potrafi się generować kilkadziesiąt sekund
_CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def api_key():
    stored = i18n.settings().value("ai_key", "")
    return (decrypt_password(stored) or "") if stored else ""


def set_api_key(key):
    if key and CAN_STORE_PASSWORDS:
        i18n.settings().setValue("ai_key", encrypt_password(key))
    else:
        i18n.settings().remove("ai_key")  # bez DPAPI nie trzymamy go jawnie


def model():
    name = i18n.settings().value("ai_model", MODELS[0])
    return name if name in MODELS else MODELS[0]


def set_model(name):
    i18n.settings().setValue("ai_model", name)


def context_text(terminal):
    """Zaznaczenie albo ostatnie `CONTEXT_LINES` linii terminala."""
    cursor = terminal.textCursor()
    if cursor.hasSelection():
        return cursor.selectedText().replace("\u2029", "\n")  # Qt oddaje akapity tym znakiem
    return "\n".join(terminal.toPlainText().splitlines()[-CONTEXT_LINES:])


def system_prompt():
    language = i18n.LANGUAGES.get(i18n.language(), "English")
    return (
        "You help a system administrator working in an SSH/Telnet terminal of a Linux or "
        "Windows server (infer which from the output). Explain errors and logs briefly and "
        "concretely. When a command would help, put it in a ```bash or ```powershell block, "
        "one command per line, no prompts, no comments inside the block. Warn clearly before "
        "anything destructive (deleting data, restarts, firewall changes). "
        f"Answer in {language}."
    )


def first_message(context, question):
    return f"Terminal output:\n```\n{context}\n```\n\n{question}"


def ask(key, model_name, messages):
    """Jedno zapytanie do API. Blokuje — tylko przez `in_background`. Zwraca tekst odpowiedzi."""
    body = json.dumps({
        "model": model_name, "max_tokens": MAX_TOKENS,
        "system": system_prompt(), "messages": messages,
    }).encode()
    request = urllib.request.Request(API_URL, data=body, headers={
        "x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:  # zły klucz, brak środków, przeciążenie
        try:
            message = json.loads(error.read())["error"]["message"]
        except Exception:
            message = str(error)
        raise RuntimeError(f"{error.code}: {message}") from None
    return "".join(part.get("text", "") for part in data.get("content", []))


def commands(answer):
    """Czysta funkcja: polecenia z bloków kodu odpowiedzi, linia po linii, bez powtórzeń."""
    found = []
    for block in _CODE_BLOCK.findall(answer):
        for line in block.splitlines():
            line = re.sub(r"^\s*(\$|PS [^>]*>)\s+", "", line).strip()  # zgubiony prompt
            if line and not line.startswith("#") and line not in found:
                found.append(line)
    return found


class AssistantDialog(QDialog):
    """Okno nie blokuje programu — można w tym czasie pisać w terminalu."""

    def __init__(self, parent, terminal, key, model_name):
        super().__init__(parent)
        self.setWindowTitle(t("ai_title"))
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(760, 640)
        self.terminal, self.key, self.model_name = terminal, key, model_name
        self.messages, self.transcript = [], ""

        self.context = QPlainTextEdit(context_text(terminal))
        self.answer = QTextBrowser()
        self.answer.setOpenExternalLinks(True)
        self.answer.setPlaceholderText(t("ai_privacy"))
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(QLabel(t("ai_context")))
        top_layout.addWidget(self.context)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(self.answer)
        splitter.setSizes([200, 400])

        self.question = QLineEdit()
        self.question.setPlaceholderText(t("ai_default_question"))
        self.question.returnPressed.connect(self.send)
        self.ask_button = QPushButton(t("ai_ask"))
        self.ask_button.clicked.connect(self.send)
        row = QHBoxLayout()
        row.addWidget(self.question, 1)
        row.addWidget(self.ask_button)

        self.commands = QListWidget()
        self.commands.setMaximumHeight(110)
        self.commands.itemDoubleClicked.connect(lambda _item: self.insert())
        self.insert_button = QPushButton(t("ai_insert"))
        self.insert_button.setToolTip(t("tip_ai_insert"))
        self.insert_button.clicked.connect(self.insert)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addLayout(row)
        layout.addWidget(QLabel(t("ai_commands")))
        layout.addWidget(self.commands)
        layout.addWidget(self.insert_button)
        self.question.setFocus()

    def send(self):
        question = self.question.text().strip() or t("ai_default_question")
        # Pierwsze pytanie niesie kontekst z terminala, kolejne to ciąg tej samej rozmowy.
        content = question if self.messages else first_message(self.context.toPlainText(), question)
        messages = self.messages + [{"role": "user", "content": content}]
        try:
            answer = in_background(self, lambda: ask(self.key, self.model_name, messages))
        except Exception as error:  # brak sieci, zły klucz — rozmowa zostaje jak była
            self.answer.setMarkdown(self.transcript + t("ai_error", error))
            return
        self.messages = messages + [{"role": "assistant", "content": answer}]
        self.context.setReadOnly(True)  # już wysłany — zmiana niczego by nie zmieniła
        self.transcript += f"**{question}**\n\n{answer}\n\n---\n\n"
        self.answer.setMarkdown(self.transcript)
        self.answer.verticalScrollBar().setValue(self.answer.verticalScrollBar().maximum())
        self.commands.clear()
        self.commands.addItems(commands(answer))
        if self.commands.count():
            self.commands.setCurrentRow(0)
        self.question.clear()

    def insert(self):
        item = self.commands.currentItem()
        if item is not None:
            self.terminal.send_text(item.text())  # bez "\r" — Enter naciska człowiek
            self.terminal.setFocus()


def selftest():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from ssh_terminal import OfflineTerminal

    answer_text = "Brak miejsca.\n```bash\n$ df -h\ndu -sh /var/log/*\n# komentarz\ndf -h\n```\nKoniec."
    assert commands(answer_text) == ["df -h", "du -sh /var/log/*"], commands(answer_text)
    assert commands("```powershell\nPS C:\\> Get-Service\n```") == ["Get-Service"]
    assert commands("bez bloków kodu") == []

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.headers["x-api-key"], self.headers["anthropic-version"], body))
            if self.headers["x-api-key"] == "zly":
                reply, code = {"type": "error", "error": {"message": "invalid x-api-key"}}, 401
            else:
                reply, code = {"content": [{"type": "text", "text": answer_text}]}, 200
            data = json.dumps(reply).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    global API_URL
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real_url, API_URL = API_URL, f"http://127.0.0.1:{server.server_address[1]}/v1/messages"
    terminal = OfflineTerminal()
    try:
        assert ask("dobry", MODELS[0], [{"role": "user", "content": "x"}]) == answer_text
        key, version, body = seen[-1]
        assert (key, version, body["model"]) == ("dobry", API_VERSION, MODELS[0])
        assert body["system"] and body["max_tokens"] == MAX_TOKENS
        try:
            ask("zly", MODELS[0], [])
            raise AssertionError("błąd API przeszedł bez wyjątku")
        except RuntimeError as error:
            assert "401" in str(error) and "invalid x-api-key" in str(error), error

        # Okno: kontekst = ostatnie linie terminala, pierwsze pytanie go niesie, drugie już nie.
        terminal.setPlainText("\n".join(f"linia {n}" for n in range(100)) + "\nNo space left on device")
        dialog = AssistantDialog(None, terminal, "dobry", MODELS[0])
        sent_context = dialog.context.toPlainText()
        assert sent_context.endswith("No space left on device") and "linia 40" not in sent_context
        assert len(sent_context.splitlines()) == CONTEXT_LINES
        dialog.send()
        first = seen[-1][2]["messages"]
        assert len(first) == 1 and "No space left on device" in first[0]["content"]
        assert first[0]["content"].endswith(t("ai_default_question")), "puste pole = pytanie domyślne"
        assert dialog.commands.count() == 2 and dialog.context.isReadOnly()
        dialog.question.setText("a co z /tmp?")
        dialog.send()
        second = seen[-1][2]["messages"]
        assert [m["role"] for m in second] == ["user", "assistant", "user"], second
        assert second[-1]["content"] == "a co z /tmp?", "kolejne pytanie bez ponownego kontekstu"

        # Wstawienie polecenia: przez send_text, bez Entera.
        terminal.channel.sent.clear()
        dialog.commands.setCurrentRow(1)
        dialog.insert()
        typed = b"".join(d if isinstance(d, bytes) else d.encode() for d in terminal.channel.sent)
        assert typed == b"du -sh /var/log/*", typed
        terminal.read_only = True
        terminal.channel.sent.clear()
        dialog.insert()
        assert not terminal.channel.sent, "tryb tylko do odczytu omija asystenta"
        terminal.read_only = False

        # Błąd sieci nie psuje rozmowy.
        dialog.key = "zly"
        dialog.send()
        assert len(dialog.messages) == 4 and "401" in dialog.answer.toPlainText()
        dialog.close()

        # Zaznaczenie wygrywa z ostatnimi liniami.
        cursor = terminal.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(7, cursor.MoveMode.KeepAnchor)
        terminal.setTextCursor(cursor)
        assert context_text(terminal) == "linia 0"
    finally:
        API_URL = real_url
        server.shutdown()
        server.server_close()
        terminal.deleteLater()
    print("assistant selftest OK")
