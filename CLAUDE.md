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
- [`rdp.py`](rdp.py) — sesja RDP na kontrolce ActiveX Microsoftu jako widget zakładki.
- [`update.py`](update.py) — sprawdzanie, czy kopia nadąża za gałęzią na GitHubie.
- [`scanner.py`](scanner.py) — skaner sieci (menu „Programy”) i okno z wynikami.

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

#### RDP w zakładce (`rdp.py`)

Kontrolka ActiveX `MsTscAx` — ta sama, na której stoi `mstsc.exe` — osadzona
w `QAxWidget`. **Bez nowej zależności**: `QtAxContainer` jest w PySide6.
FreeRDP (wcześniejszy pomysł) odpada — wymagałby dowożenia natywnych binariów.

Cztery pułapki, każda kosztowała próbę:

- **`setProperty()` na kontrolce głównej NIE dochodzi do COM.** Metaobiekt Qt nie
  dostaje właściwości ActiveX (`methodCount` pokazuje same składowe `QWidget`),
  więc `setProperty` ląduje w dynamicznej właściwości Qt i **cicho nic nie robi**.
  Do kontrolki głównej idzie `dynamicCall("SetX(...)")`.
- **Argument musi iść w liście.** `dynamicCall("SetServer(QString)", "host")` ustawia
  `"h"` — sam pierwszy znak. Poprawnie: `dynamicCall("SetServer(QString)", ["host"])`.
- **Na podobiektach (`AdvancedSettings9`) `setProperty()` działa normalnie** i czyta
  prawdziwe wartości COM. Dlatego port i hasło ustawiamy właśnie tam.
- **ProgID `.13` nie wstaje** mimo wpisu w rejestrze, `.11` wstaje — stąd lista
  `RDP_PROGIDS` i próbowanie po kolei (ten sam wzorzec „spróbuj obu", co przy
  statystykach i skryptach).

Kontrolka **nie wystawia zdarzeń** w metaobiekcie, więc rozłączenie wykrywamy
odpytywaniem `Connected` (0/1/2) `QTimer`-em raz na sekundę — patrz komentarz
`ponytail:` przy `POLL_MS`. `RdpTab.last_stats` udaje interfejs `SessionTab`,
dzięki czemu `MainWindow` nie musi wiedzieć, który protokół siedzi w zakładce;
po rozłączeniu wraca tam powód zamiast statystyk.

Gdy kontrolka nie wstanie, `open_rdp()` zapisuje plik `.rdp` i odpala
`mstsc.exe` w osobnym oknie. Hasła w `.rdp` **nie ma celowo** — idzie tam jako
blob DPAPI, nie tekstem, więc `mstsc` i tak zapyta.

#### Skaner sieci (`scanner.py`, menu „Programy”)

Odpowiednik Advanced IP Scanner: zakres adresów -> lista żywych hostów z nazwą,
MAC-iem i wykrytymi usługami, a z wiersza otwiera się sesja SSH albo RDP
(dwuklik: SSH gdy port 22 otwarty, inaczej RDP). Bez nowych zależności —
systemowy `ping`, `arp -a`, `socket` i odwrotny DNS.

- **Dwie rundy, nie jedna.** Ping łapie większość, ale Windows z włączoną zaporą
  na ICMP nie odpowiada. Po skanowaniu czytamy tablicę ARP (pingi zdążyły ją
  wypełnić) i hosty, które są w ARP a nie odpowiedziały, dostają drugą rundę
  z samym sprawdzeniem portów. Bez tego znikałaby połowa serwerów Windows.
- **Windowsowy `ping` zwraca 0 także przy „host nieosiągalny”** — dlatego
  `alive()` sprawdza dodatkowo `TTL=` w wyjściu, nie sam kod wyjścia.
- Równoległość to `ThreadPoolExecutor(WORKERS)` w środku `QThread`: całość jest
  czekaniem na sieć, więc wątki ze stdliba wystarczają (254 adresy ~9 s).
- `parse_range()` (czysta funkcja, stąd asercje) rozumie `192.168.0.1-100`,
  `192.168.0.1-192.168.0.2`, `/24` i listy po przecinku; `MAX_HOSTS` odbija
  literówkę w rodzaju `10.0.0.0/8`, zanim ta zamieni program w skaner internetu.
- Menu pod prawym klawiszem ma podmenu **„Kopiuj”** zbudowane z `COLUMNS`
  (wszystko / nazwa / IP / MAC / usługi) — nowa kolumna dopisuje się do niego sama.
- **Menu „Programy” to lista `TOOLS`** w `main.py` (klucz napisu -> metoda okna).
  Kolejny dodatek narzędziowy to jeden wiersz, nie nowe menu.

#### Aktualizacja z GitHuba (`update.py`)

Program mieszka w kopii roboczej gita, więc „wersją" jest identyfikator commitu —
bez własnego pliku `VERSION` i porównywania numerów. `main()` po pokazaniu okna
woła `check_updates()`: `UpdateCheck` (QThread) porównuje `git rev-parse HEAD`
z `HEAD` gałęzi `main` na GitHubie i przy różnicy pyta, czy pobrać.
Pobranie to `git pull --ff-only`, zmiany wchodzą po restarcie.

