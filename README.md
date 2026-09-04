# ssh-rdp-manager

Desktopowy menedżer połączeń SSH/RDP w Pythonie (PySide6). Drzewo połączeń po lewej,
terminal SSH otwierany w zakładce po prawej.

> **Status: wczesny etap.** Terminal SSH działa i jest przetestowany end-to-end.
> RDP jeszcze nie jest podpięte, a połączenia nie zapisują się na dysk.

## Wymagania

- Python 3.11+
- PySide6, Paramiko

## Instalacja i uruchomienie

```bash
pip install -r requirements.txt
python main.py
```

Testy (bez sieci i bez okna):

```bash
python main.py --selftest
```

## Użycie

1. Prawy klik na drzewie → **Nowa grupa** / **Nowe połączenie**.
2. Przy połączeniu podaj host, port i użytkownika.
3. Dwuklik na połączeniu otwiera terminal w nowej zakładce.
4. Hasło jest pytane przy każdym łączeniu. **Puste hasło = logowanie kluczem SSH**
   (agent lub `~/.ssh`).

Interfejs jest domyślnie po **angielsku**; polski wybiera się w **Widok → Language**
(zmiana działa po ponownym uruchomieniu aplikacji).

## Bezpieczeństwo

- Hasła **nie są nigdzie zapisywane** — żyją tylko w pamięci na czas sesji.
- Nieznany klucz serwera pokazuje odcisk i wymaga potwierdzenia, zamiast być
  akceptowany automatycznie. To ochrona przed atakiem typu man-in-the-middle.

## Znane ograniczenia

- **Brak emulacji VT100.** Sekwencje ANSI są wycinane, więc zwykła powłoka wygląda
  dobrze, ale programy pełnoekranowe (`vim`, `htop`, `mc`) będą rozjechane.
- Nawiązywanie połączenia blokuje interfejs do 10 sekund.
- Połączenia znikają po restarcie aplikacji.
- Brak edycji istniejącego połączenia (można dodać i usunąć).

## Struktura

| Plik | Zawartość |
|---|---|
| `main.py` | Okno, drzewo połączeń, zakładki, formularz połączenia |
| `ssh_terminal.py` | Sesja SSH (Paramiko) jako widget zakładki |
| `i18n.py` | Napisy interfejsu po angielsku i po polsku |

## Licencja

MIT
