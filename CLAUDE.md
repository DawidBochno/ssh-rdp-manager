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

Łączenie i sesja są rozdzielone:

- `SshConnector` (QThread) — nawiązuje połączenie **w tle**.
- `connect_with_progress()` — pokazuje `QProgressDialog` z licznikiem czasu,
  zwraca gotowy `SshTerminal` albo `None` (anulowano lub błąd, już pokazany).
- `SshTerminal` — dostaje **gotowe** połączenie, sam już się nie łączy.

`_Reader` (QThread) czyta z kanału i sygnałem oddaje tekst do wątku GUI,
`keyPressEvent` wysyła klawisze do powłoki.

- **Brak emulacji VT100** — `strip_ansi()` wycina kolory i adresowanie kursora, więc
  `vim`/`htop`/`mc` będą wyglądać źle. Gdy będą potrzebne: `pyte` albo QTermWidget.
- **Hasło opcjonalnie zapisywane** w `connections.json`, zaszyfrowane **DPAPI**
  (`CryptProtectData` przez `ctypes`, bez nowej zależności). Klucz jest związany
  z kontem Windows — plik skopiowany gdzie indziej jest bezużyteczny.
  Bez zapisanego hasła pytamy jak dotąd; puste = logowanie kluczem (`~/.ssh`, agent).
  Poza Windows checkbox zapisu jest wyłączony.
- **Nieznany klucz serwera** pokazuje odcisk i pyta o zgodę. Pytanie idzie z wątku
  roboczego do GUI sygnałem `BlockingQueuedConnection` — okien Qt nie wolno tworzyć
  poza wątkiem GUI. Nie zamieniaj tego na `AutoAddPolicy`: to ochrona przed MITM.

#### Pułapki, które już nas kosztowały czas

- **Gniazdo tworzymy sami** (`socket.create_connection` + `sock=` do Paramiko),
  żeby `cancel()` mogło je zamknąć i wybić Paramiko z blokującego odczytu.
  Bez tego anulowany wątek mielił do końca limitu, a Qt **wywalało proces**
  przy zamykaniu aplikacji („QThread: Destroyed while thread is still running").
  Dlatego `closeEvent` woła `wait_for_pending()`.
- **`QProgressDialog.cancel()` wywołane z kodu NIE emituje `canceled()`** — sygnał
  leci tylko z kliknięcia w przycisk. W testach klikaj `findChild(QPushButton).click()`,
  inaczej test wisi w nieskończoność.
- Komunikaty Paramiko `WinError 10038` przy anulowaniu są **normalne** — to skutek
  celowego zamknięcia gniazda.

## Pułapki Qt (już nas ugryzły)

- **`QTreeWidgetItem` nie ma `setType()`.** Typ ustawia się wyłącznie w konstruktorze:
  `QTreeWidgetItem(parent, [nazwa], CONNECTION_TYPE)`. Próba `setType()` = `AttributeError`
  przy każdym dodawaniu połączenia.

## Stan i plany

Zrobione: drzewo grup/połączeń, formularz host/port/użytkownik, **terminal SSH**
w zakładce, **zapis do `connections.json`**, **okno postępu z licznikiem czasu
i anulowaniem**, **przeciąganie elementów w drzewie** (`InternalMove`;
`dropEvent` odrzuca upuszczenie na połączenie i poza korzeń, po ruchu zapis),
**zmiana nazwy grupy i edycja połączenia**, **ikony (emoji) na elementach**,
**zapis haseł szyfrowanych DPAPI**, self-testy.

Ikona nie jest osobną kolumną: siedzi w roli `ICON_DATA`, a `set_label()` skleja ją
z nazwą w tekście elementu. Nazwę do zapisu wyciąga `item_name()` — nie czytaj
`item.text(0)` wprost, bo złapiesz emoji.

Świadomie pominięte — dodać gdy będzie potrzebne:
- **RDP** — niepodpięte (kierunek: FreeRDP osadzony w oknie).
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
