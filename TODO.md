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

## Proponowana kolejność dalej

1. ⭐ **Emulacja VT100 przez `pyte`** (L) — największa pozostała dziura.
2. ⭐ **Tunele SSH** (M) — Paramiko to umie, admin tego chce.
3. ⭐ **Import z `~/.ssh/config`** (S) — tanie wejście dla kogoś z gotowymi wpisami.
4. ⭐ **Przeciąganie plików myszką w SFTP** (M).
5. ⭐ **Własne skrypty użytkownika** (S) — dziś `SCRIPTS` jest zaszyte w kodzie.

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
- [ ] **Krój i rozmiar czcionki w menu** (S) — dziś zaszyte `QFont("Consolas", 10)`
      w dwóch miejscach.
- [ ] **Długość historii przewijania** (S) — dziś `setMaximumBlockCount(5000)`.
- [ ] **Zapis sesji do pliku** (S) — log wszystkiego, co przyszło z serwera.
- [ ] **Wysyłanie tego samego polecenia do wszystkich zakładek** (M) — MobaXterm to ma,
      przy kilkunastu serwerach naraz oszczędza godziny.
- [ ] **Ctrl+Tab i Ctrl+1..9 do przełączania zakładek** (S).

## Połączenia i drzewo

- [x] **Wskazanie pliku klucza per połączenie** — pole `key_file`, chowane przy RDP.
- [ ] **Hasło do klucza (passphrase) osobno od hasła konta** (S) — dziś Paramiko
      dostaje to samo pole w obu rolach.
- [ ] **Tunele SSH (przekierowanie portów)** (M) — Paramiko to umie, admin tego chce.
- [ ] **Jump host / ProxyJump** (M) — łączenie przez bastion.
- [ ] **Import z `~/.ssh/config`** (S) — stdlib nie ma parsera, ale Paramiko ma
      `SSHConfig`. Tanie wejście dla kogoś, kto ma już swoje wpisy.
- [ ] **Import z PuTTY** (M) — wpisy siedzą w rejestrze Windows.
- [ ] **Duplikowanie połączenia** (S) — dziś trzeba przeklikać formularz od zera.
- [ ] **Polecenia startowe po zalogowaniu** (S) — np. `sudo -i`, `cd /var/log`.
- [ ] **Automatyczne ponowne łączenie po zerwaniu** (M) — z limitem prób,
      inaczej zrobi się pętla dobijająca się do wyłączonego serwera.
- [ ] **Notatki do połączenia** (S).
- [ ] **Sprawdzanie dostępności (port 22) i kropka statusu w drzewie** (M) — kuszące,
      ale to wątek odpytujący *wszystkie* wpisy; łatwo zrobić z tego skaner portów
      po całej sieci klienta. Odstęp konfigurowalny, domyślnie rzadko.
- [ ] **Wake-on-LAN** (S) — magic packet to kilkanaście linii na `socket`.

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

- [ ] **Własne skrypty użytkownika** (S) — dziś `SCRIPTS` jest zaszyte w kodzie;
      wczytywanie dodatkowych z pliku JSON obok `connections.json`.
- [ ] **Uruchomienie skryptu na wielu serwerach naraz** (M) — z wynikiem per serwer.
- [ ] **Zapis wyniku skryptu do pliku** (S) — dziś tylko okno i Ctrl+F.
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
