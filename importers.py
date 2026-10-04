"""Import połączeń z innych programów: PuTTY, MobaXterm, mRemoteNG, CSV.

Każdy czytnik oddaje listę `(folder, połączenie)`, gdzie folder to krotka nazw
podgrup, a `nodes()` składa z tego węzły w formacie `connections.json`.
Hasła celowo pomijamy: PuTTY ich nie trzyma, a MobaXterm/mRemoteNG trzymają
je zaszyfrowane własnym kluczem — po imporcie pyta się o nie jak zwykle.
"""

import csv
import io
import xml.etree.ElementTree as ET
from configparser import ConfigParser
from urllib.parse import unquote

SSH_PORT, RDP_PORT = 22, 3389


def _conn(name, host, port, username, protocol):
    default = RDP_PORT if protocol == "rdp" else SSH_PORT
    try:
        port = int(port or default)
    except ValueError:
        port = default
    return {"name": name or host, "host": host, "port": port,
            "username": username or "", "protocol": protocol}


def nodes(items):
    """[(folder, połączenie)] -> drzewo węzłów jak w `connections.json`."""
    top = []
    groups = {(): top}
    for folder, conn in items:
        for depth in range(1, len(folder) + 1):
            path = folder[:depth]
            if path not in groups:
                children = []
                groups[path[:-1]].append({"name": path[-1], "children": children})
                groups[path] = children
        groups[tuple(folder)].append({"name": conn["name"], "connection": conn})
    return top


# --- PuTTY (rejestr) ----------------------------------------------------------

PUTTY_KEY = r"Software\SimonTatham\PuTTY\Sessions"


def parse_putty(sessions):
    """{nazwa sesji z rejestru: {wartość: dane}} -> [(folder, połączenie)].

    Nazwy w rejestrze są zakodowane jak w URL („web%2001”). Sesja „Default
    Settings” i sesje bez hosta / nie-SSH (telnet, serial) odpadają. Plik klucza
    `.ppk` pomijamy — Paramiko go nie czyta (trzeba przekonwertować w PuTTYgen).
    """
    items = []
    for raw_name, values in sessions.items():
        name = unquote(raw_name)
        host = (values.get("HostName") or "").strip()
        if name == "Default Settings" or not host or values.get("Protocol", "ssh") != "ssh":
            continue
        user, _, plain_host = host.rpartition("@")
        items.append(((), _conn(name, plain_host, values.get("PortNumber"),
                                values.get("UserName") or user, "ssh")))
    return items


def read_putty():
    import winreg

    sessions = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PUTTY_KEY)
    except OSError:
        return []  # PuTTY nigdy nie zapisał sesji
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            values = {}
            with winreg.OpenKey(root, name) as key:
                for field in ("HostName", "PortNumber", "UserName", "Protocol"):
                    try:
                        values[field] = winreg.QueryValueEx(key, field)[0]
                    except OSError:
                        pass
            sessions[name] = values
    return parse_putty(sessions)


# --- MobaXterm (.mxtsessions albo MobaXterm.ini) --------------------------------

MOBA_TYPES = {"0": "ssh", "4": "rdp"}  # reszta (telnet, VNC, FTP...) nie ma u nas typu


def parse_mobaxterm(text):
    """Sekcje `[Bookmarks]`, `[Bookmarks_1]`...: `SubRep` = folder (z `\\`),
    wpis `nazwa=#ikona#typ%host%port%login%...`."""
    parser = ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    parser.optionxform = str  # nazwy sesji z wielkimi literami
    parser.read_string(text)
    items = []
    for section in parser.sections():
        if not section.lower().startswith("bookmarks"):
            continue
        folder = tuple(p for p in parser[section].get("SubRep", "").split("\\") if p)
        for name, value in parser[section].items():
            if name in ("SubRep", "ImgNum") or not value.startswith("#"):
                continue
            head, _, rest = value.partition("%")
            kind = head.split("#")[2] if head.count("#") >= 2 else ""
            fields = rest.split("%")
            if kind not in MOBA_TYPES or not fields[0]:
                continue
            items.append((folder, _conn(name.strip(), fields[0],
                                        fields[1] if len(fields) > 1 else "",
                                        fields[2] if len(fields) > 2 else "",
                                        MOBA_TYPES[kind])))
    return items


# --- mRemoteNG (confCons.xml) -----------------------------------------------------

MREMOTE_TYPES = {"SSH2": "ssh", "SSH1": "ssh", "RDP": "rdp"}


