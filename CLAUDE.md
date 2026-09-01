# Menedżer połączeń SSH/RDP

Aplikacja desktopowa (Python + PySide6) do zarządzania połączeniami SSH i RDP.
Założenie: **najpierw prosty, działający GUI**, warstwa graficzna rozwijana później.

## Uruchomienie

```bash
py main.py            # aplikacja
py main.py --selftest # szybki test logiki, bez okna
```

## ⚠️ Środowisko — przeczytaj przed debugowaniem "nie działa"

Na tej maszynie są **trzy** interpretery Pythona i to już raz kosztowało sesję:

| Komenda | Interpreter | PySide6 |
|---|---|---|
| `py` (i dwuklik na `.py`) | Python **3.13** — domyślny | ✅ jest |
| `py -3.11` | Python 3.11 | ✅ jest |
| `python` na PATH | Python z **Inkscape** | ❌ **brak** |

Wnioski:
- Używaj **`py`**, nigdy gołego `python` — to ostatnie to interpreter Inkscape bez zależności.
- Instalując cokolwiek: `py -m pip install ...` (trafi do 3.13, tego używa dwuklik).

**Objaw "okno otwiera się i natychmiast znika"** = to nie okno aplikacji, tylko konsola.
Skrypt wywalił się na starcie (zwykle `ModuleNotFoundError`), a dwuklik zabiera traceback ze sobą.
**Zawsze uruchamiaj z terminala** — wtedy błąd zostaje na ekranie.

## Architektura

- [`main.py`](main.py) — okno, drzewo połączeń, zakładki, formularz połączenia.
- [`ssh_terminal.py`](ssh_terminal.py) — sesja SSH na Paramiko jako widget zakładki.

```
MainWindow
└── QSplitter (poziomy)
    ├── ConnectionTree (QTreeWidget)  ← grupy i połączenia, menu pod prawym klikiem
    └── QTabWidget                    ← SshTerminal na każde otwarte połączenie
```

- `CONNECTION_TYPE = QTreeWidgetItem.UserType + 1` odróżnia **połączenie** od **grupy**.
  Dwuklik otwiera zakładkę tylko dla elementów tego typu.
- Dane połączenia (`name`/`host`/`port`/`username`) siedzą w `item.setData(0, CONNECTION_DATA, ...)`.
- Zakładki nie duplikują się — ponowne otwarcie przełącza na istniejącą.

### Terminal SSH

`SshTerminal` to `QPlainTextEdit`: wątek `_Reader` czyta z kanału i sygnałem oddaje
tekst do wątku GUI, `keyPressEvent` wysyła klawisze do powłoki.

- **Brak emulacji VT100** — `strip_ansi()` wycina kolory i adresowanie kursora, więc
  `vim`/`htop`/`mc` będą wyglądać źle. Gdy będą potrzebne: `pyte` albo QTermWidget.
- **Hasło pytane przy każdym połączeniu, nigdzie nie zapisywane.** Puste hasło =
  logowanie kluczem (agent lub `~/.ssh`).
- **Nieznany klucz serwera** pokazuje odcisk i pyta o zgodę (`_AskHostKeyPolicy`).
  Nie zamieniaj tego na `AutoAddPolicy` — to jedyna ochrona przed MITM.
- `client.connect()` blokuje GUI do 10 s; przenieść do wątku, gdy zacznie przeszkadzać.

## Pułapki Qt (już nas ugryzły)

- **`QTreeWidgetItem` nie ma `setType()`.** Typ ustawia się wyłącznie w konstruktorze:
  `QTreeWidgetItem(parent, [nazwa], CONNECTION_TYPE)`. Próba `setType()` = `AttributeError`
  przy każdym dodawaniu połączenia.

## Stan i plany

Zrobione: drzewo grup/połączeń, formularz host/port/użytkownik, **działający terminal SSH**
w zakładce, self-testy.

Świadomie pominięte — dodać gdy będzie potrzebne:
- **Persystencja** — drzewo znika po restarcie. Docelowo JSON obok `main.py`.
- **RDP** — niepodpięte (kierunek: FreeRDP osadzony w oknie).
- **Edycja połączenia** — da się dodać i usunąć, nie da się zmienić.
- Emulacja VT100, zmiana rozmiaru PTY przy zmianie rozmiaru okna, ikony.

## Testy

`py main.py --selftest` pokrywa logikę drzewa/zakładek i czyste funkcje terminala.

Test **end-to-end** (klient gada z prawdziwym serwerem SSH postawionym na Paramiko)
powstał w scratchpadzie sesji, nie w repo. Warto go odtworzyć przy zmianach w
`ssh_terminal.py`: stawia serwer na losowym porcie, sprawdza odbiór bannera,
wycięcie ANSI, wysyłkę klawiszy i zamknięcie sesji. Kluczowy szczegół: klient
wysyła **każdy klawisz osobnym pakietem**, więc serwer testowy musi zbierać bajty
w pętli aż do `\r`, a nie robić jednego `recv()`.

## Konwencje

- Interfejs i komentarze po polsku.
- Nietrywialna logika zostawia po sobie asercję w `selftest()` — bez frameworków testowych.
- Preferowane najprostsze rozwiązanie, które działa; bez abstrakcji "na zapas".
