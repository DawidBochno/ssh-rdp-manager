"""Transfery SFTP: wątek z anulowaniem, okno postępu i kolejka w panelu.

Anulowanie przerywa kopiowanie wyjątkiem z callbacku postępu, a obcięty plik
po drugiej stronie jest kasowany. Błąd (zerwane łącze) zostawia połówkę —
„Wznów” w kolejce dopisuje resztę zamiast słać wszystko od nowa.
"""

import os
import posixpath
import stat
import threading
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QProgressDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import notify
from i18n import t


class _Cancelled(Exception):
    pass


class _Transfer(QThread):
    """Plik albo cały folder (`is_dir`) na wątku roboczym — duży transfer nie zamraża okna.

    `resume`: plik już istniejący po drugiej stronie, mniejszy od źródła, jest
    dopisywany od miejsca przerwania, równy — pomijany. Tylko na wyraźne
    „Wznów” z kolejki, nie domyślnie: obcy mniejszy plik o tej samej nazwie
    zostałby po cichu sklejony z naszym.
    """

    progress = Signal(object, object)  # bajty zrobione, łącznie — `object`, bo folder > 2 GB przepełnia int
    done = Signal(str, bool)  # tekst błędu (pusty = sukces), anulowano

    CHUNK = 32768

    def __init__(self, sftp, mode, remote_path, local_path, is_dir=False, resume=False):
        super().__init__()
        self.sftp = sftp
        self.mode = mode
        self.remote_path = remote_path
        self.local_path = local_path
        self.is_dir = is_dir
        self.resume = resume
        self._target = None  # plik w trakcie zapisu — do skasowania po anulowaniu
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def _callback(self, done, total):
        if self._cancel.is_set():
            raise _Cancelled()
        self.progress.emit(done, total)

    def run(self):
        # Wywoływalne zamiast klienta = własny kanał na czas transferu, otwarty
        # tutaj, w tle (edycja/drag-out z panelu SFTP, patrz `sftp.py`).
        own = callable(self.sftp)
        if own:
            try:
                self.sftp = self.sftp()
            except Exception as error:
                self.done.emit(str(error) or type(error).__name__, False)
                return
        try:
            self._run()
        finally:
            if own:
                self.sftp.close()

    def _run(self):
        try:
            jobs = self._jobs()
            total, done = sum(size for *_, size in jobs), 0
            for remote, local, size in jobs:
                self._copy(remote, local, size, done, total)
                done += size
            self._callback(done, total)
        except Exception as error:
            cancelled = isinstance(error, _Cancelled)
            # Przy błędzie (zerwane łącze) połówka zostaje — „Wznów” dopisze resztę.
            if cancelled:
                self._remove_partial()
            self.done.emit("" if cancelled else str(error) or type(error).__name__, cancelled)
            return
        self.done.emit("", False)

    def _jobs(self):
        """[(zdalny, lokalny, rozmiar źródła)] do skopiowania; foldery celu zakłada po drodze."""
        if not self.is_dir:
            size = (self.sftp.stat(self.remote_path).st_size if self.mode == "get"
                    else os.path.getsize(self.local_path))
            return [(self.remote_path, self.local_path, size)]
        jobs = []
        if self.mode == "get":
            pending = [(self.remote_path, self.local_path)]
            while pending:
                remote, local = pending.pop()
                self._callback(0, 0)  # anulowanie w trakcie listowania dużego drzewa
                Path(local).mkdir(parents=True, exist_ok=True)
                for entry in self.sftp.listdir_attr(remote):
                    paths = posixpath.join(remote, entry.filename), os.path.join(local, entry.filename)
                    # ponytail: dowiązania pomijane (pętle), dodać `stat` celu, gdy będą potrzebne
                    if stat.S_ISDIR(entry.st_mode):
                        pending.append(paths)
                    elif stat.S_ISREG(entry.st_mode):
                        jobs.append((*paths, entry.st_size))
        else:
            for root, _dirs, files in os.walk(self.local_path):  # od góry — rodzic przed dzieckiem
                self._callback(0, 0)
                relative = Path(root).relative_to(self.local_path).parts
                remote = posixpath.join(self.remote_path, *relative)
                try:
                    self.sftp.stat(remote)
                except OSError:
                    self.sftp.mkdir(remote)
                for name in files:
                    local = os.path.join(root, name)
                    jobs.append((posixpath.join(remote, name), local, os.path.getsize(local)))
        return jobs

    def _copy(self, remote, local, size, done_before, total):
        get = self.mode == "get"
        offset = 0
        if self.resume:
            try:
                offset = os.path.getsize(local) if get else self.sftp.stat(remote).st_size
            except OSError:
                offset = 0
            if offset > size:
                offset = 0  # większy niż źródło = nie nasza połówka, od nowa
        self._target = local if get else remote
        if get:
            source, target = self.sftp.open(remote, "rb"), open(local, "ab" if offset else "wb")
        else:
            source, target = open(local, "rb"), self.sftp.open(remote, "r+b" if offset else "wb")
        with source, target:
            source.seek(offset)
            target.seek(offset)
            # Bez tego Paramiko czeka na odpowiedź po każdym kawałku — jak `get`/`put`.
            if get:
                source.prefetch(size)
            else:
                target.set_pipelined(True)
            done = offset
            while chunk := source.read(self.CHUNK):
                target.write(chunk)
                done += len(chunk)
                self._callback(done_before + done, total)
        self._target = None

    def _remove_partial(self):
        """Sprzątanie po anulowanym pliku; skończone pliki folderu zostają."""
        try:
            if self._target is None:
                return
            if self.mode == "get":
                Path(self._target).unlink(missing_ok=True)
            else:
                self.sftp.remove(self._target)
        except Exception:
            pass


