# Lista rzeczy do zrobienia

Backlog pomysłów, **nie** zobowiązanie do zbudowania wszystkiego. Lista mieszka tutaj,
a nie w `CLAUDE.md`, bo tamten plik wchodzi do kontekstu przy **każdej** sesji —
czterdzieści punktów backlogu byłoby podatkiem od każdej rozmowy.

Koszt: **S** = kilkadziesiąt linii, **M** = ~100–300 linii, **L** = osobny projekt.
⭐ = to bym robił najpierw.

## Zrobione

Piątka ze startowej listy zrobiona. Zostaje do sprawdzenia **na żywym serwerze
RDP** — testy pokrywają konfigurację kontrolki, nie samo połączenie.

- [x] **RDP w zakładce** — `rdp.py`, kontrolka ActiveX `MsTscAx`, bez nowych zależności.
- [x] **Rozmiar PTY i wklejanie** — `terminal_size()`, `paste_bytes()`, Ctrl+V i Ctrl+Shift+V.
- [x] **Klucz SSH per połączenie** — pole `key_file` w formularzu i w `client.connect()`.
- [x] **Transfer SFTP w tle** — `_Transfer` (QThread) z paskiem postępu.
- [x] **Zapamiętywanie układu okna** — rozmiar okna i podział splittera w `QSettings`.
- [x] **Sprawdzanie aktualizacji z GitHuba** — `update.py`, pytanie przy starcie,
      pobranie przez `git pull --ff-only`.
- [x] **Skaner sieci** — `scanner.py`, menu „Programy”; ping + tablica ARP,
      otwieranie sesji i kopiowanie z menu podręcznego.

## Proponowana kolejność dalej

1. ⭐ **Emulacja VT100 przez `pyte`** (L) — największa pozostała dziura.
2. ⭐ **Tunele SSH** (M) — Paramiko to umie, admin tego chce.
3. ⭐ **Przeciąganie plików myszką w SFTP** (M).
4. ⭐ **Jump host / ProxyJump** (M) — łączenie przez bastion.
5. ⭐ **Podgląd logu na żywo** (M) — `tail -f` w osobnej zakładce, z filtrem.

Zrobione w międzyczasie: import z `~/.ssh/config`, filtr drzewa, własne skrypty
z `scripts.json`, duplikowanie wpisu, notatki, polecenia startowe, skróty zakładek,
czcionka terminala, znaczniki czasu, zapis sesji i wyniku skryptu, Wake-on-LAN,
odczyt certyfikatu TLS, powiadomienia systemowe, pasek postępu w skanerze.

## RDP

- [x] **Zakładka RDP na `QAxWidget`** — zrobione, patrz `rdp.py`.
- [x] **Droga awaryjna: `mstsc.exe`** — `launch_mstsc()` z wygenerowanym `.rdp`.
- [x] **Hasło do RDP z DPAPI** — przez `AdvancedSettings9.ClearTextPassword`.
- [ ] Przekierowanie schowka i dysków, wiele monitorów, brama RDP (M) — dopiero gdy ktoś
      poprosi.
- [ ] **Zmiana rozdzielczości w locie** (S) — dziś rozdzielczość ustala się przed
      połączeniem; w locie wymaga `UpdateSessionDisplaySettings`.

## Terminal

- [x] **`channel.resize_pty()` przy zmianie rozmiaru okna** — `terminal_size()`.
- [x] **Wklejanie (Ctrl+V i Ctrl+Shift+V)** — `paste_bytes()` zamienia `\n` na `\r`.
- [ ] **Wklejanie środkowym klawiszem myszy** (S) — zwyczaj z uniksowych terminali.
- [ ] **Emulacja VT100 przez `pyte`** (L) — `vim`, `htop`, `mc` są dziś rozjechane,
      bo `strip_ansi()` wycina adresowanie kursora. Największa dziura funkcjonalna,
      ale i największa: to wymiana całego renderowania, nie łatka.
- [x] **Krój i rozmiar czcionki w menu** (S) — dziś zaszyte `QFont("Consolas", 10)`
      w dwóch miejscach.
- [ ] **Długość historii przewijania** (S) — dziś `setMaximumBlockCount(5000)`.
- [x] **Zapis sesji do pliku** (S) — log wszystkiego, co przyszło z serwera.
- [ ] **Wysyłanie tego samego polecenia do wszystkich zakładek** (M) — MobaXterm to ma,
      przy kilkunastu serwerach naraz oszczędza godziny.
- [x] **Ctrl+Tab i Ctrl+1..9 do przełączania zakładek** (S).

## Połączenia i drzewo