def parse_mremoteng(text):
    root = ET.fromstring(text)
    if root.get("FullFileEncryption", "").lower() == "true":
        raise ValueError("plik mRemoteNG jest w całości zaszyfrowany — zapisz go bez "
                         "szyfrowania całego pliku (Opcje → Zabezpieczenia) i spróbuj ponownie")
    items = []

    def walk(element, folder):
        for node in element.findall("Node"):
            if node.get("Type") == "Container":
                walk(node, folder + (node.get("Name", ""),))
            elif node.get("Type") == "Connection":
                protocol = MREMOTE_TYPES.get(node.get("Protocol", ""))
                if protocol and node.get("Hostname"):
                    items.append((folder, _conn(node.get("Name"), node.get("Hostname"),
                                                node.get("Port"), node.get("Username"), protocol)))

    walk(root, ())
    return items


# --- CSV ----------------------------------------------------------------------------


def parse_csv(text):
    """Nagłówek z kolumnami (dowolna kolejność, wielkość liter bez znaczenia):
    name, host, port, username, protocol, group (podgrupy przez „/”).
    Separator „;” albo „,” — jak zapisze Excel."""
    sample = text.split("\n", 1)[0]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=delimiter)
    items = []
    for row in rows:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("host"):
            continue
        protocol = "rdp" if row.get("protocol", "").lower() == "rdp" else "ssh"
        folder = tuple(p.strip() for p in row.get("group", "").split("/") if p.strip())
        items.append((folder, _conn(row.get("name"), row["host"], row.get("port"),
                                    row.get("username"), protocol)))
    return items


def selftest():
    putty = parse_putty({
        "web%2001": {"HostName": "admin@10.0.0.1", "PortNumber": 2222, "Protocol": "ssh"},
        "baza": {"HostName": "db.local", "UserName": "root"},
        "Default Settings": {"HostName": "x"},
        "router": {"HostName": "10.0.0.254", "Protocol": "telnet"},
    })
    assert putty == [
        ((), {"name": "web 01", "host": "10.0.0.1", "port": 2222, "username": "admin", "protocol": "ssh"}),
        ((), {"name": "baza", "host": "db.local", "port": 22, "username": "root", "protocol": "ssh"}),
    ], putty

    moba = parse_mobaxterm(
        "[Bookmarks]\nSubRep=\nImgNum=42\n"
        "web01=#109#0%10.0.0.1%22%admin%%-1%-1%%%%%0%0%0%%%-1%0%0%0%%1080%%0%0%1\n"
        "[Bookmarks_1]\nSubRep=Klient\\Windows\nImgNum=41\n"
        "AD=#91#4%10.0.0.5%%Administrator%%-1%-1\n"
        "vnc=#128#5%10.0.0.6%5900%%\n"
    )
    assert moba == [
        ((), {"name": "web01", "host": "10.0.0.1", "port": 22, "username": "admin", "protocol": "ssh"}),
        (("Klient", "Windows"), {"name": "AD", "host": "10.0.0.5", "port": 3389,
                                 "username": "Administrator", "protocol": "rdp"}),
    ], moba

    xml = """<?xml version="1.0"?><mrng:Connections xmlns:mrng="http://mremoteng.org" Name="Connections">
      <Node Name="Prod" Type="Container">
        <Node Name="web" Type="Connection" Protocol="SSH2" Hostname="10.1.0.1" Port="22" Username="deploy"/>
        <Node Name="dc" Type="Connection" Protocol="RDP" Hostname="10.1.0.2" Port="3389" Username=""/>
        <Node Name="www" Type="Connection" Protocol="HTTP" Hostname="10.1.0.3"/>
      </Node></mrng:Connections>"""
    mremote = parse_mremoteng(xml)
    assert [(f, c["name"], c["protocol"]) for f, c in mremote] == [
        (("Prod",), "web", "ssh"), (("Prod",), "dc", "rdp")], mremote
    try:
        parse_mremoteng('<Connections FullFileEncryption="true"/>')
        raise AssertionError("zaszyfrowany plik ma dać czytelny błąd")
    except ValueError:
        pass

    rows = parse_csv("﻿Name;Host;Port;Username;Protocol;Group\n"
                     "web;10.0.0.1;;admin;;Klient/Prod\nad;10.0.0.5;;;RDP;\n;;;;;\n")
    assert rows == [
        (("Klient", "Prod"), {"name": "web", "host": "10.0.0.1", "port": 22, "username": "admin", "protocol": "ssh"}),
        ((), {"name": "ad", "host": "10.0.0.5", "port": 3389, "username": "", "protocol": "rdp"}),
    ], rows
    assert parse_csv("host,name\nh1,a\n")[0][1]["name"] == "a", "separator „,”"

    tree = nodes(rows + [(("Klient",), _conn("x", "h", "", "", "ssh"))])
    assert [n["name"] for n in tree] == ["Klient", "ad"]
    klient = tree[0]["children"]
    assert klient[0]["name"] == "Prod" and klient[0]["children"][0]["connection"]["host"] == "10.0.0.1"
    assert klient[1]["connection"]["name"] == "x"
    print("importers selftest OK")


if __name__ == "__main__":
    selftest()