def transfer_percent(done, total):
    """Procent do paska postępu; nieznany rozmiar (0) daje 0."""
    return int(100 * done / total) if total else 0


def run_transfer(parent, sftp, mode, remote_path, local_path, title):
    """Przenosi plik blokująco, z oknem postępu. Zwraca tekst błędu albo pusty tekst.

    Dla edycji na miejscu i przeciągania na zewnątrz — tam plik jest potrzebny
    od razu. Zwykłe pobieranie/wysyłanie idzie przez `TransferQueue`.
    Anulowanie zwraca `t("transfer_cancelled")`.
    `sftp` może być też funkcją otwierającą nowy kanał — wtedy otwiera go i
    zamyka wątek transferu, a okno nie czeka na rundę po sieci.
    """
    dialog = QProgressDialog(title, t("cancel"), 0, 100, parent)
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)

    worker = _Transfer(sftp, mode, remote_path, local_path)
    result = {}
    worker.progress.connect(lambda d, total: dialog.setValue(transfer_percent(d, total)))
    worker.done.connect(lambda error, cancelled: result.update(error=error, cancelled=cancelled))
    # `canceled` leci tylko z kliknięcia, nie z `dialog.cancel()` w kodzie.
    dialog.canceled.connect(worker.cancel)

    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    worker.start()
    dialog.show()
    loop.exec()
    dialog.close()
    worker.wait(1000)
    if result.get("cancelled"):
        return t("transfer_cancelled")
    return result.get("error", "")