- [x] **Wskazanie pliku klucza per połączenie** — pole `key_file`, chowane przy RDP.
- [ ] **Hasło do klucza (passphrase) osobno od hasła konta** (S) — dziś Paramiko
      dostaje to samo pole w obu rolach.
- [ ] **Tunele SSH (przekierowanie portów)** (M) — Paramiko to umie, admin tego chce.
- [ ] **Jump host / ProxyJump** (M) — łączenie przez bastion.
- [x] **Import z `~/.ssh/config`** (S) — stdlib nie ma parsera, ale Paramiko ma
      `SSHConfig`. Tanie wejście dla kogoś, kto ma już swoje wpisy.
- [ ] **Import z PuTTY** (M) — wpisy siedzą w rejestrze Windows.
- [x] **Duplikowanie połączenia** (S) — dziś trzeba przeklikać formularz od zera.
- [x] **Polecenia startowe po zalogowaniu** (S) — np. `sudo -i`, `cd /var/log`.
- [ ] **Automatyczne ponowne łączenie po zerwaniu** (M) — z limitem prób,
      inaczej zrobi się pętla dobijająca się do wyłączonego serwera.
- [x] **Notatki do połączenia** (S).
- [ ] **Kropka statusu przy wpisach w drzewie** (M) — sprawdzanie samych zapisanych
      połączeń; odstęp konfigurowalny, domyślnie rzadko. Sprawdzanie portu jest już
      w `scanner.py`, więc zostaje wątek i odświeżanie drzewa.
- [x] **Wake-on-LAN** (S) — magic packet to kilkanaście linii na `socket`.

## SFTP

- [x] **Transfer na `QThread` z paskiem postępu** — `_Transfer` i `run_transfer()`.
- [ ] **Anulowanie transferu** (M) — świadomie pominięte: przerwanie w pół pliku
      zostawia obcięty plik po drugiej stronie. Dołożyć razem ze wznawianiem.
- [ ] **Przeciąganie plików myszką** (M) — dziś tylko przyciski i menu.
- [ ] **Zmiana nazwy i uprawnień (chmod)** (S).
- [ ] **Edycja pliku na miejscu** (M) — pobierz do temp, otwórz w edytorze, odeślij
      po zapisaniu. Pułapka: wykrycie, że plik naprawdę się zmienił.
- [ ] **Wolne miejsce na zdalnym dysku w pasku panelu** (S) — statystyki już to liczą.

## Interfejs

- [x] **Zapamiętywanie rozmiaru okna i pozycji splitterów** — `_save_layout()`
      w `closeEvent`, `_restore_layout()` w `__init__`.
- [ ] **Motyw ciemny/jasny** (M) — dziś idziemy motywem systemu.
- [ ] **Podział ekranu: kilka sesji obok siebie** (M).
- [ ] **Przełączanie języka bez restartu** (M) — dziś świadomie po restarcie, bo
      przebudowa okna zabiłaby otwarte sesje. Sensowna droga: przeładować tylko
      pasek menu i zakładkę Home, sesji nie ruszać.

## Skrypty i serwery wbudowane

- [x] **Własne skrypty użytkownika** (S) — dziś `SCRIPTS` jest zaszyte w kodzie;
      wczytywanie dodatkowych z pliku JSON obok `connections.json`.
- [ ] **Uruchomienie skryptu na wielu serwerach naraz** (M) — z wynikiem per serwer.
- [x] **Zapis wyniku skryptu do pliku** (S) — dziś tylko okno i Ctrl+F.
- [ ] **Kopiowanie gotowej komendy `wget`/`curl` do schowka** (S) — po uruchomieniu
      serwera HTTP i tak ręcznie przepisujemy adres do sesji SSH.
- [ ] **Podgląd żądań do serwera HTTP** (S) — widać, czy zdalny host faktycznie pobrał.

## Bezpieczeństwo

- [ ] **Hasło główne do pliku połączeń** (M) — dziś DPAPI wiąże hasła z kontem Windows,
      co jest dobre lokalnie, ale eksport na inny komputer jest bezużyteczny.
- [ ] **Blokada okna po bezczynności** (S).

## Świadomie odrzucone

- **FreeRDP** — pierwotny kierunek na RDP. Odpada: wymaga budowania i dowożenia
  natywnych binariów, a kontrolka ActiveX daje to samo w oknie bez żadnej
  nowej zależności.
- **`gettext` / `QTranslator`** — wymagają kompilowania `.mo`/`.qm` przy każdej zmianie
  napisu. Przy dwóch językach zwykły słownik wygrywa (patrz `i18n.py`).
