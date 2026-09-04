"""Teksty interfejsu w dwóch językach: angielski (domyślny) i polski.

Zwykły słownik zamiast `gettext`/`QTranslator` — te wymagają kompilowania
plików `.mo`/`.qm` przy każdej zmianie napisu. Przy dwóch językach i jednym
oknie to koszt bez zysku.

Wybór języka siedzi w `QSettings` (na Windows: rejestr), więc nie ma kolejnego
pliku do pilnowania. Zmiana działa po restarcie aplikacji — napisy są czytane
w momencie budowania okna, a przebudowa całego GUI zabiłaby otwarte sesje SSH.
"""

from PySide6.QtCore import QSettings

DEFAULT = "en"

# Kod języka -> nazwa w menu (zawsze w tym języku, nie tłumaczona).
LANGUAGES = {"en": "English", "pl": "Polski"}

_SETTINGS = ("Bochnovic", "SSH-RDP-Manager")

TEXTS = {
    "en": {
        # --- okno i formularz połączenia ---
        "app_title": "SSH/RDP Connection Manager",
        "dlg_ssh_connection": "SSH connection",
        "fld_name": "Name:",
        "fld_host": "Host:",
        "fld_port": "Port:",
        "fld_user": "Username:",
        "fld_password": "Password:",
        "chk_save_password": "Save password (encrypted with your Windows account)",
        "fld_protocol": "Protocol:",
        "fld_key_file": "Private key:",
        "btn_browse": "Browse…",
        "dlg_key_file": "Select a private key",
        "filter_all_files": "All files (*)",
        "dlg_rdp_connection": "RDP connection",
        "tip_save_password_windows_only": "Saving passwords works on Windows only.",
        "err_missing_data_title": "Missing data",
        "err_missing_host": "Enter the host address.",
        # --- drzewo połączeń ---
        "tree_header": "Connections",
        "tree_root": "All connections",
        "unnamed": "unnamed",
        "err_save_title": "Save error",
        "err_save_body": "Could not save connections:\n\n{0}",
        "err_load_title": "Load error",
        "err_load_body": "Could not load saved connections:\n\n{0}\n\n",
        "err_load_backup": "Copy of the damaged file: {0}",
        "err_not_a_list": "the file does not contain a list of connections",
        "menu_new_group": "New group",
        "menu_new_connection": "New connection",
        "menu_edit_connection": "Edit connection…",
        "menu_rename": "Rename…",
        "menu_icon": "Icon…",
        "menu_color": "Color…",
        "menu_no_color": "No color",
        "menu_delete": "Delete",
        "tip_password_saved": "\n(password saved)",
        "dlg_rename_title": "Rename",
        "dlg_group_name": "Group name:",
        "dlg_group_color": "Group color",
        "icon_none": "(no icon)",
        "dlg_icon_title": "Icon",
        "dlg_icon_prompt": "Choose an icon:",
        "confirm_delete_group_title": "Delete group?",
        "confirm_delete_group_body": "„{0}” contains {1} items. Delete everything?",
        # --- pulpit startowy ---
        "home_subtitle": "Pick a saved connection on the left, or:",
        "home_quick_btn": "➕ New temporary connection",
        "home_saved_btn": "💾 New saved connection",
        "home_search_placeholder": "🔍 Search connections (name, host, user)…",
        "tab_home": "🏠 Home",
        # --- pasek menu ---
        "menu_connection": "&Connection",
        "menu_new_group_dots": "New group…",
        "menu_new_connection_dots": "New connection…",
        "menu_export": "Export connections…",
        "menu_import": "Import connections…",
        "menu_quit": "Quit",
        "menu_view": "&View",
        "menu_connection_list": "Connection list",
        "menu_highlighting": "Syntax highlighting",
        "menu_language": "Language",
        "menu_servers": "Se&rvers",
        "menu_stop_all": "Stop all",
        "menu_scripts": "&Scripts",
        "menu_help": "Hel&p",
        "menu_about": "About…",
        "lang_restart": "The language will be applied after restarting the application.",
        # --- eksport i import ---
        "dlg_export_title": "Export connections",
        "export_default_name": "connections.json",
        "json_filter": "JSON (*.json)",
        "export_short": "Export",
        "err_export": "Could not save:\n\n{0}",
        "export_done": "Saved to:\n{0}\n\nSaved passwords are encrypted with your Windows"
                       " account — they cannot be read on another computer.",
        "dlg_import_title": "Import connections",
        "import_short": "Import",
        "import_question": "Replace the current connection list?\n\n"
                           "Yes = replace, No = append to the existing one.",
        "err_import": "Could not load:\n\n{0}",
        "import_done": "Branches loaded: {0}",
        # --- serwery wbudowane ---
        "srv_http": "HTTP server…",
        "srv_tftp": "TFTP server…",
        "srv_stopped_one": "{0}: stopped",
        "srv_dir_prompt": "Directory to share",
        "srv_start_error": "Could not start on port {0}:\n\n{1}\n\n"
                           "Ports below 1024 require administrator rights.",
        "srv_running": "Server is running.\n\nAddress: {0}\nDirectory: {1}",
        "srv_all_stopped": "Servers stopped",
        # --- skrypty administracyjne ---
        "scripts_short": "Scripts",
        "scripts_need_session": "Open an SSH connection first.",
        "script_top": "Top processes (CPU/RAM)",
        "script_disk": "Disk space",
        "script_errors": "Recent errors in logs",
        "script_ports": "Listening ports",
        "script_restart": "Restart service…",
        "script_restart_prompt": "Service name:",
        "script_updates": "Available / recent updates",
        "script_vacuum": "Clean old logs (7 days)",
        "script_failed_logins": "Failed SSH logins",
        "script_who": "Who is logged in",
        "script_ping": "Ping a host…",
        "script_ping_prompt": "Host to check:",
        "script_connections": "Active network connections",
        "script_no_output": "(no output)",
        "script_failed": "Could not run the script on this server.",
        # --- pasek boczny, okno „o programie", pasek stanu ---
        "sidebar": "Sidebar",
        "about_title": "About",
        "about_body": "SSH/RDP Connection Manager\nPython + PySide6",
        "status_idle": "No active connection",
        "dlg_auth_title": "Authentication",
        "dlg_auth_body": "Password for {0}@{1}\n(empty = SSH key):",
        "dlg_quick_title": "New temporary connection",
        # --- terminal i łączenie ---
        "find_placeholder": "Search…",
        "find_prev": "Previous (Shift+Enter)",
        "find_next": "Next (Enter)",
        "find_close": "Close (Esc)",
        "find_menu": "Find…\tCtrl+F",
        "wait_text": "Waiting: {0} s (limit {1} s)",
        "hostkey_rejected": "Server key for {0} was rejected",
        "hostkey_title": "Unknown server key",
        "hostkey_body": "Server {0} presented an unknown {1} key:\n\n{2}\n\n"
                        "Accept only if this fingerprint matches — otherwise the\n"
                        "connection may be intercepted. Continue?",
        "connecting": "Connecting to {0}…",
        "cancel": "Cancel",
        "connecting_title": "SSH connection",
        "err_connect_title": "Connection error",
        "err_connect_body": "Could not connect:\n\n{0}",
        "stats_disk": "disk",
        "stats_free": "free",
        "stats_uptime": "uptime",
        "stats_users": "users",
        "stats_unavailable": "Statistics unavailable for this server",
        "session_closed": "[session closed]",
        "decimal_sep": ".",
        # --- RDP ---
        "rdp_connecting": "Connecting to {0}…",
        "rdp_disconnected": "RDP session ended (code {0}).",
        "rdp_needs_windows": "RDP works on Windows only.",
        "rdp_no_control": "The built-in RDP control could not be started, so the session"
                          " will open in a separate mstsc.exe window.",
        "rdp_mstsc_failed": "Could not start mstsc.exe:\n\n{0}",
        # --- transfer plików ---
        "transfer_download": "Downloading {0}…",
        "transfer_upload": "Sending {0}…",
        # --- panel SFTP ---
        "sftp_back": "Back",
        "sftp_forward": "Forward",
        "sftp_up": "Parent folder",
        "sftp_refresh": "Refresh",
        "sftp_new_folder": "New folder",
        "sftp_upload": "Upload file",
        "sftp_unavailable": "SFTP unavailable for this server",
        "err_prefix": "Error: {0}",
        "sftp_download_title": "Download file",
        "sftp_download": "Download",
        "err_download": "Download error",
        "err_upload": "Upload error",
        "err_delete": "Delete error",
        "err_generic": "Error",
        "lbl_name": "Name:",
        "confirm_delete_title": "Delete?",
        "confirm_delete_body": "Delete „{0}”?",
    },
    "pl": {
        # --- okno i formularz połączenia ---
        "app_title": "Menedżer połączeń SSH/RDP",
        "dlg_ssh_connection": "Połączenie SSH",
        "fld_name": "Nazwa:",
        "fld_host": "Host:",
        "fld_port": "Port:",
        "fld_user": "Użytkownik:",
        "fld_password": "Hasło:",
        "chk_save_password": "Zapisz hasło (szyfrowane kontem Windows)",
        "fld_protocol": "Protokół:",
        "fld_key_file": "Klucz prywatny:",
        "btn_browse": "Wybierz…",
        "dlg_key_file": "Wskaż klucz prywatny",
        "filter_all_files": "Wszystkie pliki (*)",
        "dlg_rdp_connection": "Połączenie RDP",
        "tip_save_password_windows_only": "Zapis hasła działa tylko na Windows.",
        "err_missing_data_title": "Brak danych",
        "err_missing_host": "Podaj adres hosta.",
        # --- drzewo połączeń ---
        "tree_header": "Połączenia",
        "tree_root": "Wszystkie połączenia",
        "unnamed": "bez nazwy",
        "err_save_title": "Błąd zapisu",
        "err_save_body": "Nie udało się zapisać połączeń:\n\n{0}",
        "err_load_title": "Błąd odczytu",
        "err_load_body": "Nie udało się wczytać zapisanych połączeń:\n\n{0}\n\n",
        "err_load_backup": "Kopia uszkodzonego pliku: {0}",
        "err_not_a_list": "plik nie zawiera listy połączeń",
        "menu_new_group": "Nowa grupa",
        "menu_new_connection": "Nowe połączenie",
        "menu_edit_connection": "Edytuj połączenie…",
        "menu_rename": "Zmień nazwę…",
        "menu_icon": "Ikona…",
        "menu_color": "Kolor…",
        "menu_no_color": "Bez koloru",
        "menu_delete": "Usuń",
        "tip_password_saved": "\n(hasło zapisane)",
        "dlg_rename_title": "Zmień nazwę",
        "dlg_group_name": "Nazwa grupy:",
        "dlg_group_color": "Kolor grupy",
        "icon_none": "(bez ikony)",
        "dlg_icon_title": "Ikona",
        "dlg_icon_prompt": "Wybierz ikonę:",
        "confirm_delete_group_title": "Usunąć grupę?",
        "confirm_delete_group_body": "„{0}” zawiera {1} elementów. Usunąć wszystko?",
        # --- pulpit startowy ---
        "home_subtitle": "Wybierz zapisane połączenie po lewej albo:",
        "home_quick_btn": "➕ Nowe połączenie tymczasowe",
        "home_saved_btn": "💾 Nowe zapisane połączenie",
        "home_search_placeholder": "🔍 Szukaj połączenia (nazwa, host, użytkownik)…",
        "tab_home": "🏠 Start",
        # --- pasek menu ---
        "menu_connection": "&Połączenie",
        "menu_new_group_dots": "Nowa grupa…",
        "menu_new_connection_dots": "Nowe połączenie…",
        "menu_export": "Eksportuj połączenia…",
        "menu_import": "Importuj połączenia…",
        "menu_quit": "Zakończ",
        "menu_view": "&Widok",
        "menu_connection_list": "Lista połączeń",
        "menu_highlighting": "Podświetlanie składni",
        "menu_language": "Język",
        "menu_servers": "Se&rwery",
        "menu_stop_all": "Zatrzymaj wszystkie",
        "menu_scripts": "&Skrypty",
        "menu_help": "Pomo&c",
        "menu_about": "O programie…",
        "lang_restart": "Język zmieni się po ponownym uruchomieniu aplikacji.",
        # --- eksport i import ---
        "dlg_export_title": "Eksport połączeń",
        "export_default_name": "polaczenia.json",
        "json_filter": "JSON (*.json)",
        "export_short": "Eksport",
        "err_export": "Nie udało się zapisać:\n\n{0}",
        "export_done": "Zapisano do:\n{0}\n\nZapisane hasła są szyfrowane kontem Windows —"
                       " na innym komputerze nie dadzą się odczytać.",
        "dlg_import_title": "Import połączeń",
        "import_short": "Import",
        "import_question": "Zastąpić obecną listę połączeń?\n\n"
                           "Tak = zastąp, Nie = dopisz do istniejącej.",
        "err_import": "Nie udało się wczytać:\n\n{0}",
        "import_done": "Wczytano gałęzi: {0}",
        # --- serwery wbudowane ---
        "srv_http": "Serwer HTTP…",
        "srv_tftp": "Serwer TFTP…",
        "srv_stopped_one": "{0}: zatrzymany",
        "srv_dir_prompt": "Katalog do udostępnienia",
        "srv_start_error": "Nie udało się uruchomić na porcie {0}:\n\n{1}\n\n"
                           "Porty poniżej 1024 wymagają uprawnień administratora.",
        "srv_running": "Serwer działa.\n\nAdres: {0}\nKatalog: {1}",
        "srv_all_stopped": "Serwery zatrzymane",
        # --- skrypty administracyjne ---
        "scripts_short": "Skrypty",
        "scripts_need_session": "Otwórz najpierw połączenie SSH.",
        "script_top": "Top procesów (CPU/RAM)",
        "script_disk": "Miejsce na dyskach",
        "script_errors": "Ostatnie błędy w logach",
        "script_ports": "Nasłuchujące porty",
        "script_restart": "Restart usługi…",
        "script_restart_prompt": "Nazwa usługi:",
        "script_updates": "Dostępne / ostatnie aktualizacje",
        "script_vacuum": "Czyszczenie starych logów (7 dni)",
        "script_failed_logins": "Nieudane logowania SSH",
        "script_who": "Kto jest zalogowany",
        "script_ping": "Ping hosta…",
        "script_ping_prompt": "Host do sprawdzenia:",
        "script_connections": "Aktywne połączenia sieciowe",
        "script_no_output": "(brak wyniku)",
        "script_failed": "Nie udało się uruchomić skryptu na tym serwerze.",
        # --- pasek boczny, okno „o programie", pasek stanu ---
        "sidebar": "Pasek boczny",
        "about_title": "O programie",
        "about_body": "Menedżer połączeń SSH/RDP\nPython + PySide6",
        "status_idle": "Brak aktywnego połączenia",
        "dlg_auth_title": "Uwierzytelnianie",
        "dlg_auth_body": "Hasło dla {0}@{1}\n(puste = klucz SSH):",
        "dlg_quick_title": "Nowe połączenie tymczasowe",
        # --- terminal i łączenie ---
        "find_placeholder": "Szukaj…",
        "find_prev": "Poprzednie (Shift+Enter)",
        "find_next": "Następne (Enter)",
        "find_close": "Zamknij (Esc)",
        "find_menu": "Znajdź…\tCtrl+F",
        "wait_text": "Czas oczekiwania: {0} s (limit {1} s)",
        "hostkey_rejected": "Odrzucono klucz serwera {0}",
        "hostkey_title": "Nieznany klucz serwera",
        "hostkey_body": "Serwer {0} przedstawił nieznany klucz {1}:\n\n{2}\n\n"
                        "Zaakceptuj tylko jeśli ten odcisk się zgadza — inaczej\n"
                        "połączenie może być przechwytywane. Kontynuować?",
        "connecting": "Łączenie z {0}…",
        "cancel": "Anuluj",
        "connecting_title": "Łączenie SSH",
        "err_connect_title": "Błąd połączenia",
        "err_connect_body": "Nie udało się połączyć:\n\n{0}",
        "stats_disk": "dysk",
        "stats_free": "wolne",
        "stats_uptime": "uptime",
        "stats_users": "zalogowani",
        "stats_unavailable": "Statystyki niedostępne dla tego serwera",
        "session_closed": "[sesja zakończona]",
        "decimal_sep": ",",
        # --- RDP ---
        "rdp_connecting": "Łączenie z {0}…",
        "rdp_disconnected": "Sesja RDP zakończona (kod {0}).",
        "rdp_needs_windows": "RDP działa tylko na Windows.",
        "rdp_no_control": "Nie udało się uruchomić wbudowanej kontrolki RDP, więc sesja"
                          " otworzy się w osobnym oknie mstsc.exe.",
        "rdp_mstsc_failed": "Nie udało się uruchomić mstsc.exe:\n\n{0}",
        # --- transfer plików ---
        "transfer_download": "Pobieranie {0}…",
        "transfer_upload": "Wysyłanie {0}…",
        # --- panel SFTP ---
        "sftp_back": "Wstecz",
        "sftp_forward": "Do przodu",
        "sftp_up": "Do folderu nadrzędnego",
        "sftp_refresh": "Odśwież",
        "sftp_new_folder": "Nowy folder",
        "sftp_upload": "Wyślij plik",
        "sftp_unavailable": "SFTP niedostępne dla tego serwera",
        "err_prefix": "Błąd: {0}",
        "sftp_download_title": "Pobierz plik",
        "sftp_download": "Pobierz",
        "err_download": "Błąd pobierania",
        "err_upload": "Błąd wysyłania",
        "err_delete": "Błąd usuwania",
        "err_generic": "Błąd",
        "lbl_name": "Nazwa:",
        "confirm_delete_title": "Usunąć?",
        "confirm_delete_body": "Usunąć „{0}”?",
    },
}

