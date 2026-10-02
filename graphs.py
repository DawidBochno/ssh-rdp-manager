"""Mały wykres CPU/RAM z ostatnich minut — w prawym rogu paska statusu.

Historię zbiera już terminal (`SshTerminal.history`, jedna próbka na odpytanie
`_StatsPoller`), tu jest tylko rysowanie `QPainter`em: bez bibliotek do
wykresów, bo dwie linie na 100 punktów to kilkanaście linijek.
"""
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from i18n import t

CPU_COLOR = "#3498db"
RAM_COLOR = "#2ecc71"


def graph_points(values, width, height, step=1):
    """Procenty (0–100, None = brak próbki) -> punkty linii wykresu.

    Najnowsza próbka przy prawej krawędzi, jedna próbka na piksel szerokości —
    starsze, które się nie mieszczą, odpadają. None (pierwsza próbka CPU, brak
    danych) nie daje punktu, więc linia zaczyna się od pierwszej prawdziwej.
    `step` = pikseli na próbkę (duży wykres historii monitoringu).
    """
    values = list(values)[-(width // step):]
    offset = width // step - len(values)
    points = []
    for i, value in enumerate(values):
        if value is None:
            continue
        value = max(0.0, min(100.0, value))
        points.append(((offset + i) * step, (height - 1) * (1 - value / 100)))
    return points


class StatsGraph(QWidget):
    """Dwie linie: CPU i RAM aktywnej zakładki. Bez historii — schowany."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(100, 18)  # 100 próbek = 5 min przy odpytywaniu co 3 s
        self.history = None
        self.step = 1
        self.hide()

    def set_history(self, history):
        """`history` = lista par (cpu, ram) albo None (Home, RDP, zakładka logu)."""
        self.history = history
        self.setVisible(bool(history))
        if history:
            # None też w RAM — w historii monitoringu nieudany pomiar nie ma żadnej wartości.
            cpu, ram = (f"{v:.0f}%" if v is not None else "—" for v in history[-1])
            self.setToolTip(t("graph_tooltip", cpu, ram))
        self.update()

    def paintEvent(self, event):
        if not self.history:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(128, 128, 128, 40))
        for index, color in ((1, RAM_COLOR), (0, CPU_COLOR)):  # CPU na wierzchu
            points = graph_points(
                (sample[index] for sample in self.history), self.width(), self.height(), self.step
            )
            if len(points) > 1:
                painter.setPen(QPen(QColor(color), 1.2))
                painter.drawPolyline([QPointF(x, y) for x, y in points])
        painter.end()


def selftest():
    from PySide6.QtWidgets import QApplication
    import i18n

    QApplication.instance() or QApplication([])
    i18n.use("en")

    # Najnowsza próbka przy prawej krawędzi; 100% na górze, 0% na dole.
    assert graph_points([0, 100], 10, 11) == [(8, 10.0), (9, 0.0)], graph_points([0, 100], 10, 11)
    assert graph_points([None, 50], 4, 3) == [(3, 1.0)], "None nie daje punktu"
    assert len(graph_points(range(200), 100, 18)) == 100, "starsze niż szerokość odpadają"
    assert graph_points([150, -5], 2, 11) == [(0, 0.0), (1, 10.0)], "poza 0–100 przycięte"
    assert graph_points([0, 100, 0], 4, 11, step=2) == [(0, 0.0), (2, 10.0)], "step: 2 px na próbkę"

    holder = QWidget()  # rodzic, żeby setVisible nie otwierał osobnego okienka
    graph = StatsGraph(holder)
    graph.set_history([])
    assert graph.isHidden(), "bez historii wykres ma być schowany"
    graph.set_history([(None, 40.0), (12.0, 41.0)])
    assert not graph.isHidden()
    assert "12%" in graph.toolTip() and "41%" in graph.toolTip(), graph.toolTip()
    graph.set_history([(None, None)])
    assert "—" in graph.toolTip(), "nieudany pomiar z historii nie może wywalić podpowiedzi"
    graph.set_history(None)
    assert graph.isHidden()
    print("graphs selftest OK")