class TransferQueue(QWidget):
    """Kolejka transferów pod listą plików: po jednym naraz, reszta czeka.

    Własny kanał SFTP (`open_sftp`), nie ten z panelu — Paramiko nie obiecuje,
    że jeden `SFTPClient` zniesie `get` w tle i `listdir` z GUI jednocześnie.
    """

    upload_finished = Signal()  # panel odświeża listę plików

    COLUMNS = 3  # plik, kierunek, stan
    RESUME_ROLE = Qt.UserRole + 1  # „Wznów”: dopisywać do połówek zamiast od nowa

    def __init__(self, open_sftp, parent=None):
        super().__init__(parent)
        self.open_sftp = open_sftp  # () -> nowy SFTPClient
        self.sftp = None
        self.worker = None
        self.current = None  # QTreeWidgetItem w trakcie
        self._ok = self._failed = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([t("transfer_file"), t("transfer_direction"), t("transfer_state")])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setMaximumHeight(130)
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        cancel = QPushButton(t("transfer_cancel_selected"))
        cancel.clicked.connect(self.cancel_selected)
        resume = QPushButton(t("transfer_resume_selected"))
        resume.setToolTip(t("transfer_resume_tip"))
        resume.clicked.connect(self.resume_selected)
        buttons.addWidget(resume)
        clear = QPushButton(t("transfer_clear"))
        clear.clicked.connect(self.clear_finished)
        buttons.addWidget(cancel)
        buttons.addWidget(clear)
        layout.addLayout(buttons)
        self.setVisible(False)  # pojawia się przy pierwszym transferze

    def reset_sftp(self):
        """Po ponownym połączeniu stary kanał jest martwy — następny transfer otworzy nowy."""
        self.sftp = None

    def add(self, mode, remote_path, local_path, is_dir=False):
        name = Path(remote_path if mode == "get" else local_path).name
        arrow = t("transfer_dir_get") if mode == "get" else t("transfer_dir_put")
        item = QTreeWidgetItem([f"📁 {name}" if is_dir else name, arrow, t("transfer_waiting")])
        item.setData(0, Qt.UserRole, (mode, remote_path, local_path, is_dir))
        item.setToolTip(0, remote_path if mode == "get" else local_path)
        self.tree.addTopLevelItem(item)
        self.setVisible(True)
        self._start_next()

    def _waiting(self):
        return [
            item for item in (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
            if item.text(2) == t("transfer_waiting")
        ]

    def _start_next(self):
        if self.worker is not None:
            return
        waiting = self._waiting()
        if not waiting:
            if self._ok or self._failed:
                notify.notify(t("notify_title"), t("transfer_queue_done", self._ok, self._failed))
                self._ok = self._failed = 0
            return
        item = waiting[0]
        if self.sftp is None:
            try:
                self.sftp = self.open_sftp()
            except Exception as error:
                self._finish(item, str(error), False)
                self._start_next()
                return
        self.current = item
        item.setText(2, "0%")
        resume = bool(item.data(0, self.RESUME_ROLE))
        self.worker = _Transfer(self.sftp, *item.data(0, Qt.UserRole), resume=resume)
        self.worker.progress.connect(lambda d, total: item.setText(2, f"{transfer_percent(d, total)}%"))
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_done(self, error, cancelled):
        item, worker = self.current, self.worker
        self.current = None
        worker.wait()
        self.worker = None
        if error:
            self.sftp = None  # po błędzie kanał bywa martwy — następny otworzy nowy
        self._finish(item, error, cancelled)
        if not error and not cancelled and item.data(0, Qt.UserRole)[0] == "put":
            self.upload_finished.emit()
        self._start_next()

    def _finish(self, item, error, cancelled):
        if cancelled:
            item.setText(2, t("transfer_cancelled"))
        elif error:
            item.setText(2, t("err_prefix", error))
            item.setToolTip(2, error)
            for column in range(self.COLUMNS):
                item.setForeground(column, QColor("#d9534f"))
            self._failed += 1
        else:
            item.setText(2, t("transfer_done"))
            self._ok += 1

    def cancel_selected(self):
        for item in self.tree.selectedItems():
            if item is self.current:
                self.worker.cancel()
            elif item.text(2) == t("transfer_waiting"):
                item.setText(2, t("transfer_cancelled"))

    def resume_selected(self):
        """Przerwane/nieudane wracają do kolejki; plik zrobiony do końca zostaje pominięty."""
        for item in self.tree.selectedItems():
            if item is self.current or item.text(2) == t("transfer_waiting"):
                continue
            item.setData(0, self.RESUME_ROLE, True)
            item.setText(2, t("transfer_waiting"))
            item.setToolTip(2, "")
            for column in range(self.COLUMNS):
                item.setData(column, Qt.ForegroundRole, None)
        self._start_next()

    def cancel_all(self):
        """Zamknięcie sesji: czekające odpadają, bieżący przerwany i posprzątany."""
        for item in self._waiting():
            item.setText(2, t("transfer_cancelled"))
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)

    def clear_finished(self):
        for i in reversed(range(self.tree.topLevelItemCount())):
            item = self.tree.topLevelItem(i)
            if item is not self.current and item.text(2) != t("transfer_waiting"):
                self.tree.takeTopLevelItem(i)
        self.setVisible(self.tree.topLevelItemCount() > 0)