- **`cmdkey` do zapisu haseł RDP** — zostawia poświadczenia w Windows Credential
  Manager, poza naszym plikiem i poza naszą kontrolą przy odinstalowaniu.
- **Kolorowanie po stronie serwera (pełne ANSI)** — do czasu emulacji VT100
  własne `HIGHLIGHT_RULES` działają nawet gdy serwer kolorów nie wysyła.

## Pomysły na przyszłość (druga runda)

Zebrane po przeglądzie, **poza** listami powyżej. Bez oceny kosztu tam, gdzie
zależy od zakresu; ⭐ = najtańsze z realnym zyskiem.

### Sesje i połączenia

- [ ] **Menedżer poświadczeń** (M) — jedno konto (login + hasło + klucz) współdzielone
      przez wiele wpisów, zamiast wpisywać to samo w każdym.
- [ ] **Zmienne w połączeniach** (`%h`, `%u`) (S) — w poleceniach startowych i skryptach.
- [ ] **Historia połączeń / „ostatnio używane"** (S) — lista pod Home, z czasem
      ostatniego logowania.
- [x] **Filtrowanie drzewa** (S) — pole szukania nad drzewem, filtr po
      nazwie i hoście.
- [ ] **Tagi na połączeniach** (S) — `prod`, `db`, `klient-X`, plus filtr po tagu.
- [ ] **Telnet i połączenie szeregowe (COM)** (M) — `pyserial`, przydatne przy
      switchach i UPS-ach.
- [ ] **Serwer skoku dla całej grupy** (S) — bastion ustawiany raz na grupie,
      dziedziczony jak kolor; wymaga wpierw jump hosta z listy wyżej.
- [ ] **„Połącz na próbę" w formularzu** (S) — sprawdza dane bez otwierania zakładki.

### Terminal

- [ ] **Autouzupełnianie z historii poleceń** (M) — podpowiedź jak w fish, z lokalnej
      historii wpisanych komend.
- [ ] **Makra — własne przyciski wysyłające tekst** (S) do bieżącej sesji.
- [ ] **Wyzwalacze na tekst** (S) — regex w wyjściu → powiadomienie systemowe
      (`FAILED`, `Kernel panic`). `HIGHLIGHT_RULES` już mają połowę roboty.
- [x] **Timestampy na linii** (S) — przełącznik: każda linia poprzedzona godziną.
- [ ] **Kopiowanie zaznaczenia automatycznie** (S) — zwyczaj X11, plus Ctrl+Shift+C.
- [ ] **Eksport wyjścia sesji do HTML** (S) — z kolorami z podświetlania.

### SFTP i pliki

- [ ] **Drugi panel — lokalny** (M) — klasyczny widok dwupanelowy, kopiowanie w obie strony.
- [ ] **Synchronizacja katalogu** (M) — jednostronna, porównanie po rozmiarze i dacie.
- [ ] **Kolejka transferów** (M) — zamiast blokowania `QEventLoop` po jednym pliku.
- [ ] **Podgląd pliku tekstowego bez pobierania** (S) — pierwsze N kB do okna z Ctrl+F.
- [ ] ⭐ **Zakładki katalogów** (S) — `/var/log`, `/etc/nginx`, per połączenie.

### Administracja

- [ ] **Lista procesów z zabijaniem** (M) — tabela z `ps` / `Get-Process` i przyciskiem kill.
- [ ] ⭐ **Menedżer usług** (M) — start/stop/restart `systemctl` / `Get-Service` z listy.
- [ ] ⭐ **Podgląd logu na żywo** (M) — `tail -f` w osobnej zakładce, z filtrem.
- [ ] **Wykresy CPU/RAM/sieci w czasie** (M) — sparkline na `QPainter` pod paskiem
      statusu, historia prosto z pollera.
- [ ] **Panel dysków i inode'ów** (S) — `df -h` w tabeli, ostrzeżenie przy >90%.
- [ ] **Lista aktualizacji pakietów z instalacją jednym kliknięciem** (M).

### Reszta

- [x] **Powiadomienia systemowe** (S) — koniec długiego polecenia albo transferu.
- [ ] **Tryb tylko do odczytu dla zakładki** (S) — blokada wysyłania klawiszy,
      do demonstracji i prezentacji.
- [ ] **Dziennik audytu** (S) — kto, kiedy, co uruchomił; plik lokalny.
- [x] **Sprawdzanie certyfikatów TLS hosta** (S) — data wygaśnięcia, stdlib `ssl`.
- [ ] ⭐ **Generator kluczy SSH** (M) — plus wgranie do `authorized_keys` jednym kliknięciem.
