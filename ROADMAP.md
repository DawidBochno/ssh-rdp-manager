# Plan rozwoju — porównanie z rynkiem

Stan na 2026-10-01. Porównanie z najpopularniejszymi programami do
administracji serwerami przez SSH/RDP i lista propozycji, co dalej.

## Z kim się porównujemy

| Program | Mocne strony, których nam brakuje |
|---|---|
| **MobaXterm** | serwer X11, wiele protokołów (VNC, Telnet, port szeregowy), wysyłanie do wielu terminali, ZMODEM, narzędzia uniksowe na Windows |
| **Termius** | szyfrowana synchronizacja połączeń między komputerami i telefonem, biblioteka gotowych poleceń (snippets) ze zmiennymi, podpowiadanie poleceń, praca zespołowa |
| **Royal TS / Devolutions RDM** | sejf haseł i integracje (KeePass, Bitwarden, 1Password, Azure Key Vault), dziedziczenie ustawień w folderach, wiele protokołów, uprawnienia zespołu, dziennik audytu |
| **SecureCRT** | skrypty (Python), pasek przycisków, okno „wyślij do wszystkich”, rozbudowane podświetlanie słów, port szeregowy i Telnet |
| **mRemoteNG** (darmowy) | RDP/VNC/SSH/Telnet/HTTP w jednym drzewie, dziedziczenie ustawień, import z Active Directory, zewnętrzne narzędzia |
| **Tabby** (darmowy) | wtyczki, podział paneli, szyfrowany sejf, port szeregowy, synchronizacja ustawień |
| **WindTerm** (darmowy) | podpowiadanie poleceń, nagrywanie i odtwarzanie sesji, ZMODEM, bardzo szybki terminal |
| **Xshell** | pasek „compose” do edycji polecenia przed wysłaniem, wysyłanie do wielu sesji, skrypty |
| **Warp / Windows Terminal** | asystent AI w terminalu, bloki poleceń, profile |

**Czego żaden z nich nie ma w jednym miejscu, a my mamy:** statystyki i wykres
serwera na pasku, monitoring w tle z kafelkami i historią, panele Usługi/Dyski/
Procesy dla Linuksa *i* Windows Servera, wbudowane serwery HTTP/TFTP, skaner
sieci z Wake-on-LAN i odczytem TLS — w darmowym programie.

## Propozycje

Wielkość: **S** = godziny, **M** = dni, **L** = tydzień i więcej.
Priorytet: ⭐⭐⭐ = najpierw (duży zysk, mały koszt).

### Połączenia i protokoły

| # | Propozycja | Na wzór | Wielkość | Priorytet |
|---|---|---|---|---|
| 1 | ✅ **Dziedziczenie ustawień z grupy** — login, klucz, port, bastion ustawione raz na folderze | mRemoteNG, Royal TS | M | ⭐⭐⭐ |
| 2 | ✅ **Import z innych programów** — MobaXterm, PuTTY (rejestr), mRemoteNG, RDCMan, CSV | Royal TS, mRemoteNG | M | ⭐⭐⭐ |
| 3 | ✅ **Przywracanie sesji po starcie** — otwarte zakładki (i siatka) wracają po ponownym uruchomieniu | Tabby, Windows Terminal | S | ⭐⭐⭐ |
| 4 | ~~**Obszary robocze** — zapisany zestaw zakładek/siatek („poranny przegląd”) otwierany jednym kliknięciem~~ — zrobione 2026-10-06 (menu „Obszary robocze”) | Royal TS | M | ⭐⭐ |
| 5 | ~~**VNC** jako typ połączenia~~ — zrobione 2026-10-08 (VNC; własny klient, Raw+CopyRect) | MobaXterm, mRemoteNG | M | ⭐⭐ |
| 6 | ~~**Telnet i port szeregowy (COM)** — przełączniki, routery, konsole UPS~~ — zrobione 2026-10-08 | SecureCRT, Tabby | M | ⭐⭐ |
| 7 | ~~**PowerShell Remoting / WinRM** dla Windows bez OpenSSH~~ — zrobione 2026-10-08 (konsola PowerShell, polecenie po poleceniu) | Devolutions RDM | M | ⭐ |
| 8 | **Przekierowanie X11** (okienkowe programy z Linuksa) | MobaXterm | L | ⭐ |
| 9 | **Import z chmury / AD** — lista maszyn z AWS, Azure, Proxmox albo Active Directory | Royal TS, RDM | L | ⭐ |
| 10 | ~~**RDP: wiele monitorów, brama RD Gateway, zmiana rozdzielczości w locie**~~ — zrobione 2026-10-08 (brama RD Gateway, rozdzielczość w locie; wiele monitorów przez mstsc) | wszystkie | L | ⭐⭐ |

