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
- [`ssh_terminal.py`](ssh_terminal.py) — sesja SSH na Paramiko jako widget zakładki
  plus odpytywanie statystyk serwera.
- [`i18n.py`](i18n.py) — napisy interfejsu po angielsku i po polsku.

```
MainWindow
└── QSplitter (poziomy)
    ├── ConnectionTree (QTreeWidget)  ← grupy i połączenia, menu pod prawym klikiem
    └── QTabWidget                    ← "🏠 Home" | SessionTab... | "+" (zawsze pierwsza/ostatnia)
        └── SessionTab (na każde otwarte połączenie)
            └── QSplitter (poziomy): SftpPanel | SshTerminal
statusBar()                           ← statystyki serwera z aktywnej zakładki
```

- `CONNECTION_TYPE = QTreeWidgetItem.UserType + 1` odróżnia **połączenie** od **grupy**.
  Dwuklik otwiera zakładkę tylko dla elementów tego typu.
- Dane połączenia (`name`/`host`/`port`/`username`) siedzą w `item.setData(0, CONNECTION_DATA, ...)`.
- Zakładki nie duplikują się — ponowne otwarcie przełącza na istniejącą.

#### Język interfejsu (`i18n.py`)

Cały interfejs jest po **angielsku** (domyślnie) albo po **polsku**; wybór siedzi
w Widok → „Language". Zwykły słownik `TEXTS[kod][klucz]` i funkcja `t(klucz, *args)`
zamiast `gettext`/`QTranslator` — te wymagają kompilowania `.mo`/`.qm` przy każdej
zmianie napisu, co przy dwóch językach jest kosztem bez zysku.

- Wybór zapisuje się w `QSettings("Bochnovic", "SSH-RDP-Manager")`, czyli w rejestrze
  Windows — bez kolejnego pliku do pilnowania. `i18n.load()` woła się w `main()`
  **przed** zbudowaniem okna.
- Zmiana działa **po restarcie**: napisy czyta się raz, przy budowaniu widgetów,
  a przebudowa całego okna zabiłaby otwarte sesje SSH.
- `t()` wywołuj **w miejscu użycia**, nie na poziomie modułu — inaczej napis
  zamarznie w języku z chwili importu. Dlatego etykiety w `SCRIPTS` (ssh_terminal)
  i `SERVERS` (servers) trzymają **klucze** tłumaczeń, a menu tłumaczy je przy
  budowaniu.
- `selftest()` sprawdza, że oba słowniki mają **ten sam zbiór kluczy** — inaczej
  jeden język po cichu gubiłby napisy. Testy w `main.py`/`ssh_terminal.py` wołają
  `i18n.use("en")` na starcie, żeby asercje na napisach nie zależały od ustawienia
  użytkownika.
- Polskie słowa w `HIGHLIGHT_RULES` („błąd", „ostrzeżenie") **zostają** — to reguły
  na wyjście *zdalnego serwera*, nie na nasz interfejs.
- Separator dziesiętny (`human_bytes`) idzie z języka: „1.5 kB" / „1,5 kB".

#### Zakładka „Home” i „+” (pulpit startowy, wzorem MobaXterm)

`self.tabs` ma dwie **stałe** zakładki, dodane raz w `MainWindow.__init__`, obie bez
przycisku zamknięcia (`tabBar().setTabButton(i, QTabBar.RightSide, None)`):

- **„🏠 Home”** — zawsze indeks `0`. Prosty `HomeTab` z dwoma przyciskami:
  nowe połączenie tymczasowe / nowe zapisane.
- **„+”** — zawsze **ostatnia** zakładka. To nie jest przycisk w rogu widgetu
  (`setCornerWidget`) — taki ląduje na krańcu całego okna, nie zaraz za ostatnią
  kartą. Zamiast tego to zwykła (pusta) zakładka, a `tabBarClicked` (nie
  `currentChanged` — musi złapać *każdy* klik, nawet gdy indeks się nie zmienia)
  woła `_on_tab_bar_clicked()`: jeśli kliknięto „+”, **cofa** `currentIndex` na
  poprzednią zakładkę i dopiero wtedy otwiera dialog — „+” nigdy nie staje się
  aktywną, prawdziwą zakładką.
- Nowe sesje wchodzą przez `insertTab(tabs.count() - 1, ...)`, czyli **przed**
  „+” — dzięki temu „+” zawsze zostaje na końcu paska.
- Tymczasowe połączenie (`_quick_connect`) używa `ConnectionDialog` z ukrytym
  checkboxem zapisu hasła — nic nie trafia do drzewa ani do `connections.json`,
  sesja znika bez śladu po zamknięciu karty.

#### Graficzny SFTP po lewej stronie zakładki

