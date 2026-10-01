"""Окна модуля для scripts/ui_snap.py (навык qgis-ui-review).

Своих окон у модуля нет: кнопка считает молча, а пользователь видит
- окно алгоритма Processing (его строит QGIS из наших параметров и подсказки) —
  оно же открывается из панели инструментов анализа;
- вопрос «откуда брать № угла», когда в слое нет поля с номером.
Снимаем оба: тексты в них наши.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
sys.path.insert(0, PLUGIN_ROOT)

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature,  # noqa: E402
                       QgsGeometry, QgsProject, QgsRectangle, QgsVectorLayer)
from qgis.gui import QgsBrowserGuiModel, QgsMapCanvas  # noqa: E402
from qgis.PyQt.QtWidgets import QInputDialog, QMainWindow  # noqa: E402


class Iface:
    """Минимальный iface: Processing берёт из него главное окно и холст."""

    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        self.canvas.setExtent(QgsRectangle(4070000, 7430000, 4090000, 7450000))

        self.browser = QgsBrowserGuiModel()

    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def messageBar(self): return None
    def activeLayer(self): return None
    def browserModel(self): return self.browser
    def layerTreeView(self): return None


def points_layer():
    layer = QgsVectorLayer("Point?crs=EPSG:4326&field=Угол поворота:integer", "углы поворота МО", "memory")
    for num, lon, lat in ((1, 36.6481, 55.2778), (2, 36.6440, 55.2777), (3, 36.6423, 55.2836)):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([num])
        feature.setGeometry(QgsGeometry.fromWkt("Point ({} {})".format(lon, lat)))
        layer.dataProvider().addFeature(feature)
    return layer


def windows():
    import qgis.utils

    iface = Iface()
    qgis.utils.iface = iface                      # Processing берёт iface отсюда
    for path in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        plugins = os.path.join(path, "plugins")
        if os.path.isdir(os.path.join(plugins, "processing")) and plugins not in sys.path:
            sys.path.append(plugins)
    import processing
    from processing.core.Processing import Processing

    Processing.initialize()
    from corner_points_catalog.processing.provider import CornerPointsProvider

    provider = CornerPointsProvider()
    QgsApplication.processingRegistry().addProvider(provider)
    windows.provider = provider                   # ссылку держим: иначе Python удалит провайдер

    layer = points_layer()
    QgsProject.instance().addMapLayer(layer)

    def algorithm_dialog():
        dialog = processing.createAlgorithmDialog("cornerpoints:coordinates", {"INPUT": layer.id()})
        dialog.resize(900, 620)   # как окно открывается в QGIS; состояние «min» сжимает его само
        return dialog

    def number_field_question():
        from corner_points_catalog.plugin import BY_ORDER, TITLE

        dialog = QInputDialog(iface.mainWindow())
        dialog.setWindowTitle(TITLE)
        dialog.setLabelText("В слое «углы поворота МО» нет поля с номером точки. "
                            "Откуда брать № угла?")
        dialog.setComboBoxItems(["Name", "Описание", BY_ORDER])
        dialog.setTextValue(BY_ORDER)
        return dialog

    def many_points_question():
        from corner_points_catalog.plugin import CornerPointsCatalogPlugin

        plugin = CornerPointsCatalogPlugin(iface)
        return plugin.many_points_box(540)

    return [("алгоритм", algorithm_dialog), ("вопрос_о_номере", number_field_question),
            ("много_точек", many_points_question)]