def selftest():
    """Plik, folder, błąd z połówką + wznowienie, anulowanie i kolejka — na atrapie SFTP."""
    import tempfile
    import time

    import i18n
    import paramiko
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    i18n.use("en")
    assert transfer_percent(0, 0) == 0, "nieznany rozmiar nie może dzielić przez 0"
    assert transfer_percent(50, 200) == 25

    class _File:
        """Plik z metodami Paramiko; `fail_at` = „brak miejsca” po tylu bajtach zapisu."""

        def __init__(self, sftp, real):
            self.sftp, self.real = sftp, real

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.real.close()

        def seek(self, offset):
            self.real.seek(offset)

        def read(self, size):
            return self.real.read(size)

        def write(self, data):
            if self.sftp.fail_at is not None and self.sftp.written + len(data) > self.sftp.fail_at:
                raise OSError("brak miejsca na dysku")
            self.sftp.written += len(data)
            self.real.write(data)

        def prefetch(self, size):
            pass

        def set_pipelined(self, on):
            pass

    class _DirSftp:
        """Katalog lokalny udający serwer: „/x” to `root/x`."""

        def __init__(self, root):
            self.root, self.removed, self.fail_at, self.written = root, [], None, 0

        def _path(self, path):
            return os.path.join(self.root, path.lstrip("/"))

        def stat(self, path):
            return os.stat(self._path(path))

        def mkdir(self, path):
            os.mkdir(self._path(path))

        def listdir_attr(self, path):
            return [paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(self._path(path), name)), name)
                    for name in os.listdir(self._path(path))]

        def open(self, path, mode):
            return _File(self, open(self._path(path), mode))

        def remove(self, path):
            self.removed.append(path)
            os.remove(self._path(path))

    def run(worker):
        got = {}
        worker.done.connect(lambda e, c: got.update(error=e, cancelled=c), Qt.DirectConnection)
        worker.run()
        return got

    data = bytes(range(256)) * 400  # 100 KB = kilka kawałków
    with tempfile.TemporaryDirectory() as tmp:
        server, local = Path(tmp) / "server", Path(tmp) / "local"
        server.mkdir()
        local.mkdir()
        fake = _DirSftp(str(server))
        (server / "plik").write_bytes(data)

        assert run_transfer(None, fake, "get", "/plik", str(local / "plik"), "test") == ""
        assert (local / "plik").read_bytes() == data

        # Błąd w połowie wysyłania: połówka zostaje, „Wznów” dosyła tylko resztę.
        fake.fail_at = 50000
        assert "brak miejsca" in run_transfer(None, fake, "put", "/kopia", str(local / "plik"), "test")
        assert not fake.removed, "błąd ma zostawić połówkę do wznowienia"
        half = (server / "kopia").stat().st_size
        assert 0 < half < len(data), half
        fake.fail_at, fake.written = None, 0
        assert run(_Transfer(fake, "put", "/kopia", str(local / "plik"), resume=True)) ==             {"error": "", "cancelled": False}
        assert (server / "kopia").read_bytes() == data, "wznowienie skleiło plik źle"
        assert fake.written == len(data) - half, "wznowienie wysłało więcej niż brakującą resztę"

        # Wznowienie pobierania: lokalna połówka dopisywana, nie nadpisywana.
        (local / "pol").write_bytes(data[:30000])
        run(_Transfer(fake, "get", "/plik", str(local / "pol"), resume=True))
        assert (local / "pol").read_bytes() == data

        # Anulowanie w trakcie: wyjątek z callbacku, połówka skasowana.
        worker = _Transfer(fake, "get", "/plik", str(local / "anul"))
        worker.progress.connect(lambda d, total: worker.cancel(), Qt.DirectConnection)
        assert run(worker) == {"error": "", "cancelled": True}
        assert not (local / "anul").exists(), "anulowany get ma skasować połówkę pliku"

        # Folder w obie strony: podkatalogi, pusty katalog, postęp w bajtach całości.
        (server / "www" / "css" / "pusty").mkdir(parents=True)
        (server / "www" / "index.html").write_bytes(data)
        (server / "www" / "css" / "a.css").write_bytes(b"body{}")
        worker = _Transfer(fake, "get", "/www", str(local / "www"), is_dir=True)
        steps = []
        worker.progress.connect(lambda d, total: steps.append((d, total)), Qt.DirectConnection)
        assert run(worker)["error"] == ""
        assert steps[-1] == (len(data) + 6, len(data) + 6), steps[-1]
        assert (local / "www" / "css" / "a.css").read_bytes() == b"body{}"
        assert (local / "www" / "css" / "pusty").is_dir()
        assert run(_Transfer(fake, "put", "/www2", str(local / "www"), is_dir=True))["error"] == ""
        assert (server / "www2" / "index.html").read_bytes() == data
        assert (server / "www2" / "css" / "pusty").is_dir()
        fake.written = 0
        run(_Transfer(fake, "put", "/www2", str(local / "www"), is_dir=True, resume=True))
        assert fake.written == 0, "wznowienie skończonego folderu nie może nic wysyłać"

        # Kolejka: po jednym, błąd nie zatrzymuje reszty, anulowany czekający nie rusza,
        # „Wznów” wraca nieudany do kolejki i go kończy.
        def drain():
            end = time.time() + 10
            while queue.worker is not None and time.time() < end:
                app.processEvents()
                time.sleep(0.01)

        queue = TransferQueue(lambda: fake)
        uploads = []
        queue.upload_finished.connect(lambda: uploads.append(1))
        fake.fail_at, fake.written = 10, 0
        queue.add("get", "/plik", str(local / "a"))
        queue.add("put", "/b", str(local / "plik"))
        queue.add("get", "/www", str(local / "c"), True)
        states = lambda: [queue.tree.topLevelItem(i).text(2) for i in range(3)]
        assert states()[1:] == [t("transfer_waiting")] * 2, states()
        assert queue.tree.topLevelItem(2).text(0) == "📁 www"
        queue.tree.topLevelItem(2).setSelected(True)
        queue.cancel_selected()
        drain()
        assert states() == [t("transfer_done"), t("err_prefix", "brak miejsca na dysku"),
                            t("transfer_cancelled")], states()
        assert not (local / "c").exists() and not uploads
        fake.fail_at = None
        queue.tree.clearSelection()
        queue.tree.topLevelItem(1).setSelected(True)
        queue.resume_selected()
        drain()
        assert states()[1] == t("transfer_done") and uploads == [1], states()
        assert (server / "b").read_bytes() == data
        queue.clear_finished()
        assert queue.tree.topLevelItemCount() == 0 and queue.isHidden()
    print("transfers selftest OK")