### Terminal

| # | Propozycja | Na wzór | Wielkość | Priorytet |
|---|---|---|---|---|
| 11 | ✅ **Biblioteka poleceń (snippets) ze zmiennymi** — `systemctl restart {usługa}`, per grupa, w palecie | Termius | M | ⭐⭐⭐ |
| 12 | ~~**Podpowiadanie poleceń** z historii wszystkich sesji~~ — zrobione 2026-10-06 (bez linii po Tab/strzałkach i bez haseł) | WindTerm, Termius | M | ⭐⭐ |
| 13 | ✅ **Pasek „compose”** — wieloliniowe polecenie edytowane przed wysłaniem, opcjonalnie do wielu sesji | Xshell, SecureCRT | S | ⭐⭐ |
| 14 | ✅ **Klikalne adresy URL i IP** w terminalu (Ctrl+klik) | Tabby, Windows Terminal | S | ⭐⭐ |
| 15 | ✅ **Automatyczny zapis wszystkich sesji do plików** z rotacją | SecureCRT, MobaXterm | S | ⭐⭐ |
| 16 | **ZMODEM** (`rz`/`sz`) — plik przez sam terminal, bez SFTP | MobaXterm, WindTerm | M | ⭐ |
| 17 | **Edytor reguł podświetlania** w Ustawieniach (słowo → kolor) | SecureCRT | S | ⭐ |
| 18 | ✅ **Własne skróty klawiszowe** (makra pod skrótem) | wszystkie | S | ⭐⭐ |
| 19 | ✅ **Kolor środowiska na zakładce** — produkcja na czerwono + pytanie przed wysłaniem do prod | Royal TS | S | ⭐⭐⭐ |

### Bezpieczeństwo

| # | Propozycja | Na wzór | Wielkość | Priorytet |
|---|---|---|---|---|
| 20 | **Hasło główne / sejf** (najpierw plan — ryzyko utraty haseł) | Termius, Royal TS | L | ⭐⭐ |
| 21 | **Integracja z KeePass / Bitwarden** — hasło pobierane z menedżera, nie z naszego pliku | Royal TS, RDM | M | ⭐⭐ |
| 22 | ✅ **Agent SSH (Pageant/OpenSSH) i przekazywanie agenta** | wszystkie | S | ⭐⭐ |
| 23 | ✅ **Logowanie z kodem 2FA** (keyboard-interactive, TOTP) | SecureCRT, Termius | M | ⭐⭐ |
| 24 | ✅ **Okno zapamiętanych kluczy serwerów** — podgląd i usuwanie wpisów `known_hosts` | Bitvise | S | ⭐⭐ |
| 25 | **Nagrywanie sesji i dziennik audytu** (kto, gdzie, jakie polecenie) | WindTerm, RDM | M | ⭐⭐ |

### Monitoring i administracja

| # | Propozycja | Na wzór | Wielkość | Priorytet |
|---|---|---|---|---|
| 26 | ✅ **Wykres historii z monitoringu** po kliknięciu kafelka (dni/tygodnie) + eksport CSV | Zabbix (lekko) | S | ⭐⭐⭐ |
| 27 | ✅ **Alert „certyfikat TLS wygasa”** w monitoringu | — | S | ⭐⭐⭐ |
| 28 | ✅ **Alerty na zewnątrz** — e-mail, Teams, Slack, Telegram (webhook) | Uptime Kuma | M | ⭐⭐ |
| 29 | ✅ **Panel Docker** — kontenery, logi, restart, zużycie | Portainer (lekko) | M | ⭐⭐⭐ |
| 30 | ✅ **Panel aktualizacji** — ile pakietów czeka, „wymagany restart”, na całej grupie | — | M | ⭐⭐ |
| 31 | **Edytor zadań cron / Harmonogramu zadań Windows** | Webmin | M | ⭐ |
| 32 | **Podgląd zapory** (ufw/firewalld/Windows Firewall) | Webmin | M | ⭐ |
| 33 | ✅ **Wyszukiwanie w logach na wielu serwerach naraz** (grep po grupie, wynik per serwer) | — | M | ⭐⭐ |
| 34 | **Scenariusze na grupie serwerów** — kroki po kolei, stop przy błędzie, harmonogram | Ansible (lekko) | L | ⭐⭐ |
| 35 | **Asystent AI w terminalu** — wyjaśnij błąd/log, zaproponuj polecenie (wysyłka po kliknięciu) | Warp, Termius | M | ⭐⭐ |

