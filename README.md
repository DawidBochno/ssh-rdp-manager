# ssh-rdp-manager

Desktopowy menedżer połączeń SSH i RDP w Pythonie (PySide6), wzorowany na MobaXterm.
Drzewo połączeń po lewej, sesje w zakładkach po prawej: terminal SSH z panelem SFTP
albo pulpit RDP w tym samym oknie.

## Wymagania

- Python 3.11+ (na Windows uruchamiaj przez `py`, nie `python`)
- PySide6, Paramiko, pyte
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

- **Paleta poleceń** (Ctrl+Shift+P): jedno pole szuka po połączeniach (nazwa,
  host, login, `#tag`) i wszystkich akcjach menu, łącznie z Programami i skryptami.
- **Przypinanie na Start** (prawy klik w drzewie): przypięte połączenia na górze
  listy Start, pod nimi ostatnio używane.
- **Menu zakładki** (prawy klik): zamknij pozostałe, zmień nazwę, duplikuj sesję,
  podgląd logu, **tylko do odczytu** (🔒 na zakładce: klawisze, wklejanie i makra
  nie idą do serwera — do prezentacji). Zakładka w tle z nowym wyjściem dostaje
  znacznik „● ”.
- **Makra**: przyciski nad terminalem wysyłające gotowe polecenie z Enterem.
  Definiuje się je w **Ustawienia → Terminal → Makra**, jedno na linię:
  `Nazwa = polecenie` (np. `Root = sudo -i`).
- Drzewo grup i połączeń: przeciąganie myszą, ikony (emoji), kolory dziedziczone
  w grupie, zmiana nazwy i edycja wpisu.
- Zapis do `connections.json` obok programu; eksport i import tego samego formatu.
- Hasła **opcjonalnie** zapisywane, zaszyfrowane przez DPAPI (klucz związany z twoim
  kontem Windows). Bez zapisanego hasła program pyta jak dotąd.
- Wskazanie pliku klucza prywatnego per połączenie; puste hasło = logowanie kluczem
  (agent — Pageant albo agent OpenSSH — albo `~/.ssh`). **Hasło klucza (passphrase)**
  ma osobne pole — to nie to samo co hasło konta i zapisuje się osobno.
- **Przekazuj agenta SSH** (pole w formularzu, jak `ssh -A`): klucze z agenta
  działają też w `ssh`/`git` uruchomionym na serwerze. Tylko dla zaufanych
  serwerów — ich administrator może w tym czasie użyć twoich kluczy.
- **Poświadczenia** (menu **Programy → Poświadczenia** albo przycisk **Nowe…**
  w formularzu połączenia): jedno konto — login, hasło, klucz, hasło klucza —
  wskazywane przez wiele połączeń. Zmiana hasła na koncie działa od razu na
  wszystkich. Konta leżą w `credentials.json` (hasła też przez DPAPI); usunięte
  konto nie psuje połączeń — wracają one do własnych pól.
- Zakładka **Home** z wyszukiwarką zapisanych połączeń — **ostatnio używane**
  na górze, z datą ostatniego otwarcia — i przycisk **+** na pasku zakładek:
  połączenie „na szybko”, które nie trafia do drzewa ani na dysk.
- **Filtr nad drzewem**: wpisany tekst chowa wpisy niepasujące nazwą, hostem ani
  użytkownikiem; grupa zostaje, gdy pasuje cokolwiek w środku.
- **Tagi** na połączeniach (`prod, db, klient-x`): widoczne w dymku, a `#prod`
  w filtrze drzewa albo w wyszukiwarce Home pokazuje tylko otagowane wpisy.
- **Połącz na próbę** w formularzu połączenia: sprawdza dane logowania SSH
  (albo dostępność portu RDP) bez otwierania zakładki.
- Klawiatura w drzewie: **Enter** łączy, **F2** edytuje, **Delete** usuwa
  (zawsze z potwierdzeniem).
- **Import z `~/.ssh/config`** (Połączenie → Importuj): wpisy wchodzą jako osobna
  grupa, z hostem, portem, użytkownikiem i plikiem klucza. Parser jest z Paramiko,
  więc rozumie `Include` i `Match`.
- **Duplikowanie** wpisu z menu pod prawym klawiszem, **notatki** widoczne w dymku
  i **polecenia startowe** wysyłane do powłoki tuż po zalogowaniu.
- **Jump host / ProxyJump**: pole w formularzu połączenia łączy najpierw z bastionem,
  a dopiero z niego z celem — tym samym kontem/kluczem, co najczęstszy przypadek.

**Sesja SSH**

- Podświetlanie składni po naszej stronie (błędy, ostrzeżenia, IP, ścieżki, URL-e),
  więc działa też, gdy serwer kolorów nie wysyła.
