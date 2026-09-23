"""Transfery SFTP: wątek z anulowaniem, okno postępu i kolejka w panelu.

Anulowanie przerywa `get`/`put` wyjątkiem z callbacku postępu, a obcięty plik
po drugiej stronie jest kasowany — połówka pliku jest gorsza niż jego brak
(przy `put` stara wersja i tak zniknęła w chwili otwarcia pliku do zapisu).
"""

import threading
from pathlib import Path

import paramiko
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
    """Jeden `get`/`put` na wątku roboczym — duży plik nie zamraża okna."""

    progress = Signal(int, int)  # bajtów zrobione, bajtów łącznie
    done = Signal(str, bool)  # tekst błędu (pusty = sukces), anulowano

    def __init__(self, sftp, mode, remote_path, local_path):
        super().__init__()
        self.sftp = sftp
        self.mode = mode
        self.remote_path = remote_path
        self.local_path = local_path
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def _callback(self, done, total):
        if self._cancel.is_set():
            raise _Cancelled()
        self.progress.emit(done, total)

    def run(self):
        try:
            if self.mode == "get":
                self.sftp.get(self.remote_path, self.local_path, callback=self._callback)
            else:
                self.sftp.put(self.local_path, self.remote_path, callback=self._callback)
        except Exception as error:
            self._remove_partial()
            cancelled = isinstance(error, _Cancelled)
            self.done.emit("" if cancelled else str(error) or type(error).__name__, cancelled)
            return
        self.done.emit("", False)

    def _remove_partial(self):
        """Sprzątanie po przerwanym transferze; nieudane (zerwane łącze) — trudno."""
        try:
            if self.mode == "get":
                Path(self.local_path).unlink(missing_ok=True)
            else:
                self.sftp.remove(self.remote_path)
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
        clear = QPushButton(t("transfer_clear"))
        clear.clicked.connect(self.clear_finished)
        buttons.addWidget(cancel)
        buttons.addWidget(clear)
        layout.addLayout(buttons)
        self.setVisible(False)  # pojawia się przy pierwszym transferze

    def reset_sftp(self):
        """Po ponownym połączeniu stary kanał jest martwy — następny transfer otworzy nowy."""
        self.sftp = None

    def add(self, mode, remote_path, local_path):
        name = Path(remote_path if mode == "get" else local_path).name
        arrow = t("transfer_dir_get") if mode == "get" else t("transfer_dir_put")
        item = QTreeWidgetItem([name, arrow, t("transfer_waiting")])
        item.setData(0, Qt.UserRole, (mode, remote_path, local_path))
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
        mode, remote_path, local_path = item.data(0, Qt.UserRole)
        self.current = item
        item.setText(2, "0%")
        self.worker = _Transfer(self.sftp, mode, remote_path, local_path)
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
    """Sukces, błąd, anulowanie z posprzątaniem i kolejka — na atrapie SFTP."""
    import tempfile
    import time

    import i18n
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    i18n.use("en")
    assert transfer_percent(0, 0) == 0, "nieznany rozmiar nie może dzielić przez 0"
    assert transfer_percent(50, 200) == 25

    class _FakeSftp:
        def __init__(self):
            self.removed = []

        def get(self, remote, local, callback=None):
            Path(local).write_text("połowa")
            for step in range(1, 51):
                time.sleep(0.01)
                callback(step, 50)

        def put(self, local, remote, callback=None):
            raise OSError("brak miejsca na dysku")

        def remove(self, path):
            self.removed.append(path)

    fake = _FakeSftp()
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "plik"
        assert run_transfer(None, fake, "get", "/zdalny", str(local), "test") == ""
        assert local.exists()
        assert "brak miejsca" in run_transfer(None, fake, "put", "/zdalny", str(local), "test")
        assert fake.removed == ["/zdalny"], "nieudany put ma skasować obcięty plik na serwerze"

        # Anulowanie w trakcie: wyjątek z callbacku, lokalna połówka skasowana.
        worker = _Transfer(fake, "get", "/zdalny", str(local))
        got = {}
        worker.done.connect(lambda e, c: got.update(error=e, cancelled=c), Qt.DirectConnection)
        worker.progress.connect(lambda d, total: d == 5 and worker.cancel(), Qt.DirectConnection)
        worker.run()
        assert got == {"error": "", "cancelled": True}, got
        assert not local.exists(), "anulowany get ma skasować połówkę pliku"

        # Kolejka: po jednym, błąd nie zatrzymuje reszty, anulowany czekający nie rusza.
        queue = TransferQueue(lambda: fake)
        uploads = []
        queue.upload_finished.connect(lambda: uploads.append(1))
        queue.add("get", "/a", str(Path(tmp) / "a"))
        queue.add("put", "/b", str(Path(tmp) / "b"))
        queue.add("get", "/c", str(Path(tmp) / "c"))
        states = lambda: [queue.tree.topLevelItem(i).text(2) for i in range(3)]
        assert states()[1:] == [t("transfer_waiting")] * 2, states()
        queue.tree.topLevelItem(2).setSelected(True)
        queue.cancel_selected()
        end = time.time() + 10
        while queue.worker is not None and time.time() < end:
            app.processEvents()
            time.sleep(0.01)
        assert states() == [t("transfer_done"), t("err_prefix", "brak miejsca na dysku"),
                            t("transfer_cancelled")], states()
        assert not (Path(tmp) / "c").exists() and not uploads
        queue.clear_finished()
        assert queue.tree.topLevelItemCount() == 0 and queue.isHidden()
    print("transfers selftest OK")
