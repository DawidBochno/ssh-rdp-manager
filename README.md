# ssh-rdp-manager

Desktopowy menedżer połączeń SSH i RDP w Pythonie (PySide6), wzorowany na MobaXterm.
Drzewo połączeń po lewej, sesje w zakładkach po prawej: terminal SSH z panelem SFTP
albo pulpit RDP w tym samym oknie.

## Wymagania

- Python 3.11+ (na Windows uruchamiaj przez `py`, nie `python`)
- PySide6, Paramiko
- RDP w zakładce działa **tylko na Windows** (kontrolka ActiveX `MsTscAx`, ta sama,
  na której stoi `mstsc.exe`); gdy się nie uruchomi, sesja idzie do osobnego okna `mstsc`.

## Instalacja i uruchomienie

```bash
py -m pip install -r requirements.txt
py main.py
```

Testy (bez sieci i bez okna):

```bash
py main.py --selftest
```

## Co potrafi

**Połączenia**

- Drzewo grup i połączeń: przeciąganie myszą, ikony (emoji), kolory dziedziczone
  w grupie, zmiana nazwy i edycja wpisu.
- Zapis do `connections.json` obok programu; eksport i import tego samego formatu.
- Hasła **opcjonalnie** zapisywane, zaszyfrowane przez DPAPI (klucz związany z twoim
  kontem Windows). Bez zapisanego hasła program pyta jak dotąd.
- Wskazanie pliku klucza prywatnego per połączenie; puste hasło = logowanie kluczem
  (agent albo `~/.ssh`).
- Zakładka **Home** z wyszukiwarką zapisanych połączeń i przycisk **+** na pasku
  zakładek — połączenie „na szybko”, które nie trafia do drzewa ani na dysk.
- **Filtr nad drzewem**: wpisany tekst chowa wpisy niepasujące nazwą, hostem ani
  użytkownikiem; grupa zostaje, gdy pasuje cokolwiek w środku.
- **Import z `~/.ssh/config`** (Połączenie → Importuj): wpisy wchodzą jako osobna
  grupa, z hostem, portem, użytkownikiem i plikiem klucza. Parser jest z Paramiko,
  więc rozumie `Include` i `Match`.
- **Duplikowanie** wpisu z menu pod prawym klawiszem, **notatki** widoczne w dymku
  i **polecenia startowe** wysyłane do powłoki tuż po zalogowaniu.

**Sesja SSH**

- Podświetlanie składni po naszej stronie (błędy, ostrzeżenia, IP, ścieżki, URL-e),
  więc działa też, gdy serwer kolorów nie wysyła.
- Szukanie w terminalu (Ctrl+F), wklejanie (Ctrl+V i Ctrl+Shift+V), rozmiar PTY
  idący za rozmiarem okna.
- **Ctrl+Tab** i **Ctrl+1..9** przełączają zakładki; **Widok → Czcionka terminala**
  zmienia krój i rozmiar (wybór przeżywa restart), **Widok → Znacznik czasu**
  poprzedza każdą linię godziną, **Widok → Zapisz zapis sesji** odkłada bufor
  terminala do pliku.
- Panel **SFTP** po lewej stronie zakładki: nawigacja, pobieranie, wysyłanie
  (w tle, z paskiem postępu), nowy folder, usuwanie.
- Dolny pasek ze statystykami serwera: CPU, RAM, dysk, ruch sieciowy, uptime,
  liczba zalogowanych — osobno dla Linuksa i Windows Servera.
- Menu **Skrypty**: 11 gotowych poleceń administracyjnych (procesy, miejsce na dysku,
  błędy w logach, porty, restart usługi, aktualizacje, nieudane logowania, ping…),
  każde w wariancie linuksowym i windowsowym. Wynik można zapisać do pliku.
- **Własne skrypty**: plik `scripts.json` obok programu (lista obiektów
  `{"label", "unix", "windows", "prompt"}`) dopisuje pozycje do menu **Skrypty**.

**Narzędzia**

- Menu **Programy → Skaner sieci**: zakres adresów (`192.168.0.1-254`, `/24`, listy
  po przecinku) → tabela hostów z nazwą, adresem MAC i wykrytymi usługami.
  Dwuklik otwiera sesję SSH (albo RDP), a menu pod prawym klawiszem kopiuje wiersz,
  pojedynczą kolumnę albo budzi hosta przez Wake-on-LAN. Pod tabelą pasek postępu
  z licznikiem hostów i nazwą etapu (odpytywanie / tablica ARP).
- Menu **Programy → Wake-on-LAN**: magiczny pakiet pod podany adres MAC.
- Menu **Programy → Certyfikat TLS**: podmiot, wystawca, data ważności i liczba dni
  do wygaśnięcia — także dla certyfikatów samopodpisanych.
- Menu **Serwery**: wbudowany serwer HTTP i TFTP po *naszej* stronie — zdalny host
  pobiera plik od nas, zamiast stawiać cokolwiek u siebie.
- Sprawdzanie aktualizacji przy starcie: gdy gałąź `main` na GitHubie jest nowsza,
  program proponuje `git pull` (działa dla kopii z repozytorium, pyta przed pobraniem).

**Interfejs**

Domyślnie po **angielsku**; polski wybiera się w **Widok → Language** (zmiana działa
po ponownym uruchomieniu). Rozmiar okna i podział paneli wracają między sesjami.
Koniec długiego transferu, skryptu i skanowania zgłasza się dymkiem w zasobniku.

## Bezpieczeństwo

- Hasła zapisywane są **tylko na wyraźne życzenie** i wyłącznie przez DPAPI, czyli
  z kluczem twojego konta Windows — plik skopiowany na inny komputer jest bezużyteczny.
- Nieznany klucz serwera pokazuje odcisk i wymaga potwierdzenia, zamiast być
  akceptowany automatycznie. To ochrona przed atakiem typu man-in-the-middle.
- Skaner sieci wysyła zwykłe pingi i sprawdza kilka portów — używaj go w sieci,
  którą administrujesz.

## Znane ograniczenia

- **Brak emulacji VT100.** Sekwencje ANSI są wycinane, więc zwykła powłoka wygląda
  dobrze, ale programy pełnoekranowe (`vim`, `htop`, `mc`) będą rozjechane.
  To największa pozostała dziura — kierunek: `pyte`.
- Brak tuneli SSH i łączenia przez bastion (`ProxyJump`).
- Transferu SFTP nie da się przerwać w połowie (zostałby obcięty plik po drugiej stronie).
- RDP: bez przekierowania schowka i dysków, bez wielu monitorów i bramy RDP.

Backlog pomysłów siedzi w [TODO.md](TODO.md).

## Struktura

| Plik | Zawartość |
|---|---|
| `main.py` | Okno, drzewo połączeń, zakładki, formularz połączenia, menu |
| `ssh_terminal.py` | Sesja SSH (Paramiko), panel SFTP, statystyki, skrypty |
| `rdp.py` | Sesja RDP (kontrolka ActiveX Microsoftu) jako widget zakładki |
| `scanner.py` | Skaner sieci i okno z wynikami |
| `servers.py` | Wbudowane serwery HTTP i TFTP |
| `update.py` | Sprawdzanie aktualizacji względem gałęzi na GitHubie |
| `i18n.py` | Napisy interfejsu po angielsku i po polsku |
| `notify.py` | Powiadomienia systemowe (dymek z zasobnika) |

## Licencja

MIT