- **Programy pełnoekranowe działają poprawnie** (`vim`, `htop`, `mc`, `less`, `top`):
  terminal wykrywa alternate screen i rysuje siatkę znaków z prawdziwym
  adresowaniem kursora i kolorami zamiast rozjeżdżać tekst.
- Szukanie w terminalu (Ctrl+F), wklejanie (Ctrl+V, Ctrl+Shift+V, środkowy klawisz, prawy klik → Wklej), rozmiar PTY
  idący za rozmiarem okna. **Ctrl+C** kopiuje, gdy coś jest zaznaczone —
  bez zaznaczenia przerywa polecenie (`^C`), jak w każdym terminalu.
- **Ctrl+Tab** i **Ctrl+1..9** przełączają zakładki; dymek nad zakładką pokazuje
  `użytkownik@host:port`. Czcionka i znacznik czasu przy każdej linii — w oknie
  **Ustawienia**; **Widok → Zapisz zapis sesji** odkłada bufor terminala do pliku.
- **Ctrl+klik** na adresie `http(s)://` otwiera go w przeglądarce, na adresie IP
  kopiuje go do schowka.
- **Edytor polecenia** (Ctrl+Shift+E albo prawy klik na zakładce): pole pod
  terminalem na polecenie z wielu linii, poprawiane przed wysłaniem (Ctrl+Enter),
  opcjonalnie **do wszystkich otwartych sesji**.
- **Ustawienia → Terminal → Zapisuj wszystkie sesje do plików**: każda nowa sesja
  SSH trafia na bieżąco do folderu `logs` obok programu (plik na sesję, starsze
  niż 30 dni są kasowane).
- Panel **SFTP** po lewej stronie zakładki (operacje w tle — wolny serwer nie zamraża okna): nawigacja, pobieranie, wysyłanie
  (w tle, w **kolejce transferów** pod listą plików: stan każdego pliku,
  anulowanie zaznaczonych — przerwany plik jest kasowany, żeby nie zostawał
  obcięty), nowy folder, usuwanie. Przycisk **⭐** trzyma
  **zakładki katalogów** (`/var/log`, `/etc/nginx`) — osobne dla każdego
  połączenia, zapisywane w `connections.json`.
- **Przeciągnij i upuść** pliki z Eksploratora prosto do panelu SFTP — wysyła je
  na serwer bez przechodzenia przez przycisk.
- **Edycja pliku zdalnego** (menu pod prawym klawiszem → Edytuj): plik otwiera się
  w domyślnym lokalnym edytorze, a zapis odsyła go z powrotem na serwer automatycznie.
- **Tunele SSH** (Programy → Tunele SSH): `L 8080:10.0.0.5:80` przekierowuje port
  lokalny na maszynę widzianą przez serwer, `R 9000:127.0.0.1:22` odwrotnie.
  Tunele zapisane przy połączeniu wstają razem z sesją i znikają razem z nią.
- **Wyzwalacze na tekst** (Ustawienia → Terminal): regex na wyjście serwera, jeden
  na linię; trafienie daje dymek w zasobniku, także gdy okno jest zminimalizowane.
- **Ustawienia → Terminal → Historia przewijania** ustawia, ile linii trzyma terminal
  (domyślnie 5000) — tyle też trafia do zapisu sesji.
- Dolny pasek ze statystykami serwera: CPU, RAM, dysk, ruch sieciowy, uptime,
  liczba zalogowanych — osobno dla Linuksa i Windows Servera. Z prawej **wykres
  CPU (niebieski) i RAM (zielony) z ostatnich 5 minut**; najechanie myszą
  pokazuje bieżące wartości.
- **Zbiorczy dashboard** (Programy → Panel statystyk…): statystyki wszystkich
  otwartych zakładek SSH i RDP na jednym ekranie, odświeżane co 2 sekundy.
- **Motywy kolorów terminala** (Ustawienia → Wygląd): Dark, Solarized Dark,
  Dracula, Default.
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
- Menu **Programy → Usługi**: lista usług z aktywnej sesji (`systemctl` albo
  `Get-Service`) ze start/stop/restart.
- Menu **Programy → Podgląd logu na żywo**: `tail -f` w osobnym oknie, z filtrem (regex).
- Menu **Programy → Dyski**: `df -h`/`df -i` (albo dyski Windows) w tabeli,
  czerwony wiersz powyżej 90%.
- Menu **Programy → Procesy**: `ps` (albo `Get-Process`) w tabeli, posortowane
  po CPU, z zabijaniem zaznaczonego procesu (po potwierdzeniu).
- Menu **Programy → Polecenie na wielu serwerach**: własne polecenie albo gotowy
  skrypt uruchamiany równolegle na zaznaczonych otwartych sesjach SSH, wynik
  osobno dla każdego serwera (OK/BŁĄD), z zapisem do pliku.
