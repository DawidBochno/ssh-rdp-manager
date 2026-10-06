"""Graficzna przeglądarka plików (SFTP) — panel po lewej stronie zakładki sesji.

Każda operacja na kanale SFTP (otwarcie, `listdir`, `mkdir`, `rename`, `chmod`,
kasowanie) idzie przez jeden wątek roboczy panelu (`_run`), a wynik wraca
sygnałem na wątek GUI — wolny serwer nie zamraża już okna. Jeden wątek, bo
Paramiko nie obiecuje, że jeden `SFTPClient` zniesie dwa żądania naraz;
kolejka zadań to przy okazji zachowana kolejność (`mkdir`, potem `listdir`).
Transfery plików mają własne kanały (`transfers.py`).
"""

import os
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paramiko
from PySide6.QtCore import QFileSystemWatcher, QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from i18n import t
from transfers import TransferQueue, run_transfer


def open_sftp(client):
    """Nowy kanał SFTP na transporcie sesji — runda po sieci, więc nie z GUI."""
    return paramiko.SFTPClient.from_transport(client.get_transport())


def sorted_entries(entries):
    """Katalogi przed plikami, potem alfabetycznie bez wielkości liter."""
    return sorted(entries, key=lambda e: (not stat.S_ISDIR(e.st_mode), e.filename.lower()))