- Zdalny commit czytamy **jednym żądaniem** do API GitHuba z nagłówkiem
  `Accept: application/vnd.github.sha` — odpowiedź to sam identyfikator, nie JSON
  z całym commitem. Stdlib `urllib`, żadnej nowej zależności.
- Wątek startuje **z `main()`, nie z `MainWindow.__init__`** — inaczej
  `--selftest` chodziłby po sieci przy każdym uruchomieniu testów.
- `subprocess` dostaje `CREATE_NO_WINDOW`, inaczej każde wywołanie gita
  mignęłoby czarnym oknem konsoli.
- Gdy nie ma gita, sieci albo to nie jest kopia z repozytorium — cisza.
  Brak aktualizacji nie jest błędem, którym warto zawracać głowę przy starcie.
- **Poza gałęzią `main` pytania nie ma** (`current_branch()`): własna gałąź jest
  inna z założenia, więc porównanie z `main` zawsze wołałoby „nieaktualne".
- `git pull` dostaje **wprost `origin main`** — własna gałąź nie musi mieć
  ustawionego śledzenia, a wtedy samo `git pull` odmawia („no tracking
  information").

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
- `listdir` leci wprost na wątku GUI (dla admina po LAN/VPN to milisekundy),
  ale **`get`/`put` idą przez `_Transfer` (QThread)** z paskiem postępu —
  duży plik nie zamraża okna. `run_transfer()` blokuje `QEventLoop` na czas
  transferu, więc jednocześnie leci tylko jeden (Paramiko i tak nie lubi
  współbieżności na jednym `SFTPClient`). Bez przycisku anulowania: przerwanie
  w pół pliku zostawiłoby obcięty plik po drugiej stronie — patrz komentarz
  `ponytail:` przy `run_transfer()`.

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
- **Rozmiar PTY idzie za oknem**: `resizeEvent` przelicza piksele na kolumny
  i wiersze (`terminal_size()`, czysta funkcja — stąd asercje) i woła
  `channel.resize_pty()`. Bez tego powłoka trzymała się zaszytego w
  `invoke_shell()` `100 x 30` i łamała linie w złym miejscu. Porównanie
  z poprzednim rozmiarem jest po to, żeby każdy piksel przeciągnięcia
  nie szedł po sieci. `_sync_pty_size()` czyta kanał przez `getattr` —
  Qt potrafi wysłać `resizeEvent` **przed** przypisaniem `self.channel`.
- **Wklejanie**: `keyPressEvent` łapie `QKeySequence.Paste` i Ctrl+Shift+V.
  Wcześniej Ctrl+V szedł do powłoki jako `^V`, bo `key_to_bytes()` zamienia
  Ctrl+litera na znak sterujący. `paste_bytes()` zamienia `\n` na `\r` —
  inaczej wieloliniowy wklej wykonywał się tylko do pierwszego Entera.
- **Klucz prywatny per połączenie** (`key_file`) leci do `client.connect()`
  jako `key_filename`. Bez niego zostaje jak dotąd agent i `~/.ssh`.
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
(angielski/polski)**, **RDP w zakładce (kontrolka ActiveX, bez nowych
zależności)**, **rozmiar PTY za oknem, wklejanie, klucz prywatny per
połączenie, transfery SFTP w tle i zapamiętywanie układu okna**, **11 gotowych
skryptów administracyjnych** (menu „Skrypty”, Linux i Windows), **zakładka
„Home” i „+” (tymczasowe połączenia, wzorem MobaXterm)**, **graficzny SFTP po
lewej w każdej sesji** (`SftpPanel`), **sprawdzanie aktualizacji z GitHuba**,
**skaner sieci w menu „Programy”**, self-testy.

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

Backlog pomysłów siedzi w [`TODO.md`](TODO.md) — **nie** tutaj, bo `CLAUDE.md`
wchodzi do kontekstu przy każdej sesji. Największe znane dziury:

- **Emulacja VT100** — `vim`/`htop`/`mc` nadal rozjechane, bo `strip_ansi()`
  wycina adresowanie kursora. Największa pozostała dziura; kierunek: `pyte`.
- **Przeciąganie plików myszką** w panelu SFTP (na razie tylko przyciski i menu).
- **RDP**: przekierowanie schowka i dysków, wiele monitorów, brama RDP,
  zmiana rozdzielczości w locie (`UpdateSessionDisplaySettings`).

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

`py main.py --selftest` pokrywa logikę drzewa/zakładek i czyste funkcje terminala,
a przez `rdp.selftest()` **realnie sprawdza plumbing COM** — asercja na
`dynamicCall("Server")` wywala się, gdyby ktoś wrócił do `setProperty()` albo
zgubił listę wokół argumentu. Bez serwera RDP nie da się przetestować samego
połączenia; `RdpTab(..., autoconnect=False)` istnieje właśnie po to, żeby test
skonfigurował kontrolkę i nie dzwonił nigdzie po sieci.

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