- Menu **Programy → Generator kluczy SSH**: RSA albo Ed25519, opcjonalne hasło klucza,
  wgranie klucza publicznego do `authorized_keys` aktywnej sesji jednym kliknięciem.
- **Monitoring w tle**: pole „Monitoruj w tle” w formularzu połączenia. Zaznaczone
  serwery są sprawdzane co kilka minut bez otwierania zakładki (odstęp w
  Ustawieniach → Powiadomienia). Na Starcie kafelki: zielony = OK, żółty = CPU/RAM/
  dysk ponad próg albo certyfikat TLS ważny ≤ 14 dni (pole „Port TLS”, np. 443),
  czerwony = brak odpowiedzi; podpowiedź pokazuje ostatnie 24 h, prawy klik →
  „Historia…” rysuje wykres CPU/RAM z 24 h, 7 albo 30 dni i eksportuje pomiary
  do CSV (Excel); dwuklik otwiera sesję. Zmiana stanu = dymek w zasobniku. Historia (30 dni)
  w lokalnym `monitor.db`. Serwer za hostem pośrednim ma certyfikat TLS sprawdzany
  przez ten host. Nieznany klucz serwera nie jest akceptowany w tle —
  trzeba raz połączyć się ręcznie. Zamknięcie programu przerywa trwające sprawdzanie.
- Menu **Programy → Podziel ekran**: 2–4 sesje SSH obok siebie w jednej zakładce;
  **„Wpisuj do wszystkich”** wysyła to, co piszesz, do każdego terminala w siatce
  (czerwone ramki; terminale tylko do odczytu pomijane). „Rozdziel” oddaje zakładki.
- Okna narzędzi (Usługi, Dyski, Procesy, Skrypty, Generator kluczy, TLS) pracują
  w tle — wolny serwer nie zamraża programu, a odmowa serwera pokazuje jego
  komunikat (np. „Access denied”).
- Menu **Serwery**: wbudowany serwer HTTP i TFTP po *naszej* stronie — zdalny host
  pobiera plik od nas, zamiast stawiać cokolwiek u siebie.
- Sprawdzanie aktualizacji przy starcie: gdy gałąź `main` na GitHubie jest nowsza,
  program proponuje `git pull` (działa dla kopii z repozytorium, pyta przed pobraniem).

**Interfejs**

Wszystkie ustawienia w jednym oknie **Widok → Ustawienia** (Ctrl+Shift+S),
w czterech zakładkach: **Terminal** (podświetlanie, znaczniki czasu, czcionka,
historia przewijania, wyzwalacze), **Wygląd** (motyw terminala, ciemny motyw okna,
język), **Powiadomienia** (status serwerów w drzewie, alerty progowe),
**Bezpieczeństwo** (blokada po bezczynności, PIN). Zmiany działają po OK,
Anuluj niczego nie rusza.

Domyślnie po **angielsku**; polski wybiera się w Ustawieniach → Wygląd (zmiana
działa po ponownym uruchomieniu). Rozmiar okna i podział paneli wracają między
sesjami. Koniec długiego transferu, skryptu i skanowania zgłasza się dymkiem
w zasobniku.

Skróty okna (pełna lista: **Pomoc → Skróty klawiszowe**):

| Skrót | Działanie |
|---|---|
| Ctrl+Shift+N | nowe połączenie |
| Ctrl+Shift+T | szybkie połączenie |
| Ctrl+Shift+W | zamknij zakładkę |
| Ctrl+Shift+F | filtr listy połączeń |
| Ctrl+Shift+S | ustawienia |
| Ctrl+Shift+P | paleta poleceń: połączenia, akcje menu, skrypty |
| Ctrl+Shift+E | edytor polecenia pod terminalem |

Wszystkie z Shiftem celowo — samo Ctrl+N/W/F/T należy do powłoki i terminala.

## Bezpieczeństwo

- Hasła zapisywane są **tylko na wyraźne życzenie** i wyłącznie przez DPAPI, czyli
  z kluczem twojego konta Windows — plik skopiowany na inny komputer jest bezużyteczny.
- Nieznany klucz serwera pokazuje odcisk i wymaga potwierdzenia, zamiast być
  akceptowany automatycznie. To ochrona przed atakiem typu man-in-the-middle.
  Odcisk jest w formacie SHA256 (jak `ssh-keygen -lf`), a zaakceptowany klucz
  trafia do pliku `known_hosts` obok `connections.json` — kolejne połączenie
  nie pyta, a podmieniony klucz tego samego serwera zostaje odrzucony.
  **Programy → Zapamiętane klucze serwerów** pokazuje te wpisy z odciskami
  i pozwala usunąć nieaktualny (np. po reinstalacji serwera).
