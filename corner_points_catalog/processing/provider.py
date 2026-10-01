import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .table_algorithm import CoordinateTableAlgorithm

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))


class CornerPointsProvider(QgsProcessingProvider):
    def id(self):
        return "cornerpoints"

    def name(self):
        return "Угловые точки"

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.svg"))

    def loadAlgorithms(self):
        self.addAlgorithm(CoordinateTableAlgorithm())