`SessionTab` (w `ssh_terminal.py`) to widget faktycznie wkładany do `QTabWidget`:
`QSplitter` z `SftpPanel` po lewej i `SshTerminal` po prawej — jak w MobaXterm.
`SftpPanel` otwiera **osobny kanał** (`paramiko.SFTPClient.from_transport(...)`)
na tym samym połączeniu, więc nie koliduje z powłoką ani z `_StatsPoller`.

- Pasek ścieżki + przyciski: ◀ ▶ (historia jak w przeglądarce — `_back`/`_forward`,
  nowa ścieżka kasuje gałąź „do przodu"), ▲ (do góry), 🔄 (odśwież), 📁+ (nowy folder),
  📤 (wyślij). Dwuklik na folder = wejście, na plik = pobranie
  (`QFileDialog.getSaveFileName`); menu pod prawym klawiszem = pobierz/usuń.
- Gdy serwer nie daje SFTP (stary/ograniczony OpenSSH), `SftpPanel.sftp` zostaje
  `None`, a panel się **wyłącza** (lista nieaktywna, komunikat) zamiast wywalić
  aplikację — testowane atrapą klienta bez transportu w `selftest()`.
- `SessionTab.last_stats` to `@property` przekazujące do `self.terminal.last_stats`
  — dzięki temu `MainWindow._show_current_stats()` nie musi wiedzieć, że pod
  zakładką siedzi teraz splitter, a nie sam terminal.
- ponytail: `listdir`/`get`/`put` wołane wprost na wątku GUI — dla admina po
  LAN/VPN to milisekundy. Przy wolnych/dużych transferach przenieść na `QThread`
  jak `_StatsPoller`.

### Terminal SSH

Łączenie i sesja są rozdzielone:

- `SshConnector` (QThread) — nawiązuje połączenie **w tle**.
- `connect_with_progress()` — pokazuje `QProgressDialog` z licznikiem czasu,
  zwraca gotowy `SshTerminal` albo `None` (anulowano lub błąd, już pokazany).
- `SshTerminal` — dostaje **gotowe** połączenie, sam już się nie łączy.

`_Reader` (QThread) czyta z kanału i sygnałem oddaje tekst do wątku GUI,
`keyPressEvent` wysyła klawisze do powłoki.

- **Podświetlanie składni** (wzorem MobaXterm): `TerminalHighlighter`
  (`QSyntaxHighlighter` na dokumencie terminala) koloruje po **naszej** stronie,
  więc działa też, gdy zdalny serwer nie wysyła kolorów. Reguły to lista
  `HIGHLIGHT_RULES` (regex, kolor, pogrubienie) — błędy, ostrzeżenia, sukcesy,
  IP, URL, ścieżki, daty, `user@host`. `highlight_spans()` jest czystą funkcją
  (stąd asercje w `selftest()`), a **pierwsza pasująca reguła wygrywa** —
  dlatego „error" w ścieżce zostaje czerwone. Przełącznik: Widok →
  „Podświetlanie składni" (atrybut klasy, więc łapie też nowe zakładki).
- **Backspace i przerysowanie linii**: powłoka kasuje znak sekwencją ` `,
  a prompt odświeża przez `
`. `output_ops()` tłumaczy oba znaki na operacje
  (`bs`/`cr`/`text`), `apply_output()` wykonuje je na kursorze dokumentu.
  Wcześniej wstawialiśmy je dosłownie i backspace **dorysowywał kwadracik**
  zamiast kasować. `strip_ansi()` celowo **nie** usuwa już samotnego `
`.
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

#### Szukanie w tekście (Ctrl+F)

`FindBar` + `install_find(editor)` w `ssh_terminal.py` doczepiają szukanie do
**dowolnego** `QPlainTextEdit`: terminal sesji i okno z wynikiem skryptu.

- Pasek jest **dzieckiem** pola tekstowego (pływa nad nim jak w przeglądarce),
  więc nie trzeba przebudowywać układu zakładki. Pozycję poprawia `eventFilter`
  na zdarzeniu `Resize` pola.
- Enter = następne, Shift+Enter = poprzednie, Esc chowa, brak trafienia
  podświetla pole na czerwono; szukanie **zawija** na drugi koniec dokumentu.
- W terminalu `keyPressEvent` przechwytuje Ctrl+F **przed** wysłaniem do
  powłoki — inaczej poleciałoby tam ``.
- Menu pod prawym klawiszem to `createStandardContextMenu()` (kopiuj/zaznacz
  wszystko) plus pozycja „Znajdź…"; stąd `Qt.CustomContextMenu` na polu.

#### Statystyki serwera (dolny pasek)

`_StatsPoller` (QThread) co `STATS_INTERVAL` sekund puszcza **jedno** polecenie
osobnym kanałem (`exec_command`) — powłoka w zakładce tego nie widzi.
Sekcje wyjścia rozdzielają linie `@UP`, `@CPU`, … (`_sections()`), a
`format_stats()` składa z nich jeden tekst na pasek.

**Dwa warianty, jeden pasek** — przy pierwszym odpytaniu poller próbuje obu
poleceń i zapamiętuje to, które odpowiedziało:

- **Linux**: `STATS_CMD` czyta wprost `/proc` (+ `df -P /`, `who`), więc nie
  zależymy od `top`/`vmstat`. CPU liczymy z różnicy liczników.
- **Windows Server (OpenSSH)**: `WINDOWS_STATS_CMD` to jedna linijka PowerShella
  (CIM + `Get-NetAdapterStatistics` + `quser`). Skrypt **nie może zawierać
  cudzysłowów** — leci jako jeden argument w cudzysłowie, bo domyślną powłoką
  OpenSSH bywa `cmd.exe` (inaczej zjadłby `|` i `>`). Liczby rzutujemy na
  całkowite: `[string]` na ułamku dałby przecinek dziesiętny na polskim Windows.
  CPU przychodzi gotowe (`cpu_pct`), więc widać je od pierwszej próbki;
  liczniki sieci są opcjonalne — bez nich pasek pokazuje „—", nie zera.

- **Tempo sieci (i CPU na Linuksie) wymaga dwóch próbek** — pierwsze odświeżenie
  pokazuje „—". Odstęp czasu bierzemy z uptime'u z tej samej próbki, nie z
  zegara lokalnego.
- Gdy **żaden** wariant się nie rozebrał, pasek pisze „Statystyki niedostępne"
  i **pętla się kończy**, żeby nie odpytywać w kółko.
- Pasek pokazuje wyłącznie aktywną zakładkę; każdy terminal trzyma ostatni tekst
  w `last_stats`, `MainWindow._show_current_stats()` przywraca go po przełączeniu.
- `close_session()` najpierw ustawia stop, potem zamyka klienta (to wybija wątek
  z blokującego odczytu), a `wait()` jest na końcu — inaczej Qt wywala proces.

#### Serwery wbudowane (menu „Serwery")

[`servers.py`](servers.py) — daemony po *naszej* stronie (wzorem „Embedded
servers" z MobaXterm): zdalny serwer pobiera plik od nas, zamiast stawiać
cokolwiek u siebie. Bez nowych zależności.

- `HttpShare` — `http.server` ze stdlib, katalog tylko do odczytu.
- `TftpShare` — RFC 1350 na gołym `socket` (odczyt i zapis, tryb octet); transfer
  leci z **nowego portu**, tak działa TFTP. `_safe_path()` blokuje `../`.
  Port 69 wymaga uprawnień administratora, więc domyślnie proponujemy 6969.
- Menu działa jak przełącznik (`QAction` checkable): drugi klik zatrzymuje.
  `MainWindow._servers` trzyma uruchomione, `closeEvent` je zamyka.
- `selftest()` w `servers.py` robi **prawdziwy** transfer po pętli lokalnej
  (HTTP `urlopen`, TFTP ręcznie sklecony RRQ + ACK) — plik 1500 B, czyli
  ponad dwa bloki, żeby złapać błąd numerowania.

#### Skrypty administracyjne (menu „Skrypty”)

`SCRIPTS` w `ssh_terminal.py` — lista gotowych poleceń (top procesów, miejsce na
dysku, błędy w logach, porty, restart usługi, aktualizacje, nieudane logowania,
kto zalogowany, ping, aktywne połączenia). `run_script()` odpala je na klientie
**aktywnej zakładki** osobnym kanałem (`exec_command`, jak `_StatsPoller`) i
pokazuje wynik w oknie dialogowym z `QPlainTextEdit`.

- Skrypty z `{0}` (restart usługi, ping) najpierw pytają o parametr
  (`QInputDialog`) — **`.format()` woła się tylko wtedy**, bo część poleceń
  PowerShell ma dosłowne `{` (np. `@{LogName=...}`), które `.format()` inaczej
  próbowałby rozebrać jako pole.
- **Wariant Linux, potem Windows** — `_run_commands()` próbuje najpierw Linux;
  gdy `exec_command` zwróci niezerowy kod bez wyjścia (obcy shell), próbuje
  wariantu Windows. Ten sam wzorzec „spróbuj obu” co przy statystykach.
- Menu „Skrypty” w `MainWindow` jest zawsze widoczne, ale bez otwartej
  zakładki SSH pokazuje komunikat zamiast się wywalić (`_run_script`).

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
**zapis haseł szyfrowanych DPAPI**, **statystyki serwera na dolnym pasku**
(CPU, RAM, dysk, ruch sieciowy, uptime, liczba zalogowanych; Linux i Windows),
**pasek menu u góry i pasek boczny po lewej** (wzorem MobaXterm), **wybór języka
(angielski/polski)**, **11 gotowych
skryptów administracyjnych** (menu „Skrypty”, Linux i Windows), **zakładka
„Home” i „+” (tymczasowe połączenia, wzorem MobaXterm)**, **graficzny SFTP po
lewej w każdej sesji** (`SftpPanel`), self-testy.

Grupy dostają domyślną ikonę `GROUP_ICON` („📁") przy tworzeniu i przy wczytywaniu
starych wpisów bez ikony. Kolor (menu „Kolor…") siedzi w roli `COLOR_DATA`, zapisuje
się jako `"color"` i `set_color()` rozprowadza go **rekurencyjnie** na wszystkie
elementy w grupie — dziecko nie trzyma własnego koloru, tylko odziedziczony.

Eksport/import (menu „Połączenie") to ten sam format co `connections.json` —
`nodes()` zwraca listę słowników dla obu ścieżek, `import_from(path, replace)`
albo zastępuje drzewo, albo dopisuje. Hasła wędrują jako blob DPAPI, więc
na innym koncie/komputerze się nie odszyfrują (plik i tak jest użyteczny).

Ikona nie jest osobną kolumną: siedzi w roli `ICON_DATA`, a `set_label()` skleja ją
z nazwą w tekście elementu. Nazwę do zapisu wyciąga `item_name()` — nie czytaj
`item.text(0)` wprost, bo złapiesz emoji.

Świadomie pominięte — dodać gdy będzie potrzebne:
- **RDP** — niepodpięte (kierunek: FreeRDP osadzony w oknie). To osobna, większa
  funkcja niż SSH/SFTP: inny protokół, inna zależność (FreeRDP), nie da się
  dołożyć do `paramiko.Transport` jak SFTP.
- Emulacja VT100, zmiana rozmiaru PTY przy zmianie rozmiaru okna, ikony.
- Transfer plików w tle (SFTP na wątku GUI) i przeciąganie plików myszką
  (na razie tylko przyciski/menu) — patrz ponytail-komentarz przy `SftpPanel`.

## Praca z gitem

**Nowe zadanie = nowa gałąź.** Zanim zaczniesz zmieniać pliki pod nową prośbę,
załóż gałąź od `main` z nazwą opisującą temat (`git switch -c wyszukiwanie-home`).
Nie pracuj bezpośrednio na `main` — tam trafiają tylko scalenia.

**Zmiana tematu = pytanie do użytkownika.** Gdy nowa prośba nie ma związku z tym,
co właśnie leży niezacommitowane w katalogu roboczym, **zapytaj przed pisaniem
kodu**: skomitować i zacząć nową gałąź, czy najpierw dokończyć bieżącą rzecz?
Nie mieszaj dwóch tematów w jednym commicie i nie decyduj o tym sam.

Reszta jak dotąd: commit i push wyłącznie na wyraźną prośbę, komunikaty commitów
po polsku (bez polskich znaków — konsola Windows je zjada), tryb rozkazujący.

## Testy

`py main.py --selftest` pokrywa logikę drzewa/zakładek i czyste funkcje terminala.

Test **end-to-end** (klient gada z prawdziwym serwerem SSH postawionym na Paramiko)
powstał w scratchpadzie sesji, nie w repo. Warto go odtworzyć przy zmianach w
`ssh_terminal.py`: stawia serwer na losowym porcie, sprawdza odbiór bannera,
wycięcie ANSI, wysyłkę klawiszy, odpowiedź na `STATS_CMD` (pasek statystyk)
i zamknięcie sesji. Serwer testowy musi odpowiadać na `exec` w osobnym wątku
i nie zamykać kanału od razu — inaczej Paramiko dostaje „Channel closed". Kluczowy szczegół: klient
wysyła **każdy klawisz osobnym pakietem**, więc serwer testowy musi zbierać bajty
w pętli aż do `\r`, a nie robić jednego `recv()`.

## Konwencje

- Komentarze, docstringi i komunikaty commitów po polsku.
- **Interfejs przez `i18n.t()`** — żadnych napisów wprost w widgetach. Nowy napis =
  wpis w obu słownikach `TEXTS` (angielski jest domyślny).
- Nietrywialna logika zostawia po sobie asercję w `selftest()` — bez frameworków testowych.
- Preferowane najprostsze rozwiązanie, które działa; bez abstrakcji "na zapas".