class _SftpListWidget(QListWidget):
    """Lista plików SFTP z drag-out: przeciągnięcie pliku do Eksploratora.

    Qt nie umie "pobrać w trakcie przeciągania", więc plik idzie na dysk
    (blokująco, jak przy edycji zdalnego pliku) *przed* startem `QDrag` —
    inaczej Eksplorator dostałby ścieżkę do niczego.
    """

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        entry = item.data(Qt.UserRole) if item else None
        if entry is None or not self.panel.sftp:
            return
        name, is_dir = entry
        if is_dir:
            return  # ponytail: folder trzeba by pobrać cały przed QDrag — tylko z menu „Pobierz”
        local_path = Path(tempfile.mkdtemp(prefix="sshrdp_drag_")) / name
        error = self.panel._transfer("get", self.panel._child_path(name), local_path,
                                     t("transfer_download", name))
        if error:
            QMessageBox.warning(self.panel, t("err_download"), error)
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(local_path))])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class SftpPanel(QWidget):
    """Panel plików po lewej stronie zakładki sesji — wzorem MobaXterm."""

    # (callback, wynik, błąd) — emitowany z wątku roboczego, odbierany na GUI.
    _done = Signal(object, object, object)

    def __init__(self, client, parent=None, bookmarks=None, on_change=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.sftp = None
        self.path = None  # None = jeszcze nie wiemy, gdzie jest katalog domowy
        # Obserwatorzy plików otwartych do edycji — referencja musi przeżyć,
        # inaczej Python sprzątnie `QFileSystemWatcher` i sygnał nigdy nie przyjdzie.
        self._editors = []
        # Lista zakładek jest tą samą listą, co w danych połączenia — dopisanie
        # tutaj wystarczy, żeby `on_change` zrzuciło ją do connections.json.
        self.bookmarks = bookmarks if bookmarks is not None else []
        self.on_change = on_change
        self.client = client
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sftp")
        self._closed = False
        self._done.connect(lambda callback, result, error: callback(result, error))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # Historia jak w przeglądarce: gdzie byliśmy (back) i dokąd cofnęliśmy (forward).
        self._back, self._forward = [], []

        toolbar = QHBoxLayout()
        for text, tooltip, handler in (
            ("◀", t("sftp_back"), self._go_back),
            ("▶", t("sftp_forward"), self._go_forward),
            ("⬆", t("sftp_up"), self._go_up),
            ("🔄", t("sftp_refresh"), self.refresh),
            ("📁+", t("sftp_new_folder"), self._new_folder),
            ("📤", t("sftp_upload"), self._upload),
            ("📂📤", t("sftp_upload_folder"), self._upload_folder),
        ):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tooltip)
            button.clicked.connect(handler)
            toolbar.addWidget(button)

        # Zakładki katalogów: /var/log, /etc/nginx — per połączenie.
        self.bookmark_button = QToolButton()
        self.bookmark_button.setText("⭐")
        self.bookmark_button.setToolTip(t("sftp_bookmarks"))
        self.bookmark_button.clicked.connect(self._bookmark_menu)
        toolbar.addWidget(self.bookmark_button)
        layout.addLayout(toolbar)

        self.path_edit = QLineEdit()
        self.path_edit.returnPressed.connect(self._go_to_typed_path)
        layout.addWidget(self.path_edit)

        self.list = _SftpListWidget(self)
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list)

        # Wolne miejsce na dysku — dane liczy już _StatsPoller (dolny pasek),
        # panel tylko je wyświetla, żadnego własnego odpytywania.
        self.disk_label = QLabel("")
        layout.addWidget(self.disk_label)

        # Kolejka pobierania/wysyłania — `self.client` czytany w chwili otwarcia,
        # więc po ponownym połączeniu bierze już nowy transport.
        self.queue = TransferQueue(lambda: open_sftp(self.client), self)
        self.queue.upload_finished.connect(self.refresh)
        layout.addWidget(self.queue)

        self._connect(client)

    # --- wątek roboczy ----------------------------------------------------

    def _run(self, work, on_done):
        """`work()` na wątku panelu, potem `on_done(wynik, błąd)` na wątku GUI."""
        if self._closed:
            return

        def job():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            try:
                self._done.emit(on_done, result, error)
            except RuntimeError:
                pass  # panel skasowany, zanim serwer odpowiedział

        self._pool.submit(job)

    def _call(self, work, title="err_generic"):
        """Zmiana na serwerze (mkdir/rename/...): błąd w okienku, potem odświeżenie."""
        def done(_result, error):
            if error:
                QMessageBox.warning(self, t(title), str(error))
            self.refresh()

        self._run(work, done)

    def _transfer(self, mode, remote_path, local_path, title):
        """Blokujący transfer (edycja, drag-out) na osobnym kanale, nie na `self.sftp`."""
        return run_transfer(self, lambda: open_sftp(self.client), mode, remote_path,
                            str(local_path), title)

    # --- połączenie -------------------------------------------------------

    def _set_enabled(self, enabled):
        self.path_edit.setEnabled(enabled)
        self.list.setEnabled(enabled)

    def _connect(self, client):
        self._set_enabled(False)
        self.list.clear()
        self.list.addItem(t("sftp_loading"))

        def work():
            sftp = open_sftp(client)
            return sftp, sftp.normalize(".")

        self._run(work, self._connected)

    def _connected(self, result, error):
        if self._closed:
            if result:
                result[0].close()  # kanał otwarty, gdy zakładka już się zamykała
            return
        if error:
            self.sftp = None
            self.list.clear()
            self.list.addItem(t("sftp_unavailable"))
            return
        self.sftp, home = result
        if self.path is None:
            self.path = home  # po ponownym połączeniu zostaje dotychczasowa ścieżka
        self._set_enabled(True)
        self.refresh()

    def set_client(self, client):
        """Nowy kanał SFTP po ponownym połączeniu — ścieżka zostaje ta sama."""
        self.client = client
        self.queue.reset_sftp()
        self.sftp = None
        self._connect(client)

    def set_disk_stats(self, current):
        """Wpięte pod `SshTerminal.disk_changed` — None dopóki nie ma pierwszej próbki."""
        from ssh_terminal import human_bytes  # ssh_terminal importuje ten moduł

        if current is None:
            self.disk_label.setText("")
        else:
            self.disk_label.setText(
                t("sftp_free_space", human_bytes(current["disk_free"]), f"{current['disk_pct']:.0f}")
            )

    # --- nawigacja ------------------------------------------------------

    def refresh(self):
        if not self.sftp:
            return
        path, sftp = self.path, self.sftp
        # Stara lista znika od razu: jej pozycje należą do poprzedniego
        # katalogu, a `_child_path` składa ścieżkę już z nowego.
        self.list.clear()
        self.list.addItem(t("sftp_loading"))
        self.path_edit.setText(path)
        self._run(lambda: sorted_entries(sftp.listdir_attr(path)),
                  lambda entries, error: self._show_entries(path, entries, error))

    def _show_entries(self, path, entries, error):
        if path != self.path:
            return  # w międzyczasie przeszliśmy dalej — przyjdzie nowsza lista
        self.list.clear()
        if error:
            self.list.addItem(t("err_prefix", error))
            return
        for entry in entries:
            is_dir = stat.S_ISDIR(entry.st_mode)
            item = QListWidgetItem(f"{'📁' if is_dir else '📄'} {entry.filename}")
            item.setData(Qt.UserRole, (entry.filename, is_dir))
            self.list.addItem(item)

    def _child_path(self, name):
        return self.path.rstrip("/") + "/" + name

    def _navigate(self, path):
        if (path or "/") != self.path:
            self._back.append(self.path)
            self._forward.clear()
        self.path = path or "/"
        self.refresh()

    def _go_back(self):
        if self._back:
            self._forward.append(self.path)
            self.path = self._back.pop()
            self.refresh()

    def _go_forward(self):
        if self._forward:
            self._back.append(self.path)
            self.path = self._forward.pop()
            self.refresh()

    def _go_up(self):
        if self.sftp:
            self._navigate(self.path.rsplit("/", 1)[0] or "/")

    def _go_to_typed_path(self):
        self._navigate(self.path_edit.text().strip())

    # --- zakładki katalogów ------------------------------------------------

    def _bookmark_menu(self):
        menu = QMenu(self)
        for path in self.bookmarks:
            menu.addAction(path, lambda p=path: self._navigate(p))
        if not self.bookmarks:
            menu.addAction(t("sftp_no_bookmarks")).setEnabled(False)
        menu.addSeparator()
        if self.path in self.bookmarks:
            menu.addAction(t("sftp_del_bookmark"), self._remove_bookmark)
        elif self.path:
            menu.addAction(t("sftp_add_bookmark"), self._add_bookmark)
        button = self.bookmark_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _add_bookmark(self):
        if self.path not in self.bookmarks:
            self.bookmarks.append(self.path)
            if self.on_change:
                self.on_change()

    def _remove_bookmark(self):
        if self.path in self.bookmarks:
            self.bookmarks.remove(self.path)
            if self.on_change:
                self.on_change()

    def _open_item(self, item):
        entry = item.data(Qt.UserRole)
        if entry is None:
            return  # „Wczytywanie…” albo komunikat błędu
        name, is_dir = entry
        if is_dir:
            self._navigate(self._child_path(name))
        else:
            self._download(self._child_path(name), name)

    # --- przeciąganie plików z Eksploratora (upload) -----------------------

    def dragEnterEvent(self, event):
        if self.sftp and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.sftp and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.sftp:
            return
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path and Path(local_path).exists():
                self.queue.add("put", self._child_path(Path(local_path).name), local_path,
                               Path(local_path).is_dir())
        event.acceptProposedAction()

    # --- edycja pliku w lokalnym edytorze -----------------------------------

    def _edit(self, remote_path, name):
        """Pobiera plik do temp, otwiera domyślnym programem i odsyła po zapisie."""
        if not self.sftp:
            return
        local_path = Path(tempfile.mkdtemp(prefix="sshrdp_edit_")) / name
        error = self._transfer("get", remote_path, local_path, t("transfer_download", name))
        if error:
            QMessageBox.warning(self, t("err_download"), error)
            return
        try:
            os.startfile(str(local_path))  # ponytail: Windows-only, jak reszta aplikacji
        except OSError as error:
            QMessageBox.warning(self, t("err_generic"), str(error))
            return
        watcher = QFileSystemWatcher([str(local_path)], self)
        watcher.fileChanged.connect(
            lambda path, r=remote_path, l=local_path, w=watcher: self._upload_edited(r, l, w)
        )
        self._editors.append(watcher)

    def _upload_edited(self, remote_path, local_path, watcher):
        error = self._transfer("put", remote_path, local_path, t("transfer_upload", local_path.name))
        if error:
            QMessageBox.warning(self, t("err_upload"), error)
        # Niektóre edytory zapisują przez podmianę pliku — ścieżka wypada
        # z obserwacji, trzeba ją dodać ponownie.
        if str(local_path) not in watcher.files():
            watcher.addPath(str(local_path))

    # --- akcje na plikach -------------------------------------------------

    def _download(self, remote_path, name):
        local_path, _ = QFileDialog.getSaveFileName(self, t("sftp_download_title"), name)
        if local_path:
            self.queue.add("get", remote_path, local_path)

    def _download_folder(self, remote_path, name):
        target = QFileDialog.getExistingDirectory(self, t("sftp_download_folder_title"))
        if target:
            self.queue.add("get", remote_path, os.path.join(target, name), True)

    def _upload_folder(self):
        if not self.sftp:
            return
        local_path = QFileDialog.getExistingDirectory(self, t("sftp_upload_folder"))
        if local_path:
            self.queue.add("put", self._child_path(Path(local_path).name), local_path, True)

    def _upload(self):
        if not self.sftp:
            return
        # Kilka plików naraz — i tak idą do kolejki po jednym.
        paths, _ = QFileDialog.getOpenFileNames(self, t("sftp_upload"))
        for local_path in paths:
            self.queue.add("put", self._child_path(Path(local_path).name), local_path)

    def _new_folder(self):
        if not self.sftp:
            return
        name, ok = QInputDialog.getText(self, t("sftp_new_folder"), t("lbl_name"))
        if not ok or not name:
            return
        sftp, path = self.sftp, self._child_path(name)
        self._call(lambda: sftp.mkdir(path))

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        entry = item.data(Qt.UserRole) if item else None
        if entry is None or not self.sftp:
            return
        name, is_dir = entry
        menu = QMenu(self)
        if is_dir:
            menu.addAction(t("sftp_download"), lambda: self._download_folder(self._child_path(name), name))
        else:
            menu.addAction(t("sftp_download"), lambda: self._download(self._child_path(name), name))
            menu.addAction(t("sftp_edit"), lambda: self._edit(self._child_path(name), name))
        menu.addAction(t("sftp_rename"), lambda: self._rename(name))
        menu.addAction(t("sftp_chmod"), lambda: self._chmod(name))
        menu.addAction(t("menu_delete"), lambda: self._delete(name, is_dir))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _rename(self, name):
        new_name, ok = QInputDialog.getText(self, t("sftp_rename"), t("sftp_rename_prompt"), text=name)
        if not ok or not new_name or new_name == name:
            return
        sftp, old, new = self.sftp, self._child_path(name), self._child_path(new_name)
        self._call(lambda: sftp.rename(old, new), "err_rename")

    def _chmod(self, name):
        mode_text, ok = QInputDialog.getText(self, t("sftp_chmod"), t("sftp_chmod_prompt"))
        if not ok or not mode_text:
            return
        try:
            mode = int(mode_text, 8)
            if not (0 <= mode <= 0o7777):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, t("err_chmod"), t("err_chmod_bad"))
            return
        sftp, path = self.sftp, self._child_path(name)
        self._call(lambda: sftp.chmod(path, mode), "err_chmod")

    def _delete(self, name, is_dir):
        if QMessageBox.question(
            self,
            t("confirm_delete_title"),
            t("confirm_delete_body", name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        sftp, path = self.sftp, self._child_path(name)
        self._call(lambda: (sftp.rmdir if is_dir else sftp.remove)(path), "err_delete")

    def closeEvent(self, event):
        self.queue.cancel_all()  # przerwany plik sprzątnięty, zanim kanał zniknie
        if not self._closed:
            self._closed = True
            sftp = self.sftp
            if sftp:
                self._pool.submit(sftp.close)  # po zadaniach, które jeszcze czekają
            self._pool.shutdown(wait=False)
        super().closeEvent(event)


def selftest():
    import time

    from PySide6.QtWidgets import QApplication
    import i18n

    app = QApplication.instance() or QApplication([])
    i18n.use("en")

    def settle(panel):
        """Czeka, aż wątek panelu skończy, i dowozi jego sygnały na GUI."""
        panel._pool.submit(lambda: None).result(timeout=5)
        app.processEvents()

    # Gdy transport nie daje kanału SFTP (obcy serwer, brak uprawnień), panel
    # ma się wyłączyć, a nie wywalić — i to bez blokowania konstruktora.
    class _NoSftpClient:
        def get_transport(self):
            raise OSError("brak transportu")

    panel = SftpPanel(_NoSftpClient())
    assert not panel.list.isEnabled(), "do otwarcia kanału panel jest wyłączony"
    settle(panel)
    assert panel.sftp is None, "atrapa bez transportu nie mogła dać działającego SFTP"
    assert not panel.list.isEnabled(), "panel bez SFTP musi być wyłączony"
    assert panel.list.item(0).text() == t("sftp_unavailable"), panel.list.item(0).text()

    # Zakładki katalogów: dopisują się do listy z danych połączenia i wołają
    # zapis, żeby przeżyły restart.
    saved = []
    marks = ["/var/log"]
    marked = SftpPanel(_NoSftpClient(), None, marks, lambda: saved.append(True))
    marked.path = "/etc/nginx"
    marked._add_bookmark()
    assert marks == ["/var/log", "/etc/nginx"], marks
    assert saved == [True], "dodanie zakladki ma wolac zapis"
    marked._add_bookmark()
    assert marks.count("/etc/nginx") == 1, "ta sama sciezka nie moze wejsc dwa razy"
    marked._remove_bookmark()
    assert marks == ["/var/log"], marks
    marked.close()
    marked.deleteLater()

    # Historia katalogów: wstecz/do przodu jak w przeglądarce.
    panel.path = "/"
    panel._navigate("/etc")
    panel._navigate("/etc/ssh")
    panel._go_back()
    assert panel.path == "/etc", panel.path
    panel._go_back()
    assert panel.path == "/"
    panel._go_back()
    assert panel.path == "/", "pusta historia nie może cofać dalej"
    panel._go_forward()
    assert panel.path == "/etc", panel.path
    panel._navigate("/var")
    panel._go_forward()
    assert panel.path == "/var", "nowa ścieżka kasuje gałąź do przodu"

    # Lista w tle: wolny `listdir` nie blokuje wątku GUI, katalogi idą przed
    # plikami, a spóźniona odpowiedź ze starego katalogu nie nadpisuje nowego.
    def attr(name, mode):
        entry = paramiko.SFTPAttributes()
        entry.filename, entry.st_mode = name, mode
        return entry

    class _SlowSftp:
        def listdir_attr(self, path):
            time.sleep(0.2)
            return [attr("b.txt", stat.S_IFREG), attr(path.strip("/") or "root", stat.S_IFDIR)]

        def mkdir(self, path):
            raise OSError("brak uprawnień")

    panel.sftp = _SlowSftp()
    started = time.monotonic()
    panel._navigate("/stary")
    panel._navigate("/nowy")
    assert time.monotonic() - started < 0.1, "listdir zablokował wątek GUI"
    assert panel.list.item(0).data(Qt.UserRole) is None, "stara lista musi zniknąć od razu"
    panel._open_item(panel.list.item(0))  # „Wczytywanie…” — ma nic nie zrobić
    settle(panel)
    names = [panel.list.item(i).data(Qt.UserRole) for i in range(panel.list.count())]
    assert names == [("nowy", True), ("b.txt", False)], names

    # Błąd zmiany na serwerze: komunikat (tu podmieniony) i odświeżenie listy.
    warnings = []
    original_warning = QMessageBox.warning
    QMessageBox.warning = lambda *args: warnings.append(args[-1])
    try:
        panel._call(lambda: panel.sftp.mkdir("/nowy/x"))
        settle(panel)  # mkdir -> błąd -> refresh (kolejne zadanie w wątku)
        settle(panel)
    finally:
        QMessageBox.warning = original_warning
    assert warnings == ["brak uprawnień"], warnings
    assert panel.list.count() == 2, "po błędzie lista ma zostać odświeżona"

    # Po zamknięciu panel nie przyjmuje nowych zadań (wątek już zamknięty).
    panel.sftp = None
    panel.close()
    panel._run(lambda: None, lambda *_: None)
    panel.deleteLater()
    print("sftp selftest OK")