### Pliki (SFTP)

| # | Propozycja | Na wzór | Wielkość | Priorytet |
|---|---|---|---|---|
| 36 | ~~**Przesyłanie całych folderów + wznawianie przerwanego transferu**~~ — zrobione 2026-10-06 (wznawianie na żądanie z kolejki) | WinSCP | M | ⭐⭐ |
| 37 | **Porównanie i synchronizacja katalogów** (lokalny ↔ zdalny) | WinSCP | L | ⭐ |
| 38 | ~~**Edytor uprawnień** (chmod/chown z okienkiem)~~ — zrobione 2026-10-06 (UID/GID liczbowo) | WinSCP | S | ⭐ |

### Wygoda i dystrybucja

| # | Propozycja | Na wzór | Wielkość | Priorytet |
|---|---|---|---|---|
| 39 | **Instalator `.exe` i aktualizacje bez gita**, wersja przenośna (pendrive) | wszystkie | M | ⭐⭐ |
| 40 | **Szyfrowana synchronizacja połączeń** (plik na OneDrive/Dropbox/Git) | Termius, Royal TS | M | ⭐⭐ |
| 41 | ~~**Szukanie w historii wszystkich otwartych terminali**~~ — zrobione 2026-10-06 | — | S | ⭐ |
| 42 | ~~**Przeciąganie zakładki do siatki, RDP w siatce, zapamiętany układ**~~ — zrobione 2026-10-06 | Tabby, MobaXterm | M | ⭐ |

## Rekomendowana kolejność (najpierw tanie i odczuwalne)

1. ~~#27 alert TLS + #26 wykres historii~~ — zrobione 2026-10-02, dni/tygodnie i CSV 2026-10-03.
2. ~~#19 kolor produkcji + pytanie przed wysłaniem~~ — zrobione 2026-10-04.
3. ~~#3 przywracanie sesji po starcie~~ — zrobione 2026-10-04 (siatka wraca jako zwykłe zakładki).
4. ~~#1 dziedziczenie ustawień z grupy~~ — zrobione 2026-10-04 (bez portu); ~~#2 import z innych programów~~ — zrobione 2026-10-04 (PuTTY, MobaXterm, mRemoteNG, CSV; bez RDCMan).
5. ~~#11 biblioteka poleceń + #18 skróty~~ — zrobione 2026-10-04 (zmienne `{{...}}`, skrót w `[ ]`; bez podziału na grupy).
6. ~~#29 panel Docker~~ — zrobione 2026-10-04 (bez hasła sudo i compose); ~~#30 panel aktualizacji~~ — zrobione 2026-10-04 (tylko odczyt, otwarte sesje).
   ~~#28 alerty na zewnątrz~~ — zrobione 2026-10-05 (Telegram; bez e-maila/Teams/Slacka).
   ~~#33 szukanie w logach na wielu serwerach~~ — zrobione 2026-10-05 (otwarte sesje, tylko Linux).
   ~~#23 logowanie z kodem 2FA~~ — zrobione 2026-10-05 (keyboard-interactive: hasło+kod, klucz+kod; bez zapisywania sekretu TOTP).
7. Dalej duże kierunki: #34 scenariusze, #35 AI, #20 sejf, #39 instalator, #25 audyt.

Poza kolejką zrobione 2026-10-03: #13, #14, #15, #22, #24 (drobne usprawnienia terminala i bezpieczeństwa).