_current = DEFAULT


def language():
    return _current


def use(code):
    """Ustawia język tylko na czas działania (bez zapisu) — używane w testach."""
    global _current
    _current = code if code in TEXTS else DEFAULT


def settings():
    """Wspólny `QSettings` aplikacji — jedno miejsce, w którym siedzi nazwa klucza."""
    return QSettings(*_SETTINGS)


def load():
    """Wczytuje zapisany wybór; brak wpisu = angielski."""
    use(settings().value("language", DEFAULT))


def save(code):
    use(code)
    settings().setValue("language", _current)


def t(key, *args):
    """Napis dla bieżącego języka; brak tłumaczenia spada na angielski."""
    text = TEXTS[_current].get(key) or TEXTS[DEFAULT].get(key, key)
    return text.format(*args) if args else text


def selftest():
    use("en")
    assert t("cancel") == "Cancel"
    assert t("import_done", 3) == "Branches loaded: 3"
    use("pl")
    assert t("cancel") == "Anuluj"
    assert t("import_done", 3) == "Wczytano gałęzi: 3"
    use("klingon")
    assert language() == DEFAULT, "nieznany kod ma spaść na domyślny"
    # Oba słowniki muszą mieć te same klucze — inaczej jeden język gubi napisy.
    assert set(TEXTS["en"]) == set(TEXTS["pl"]), set(TEXTS["en"]) ^ set(TEXTS["pl"])
    print("i18n selftest OK")


if __name__ == "__main__":
    selftest()