- Zerwana sesja SSH łączy się ponownie sama (5 prób: po 2, 5, 10, 30 i 60 s);
  treść terminala zostaje, SFTP, tunele i polecenia startowe wstają od nowa.
  `exit` w powłoce zamyka sesję normalnie, bez ponownego łączenia.
- Skaner sieci wysyła zwykłe pingi i sprawdza kilka portów — używaj go w sieci,
  którą administrujesz.
- **Ustawienia → Bezpieczeństwo → Blokada po bezczynności**: po ustawionym czasie
  braku ruchu myszą/klawiaturą okno pokazuje ekran blokady, który otwiera tylko
  PIN (hash, nie plaintext; zmienia się w tym samym oknie). To lekki odstraszacz przed przypadkowym
  zerknięciem, gdy Windows jest odblokowany — nie zastępuje hasła głównego do
  pliku połączeń (wciąż na liście do zrobienia).

## Znane ograniczenia

- SFTP: bez przesyłania całych folderów i bez wznawiania przerwanego transferu.
- RDP: bez wielu monitorów i bramy RDP; rozdzielczość ustala się przed połączeniem.

## Plany

Pełna lista ponad 40 propozycji z porównaniem do MobaXterm, Termius, Royal TS,
SecureCRT, mRemoteNG, Tabby, WindTerm i Xshell: **[ROADMAP.md](ROADMAP.md)**.

Najbliżej w kolejce: kolor środowiska produkcyjnego na zakładce, przywracanie sesji
po starcie, dziedziczenie ustawień z grupy i import z innych programów.

## Automatyczne sprawdzanie zmian (PR)

Każda zmiana trafia na `main` przez **pull request** (PR) — propozycję zmian,
którą GitHub sprawdza, zanim zostanie scalona. Przy każdym PR same uruchamiają się:

| Sprawdzenie | Co robi | Plik |
|---|---|---|
| **Selftest** | uruchamia `py main.py --selftest` na Windows | `.github/workflows/selftest.yml` |
| **Przegląd Claude** | czyta zmiany i pisze po polsku komentarz z werdyktem: ✅ można scalać / ⚠️ warto poprawić / ❌ nie scalać | `.github/workflows/claude-review.yml` |

Jak z tego korzystać bez znajomości kodu: otwórz PR na GitHubie (zakładka
**Pull requests**), sprawdź, że przy **Selftest** jest zielony ✓, i przeczytaj
komentarz przeglądu. Zielone + „✅ Można scalać” → przycisk **Merge pull request**.

**Jednorazowa konfiguracja przeglądu Claude** (bez tego przegląd jest pomijany,
Selftest działa i tak): w repozytorium **Settings → Secrets and variables →
Actions → New repository secret**, nazwa `ANTHROPIC_API_KEY`, wartość — klucz
z [console.anthropic.com](https://console.anthropic.com/) (przegląd zużywa
płatne tokeny API, zwykle grosze za PR).

## Struktura

| Plik | Zawartość |
|---|---|
| `main.py` | Okno, drzewo połączeń, zakładki, formularz połączenia, menu |
| `ssh_terminal.py` | Sesja SSH (Paramiko), statystyki, skrypty |
| `graphs.py` | Wykres CPU/RAM z ostatnich 5 minut w pasku statusu |
| `sftp.py` | Panel SFTP (operacje w wątku w tle) |
| `transfers.py` | Transfery SFTP: kolejka w tle, anulowanie |
| `rdp.py` | Sesja RDP (kontrolka ActiveX Microsoftu) jako widget zakładki |
| `scanner.py` | Skaner sieci i okno z wynikami |
| `servers.py` | Wbudowane serwery HTTP i TFTP |
| `update.py` | Sprawdzanie aktualizacji względem gałęzi na GitHubie |
| `i18n.py` | Napisy interfejsu po angielsku i po polsku |
| `notify.py` | Powiadomienia systemowe (dymek z zasobnika) |
| `tunnels.py` | Tunele SSH (przekierowanie portów) i okno do ich zarządzania |
| `services.py` | Menedżer usług (start/stop/restart) |
| `credentials.py` | Szyfrowanie haseł (DPAPI) i konta współdzielone |
| `logtail.py` | Podgląd logu na żywo (`tail -f`) |
| `disks.py` | Panel dysków i inode'ów |
| `processes.py` | Lista procesów z sortowaniem i zabijaniem (także przez `sudo`) |
| `settings.py` | Okno Ustawień |
| `multirun.py` | Polecenie/skrypt na wielu serwerach naraz |
| `keygen.py` | Generator kluczy SSH |
| `monitor.py` | Monitoring serwerów w tle, historia w SQLite |
| `split.py` | Podział ekranu i pisanie do wielu terminali |
| `ROADMAP.md` | Porównanie z rynkiem i plan rozwoju |

## Licencja

MIT
