import os

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingFeedback,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox

from . import altan_toolbar, core

try:  # Qt6 / QGIS 4: QAction живёт в QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction

PLUGIN_DIR = os.path.dirname(__file__)
ALGORITHM = "cornerpoints:coordinates"
TITLE = "Ведомость координат"
BY_ORDER = "по порядку (1, 2, 3…)"
MANY_POINTS = 100   # больше — предупреждаем: столько запросов к сервису адресов долго


class _Feedback(QgsProcessingFeedback):
    """Собирает предупреждения и ошибки алгоритма для сообщения пользователю."""

    def __init__(self):
        super().__init__()
        self.warnings = []
        self.errors = []

    def pushWarning(self, text):
        self.warnings.append(text)
        super().pushWarning(text)

    def reportError(self, text, fatalError=False):
        self.errors.append(text)
        super().reportError(text, fatalError)


class CornerPointsCatalogPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.provider = None
        self._running = None  # (задача, контекст, feedback, слой) — пока идёт расчёт

    def initProcessing(self):
        # QGIS сам вызывает initProcessing() у модулей с hasProcessingProvider=yes,
        # и initGui() — ещё раз: второй провайдер остался бы после выгрузки
        if self.provider is not None:
            return
        from .processing.provider import CornerPointsProvider

        provider = CornerPointsProvider()
        # Если провайдер с таким id уже есть (вторая копия модуля в профиле,
        # перезагрузка через Plugin Reloader), QGIS отвергает и удаляет объект:
        # ссылку на него держать нельзя, иначе unload() упадёт на удалённом
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider

    def initGui(self):
        self.initProcessing()
        self.action = QAction(
            QIcon(os.path.join(PLUGIN_DIR, "icon.svg")),
            "Ведомость координат выделенных угловых точек",
            self.iface.mainWindow(),
        )
        self.action.setToolTip(
            "Выделите угловые точки на слое и нажмите: получится таблица с № угла, "
            "широтой и долготой (г°м′с″) и координатами в МСК субъекта")
        self.action.triggered.connect(self.run)
        altan_toolbar.add_action(self.iface, self.action)
        altan_toolbar.add_to_menu(self.iface, self.action)

    def unload(self):
        if self._running is not None:
            self._running[0].cancel()
            self._running = None
        if self.action:
            altan_toolbar.remove_from_menu(self.iface, self.action)
            altan_toolbar.remove_action(self.iface, self.action)
            self.action.deleteLater()
            self.action = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    # ------------------------------------------------------------ кнопка

    def message(self, text, level=Qgis.MessageLevel.Info, duration=6):
        self.iface.messageBar().pushMessage(TITLE, text, level, duration)

    def run(self):
        if self._running is not None:
            self.message("Ведомость ещё считается — ход виден внизу окна QGIS.")
            return
        layer = self.iface.activeLayer()
        if not (isinstance(layer, QgsVectorLayer)
                and layer.geometryType() == Qgis.GeometryType.Point):
            self.message("Выберите в списке слоёв слой угловых точек и выделите точки.",
                         Qgis.MessageLevel.Warning)
            return
        count = layer.selectedFeatureCount()
        if count == 0:
            self.message("Выделите угловые точки на слое «{}».".format(layer.name()),
                         Qgis.MessageLevel.Warning)
            return
        if count > MANY_POINTS and not self.confirm_many(count):
            return
        num_field = self.number_field(layer)
        if num_field is None:
            return
        self.start(layer, num_field)

    def confirm_many(self, count):
        """Предупредить, что много точек — это долго, и спросить, считать ли."""
        return self.many_points_box(count).exec() == QMessageBox.StandardButton.Yes

    def many_points_box(self, count):
        """Вопрос о большом выделении (отдельно от показа — чтобы снимать окно)."""
        minutes = max(1, int(round(count * core.NOMINATIM_DELAY / 60)))
        box = QMessageBox(self.iface.mainWindow())
        box.setWindowTitle(TITLE)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Выделено {}. Расчёт займёт около {}.".format(
            core.plural(count, "точка", "точки", "точек"),
            core.plural(minutes, "минуту", "минуты", "минут")))
        box.setInformativeText(
            "Субъект определяется по каждой точке через интернет, не чаще раза в секунду.\n\n"
            "Если все точки в одной МСК, быстрее задать её вручную: панель инструментов анализа "
            "→ «Угловые точки» → «Ведомость координат угловых точек», поле «МСК для всех точек». "
            "Тогда интернет не нужен.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        box.button(QMessageBox.StandardButton.Yes).setText("Считать")
        box.button(QMessageBox.StandardButton.No).setText("Отмена")
        return box

    def number_field(self, layer):
        """Поле номера: найденное по названию, иначе — выбор пользователя.
        "" — нумеровать по порядку, None — отменено."""
        names = layer.fields().names()
        found = core.find_number_field(names)
        if found:
            return found
        choice, ok = QInputDialog.getItem(
            self.iface.mainWindow(), TITLE,
            "В слое «{}» нет поля с номером точки. Откуда брать № угла?".format(layer.name()),
            names + [BY_ORDER], len(names), False)
        if not ok:
            return None
        return "" if choice == BY_ORDER else choice

    def start(self, layer, num_field):
        registry = QgsApplication.processingRegistry()
        algorithm = registry.createAlgorithmById(ALGORITHM)
        params = {
            "INPUT": QgsProcessingFeatureSourceDefinition(layer.id(), True),
            "NUM_FIELD": num_field,
            "OUTPUT": "TEMPORARY_OUTPUT",
        }
        context = QgsProcessingContext()
        context.setProject(QgsProject.instance())
        feedback = _Feedback()
        task = QgsProcessingAlgRunnerTask(algorithm, params, context, feedback)
        task.setDescription("{}: {}".format(TITLE, layer.name()))
        count = layer.selectedFeatureCount()
        self._running = (task, context, feedback, layer.name())
        task.executed.connect(self.finished)
        QgsApplication.taskManager().addTask(task)
        self.message("Считаю координаты: {}. Субъект определяется через интернет, "
                     "около секунды на точку.".format(
                         core.plural(count, "точка", "точки", "точек")), duration=4)

    def finished(self, ok, results):
        running, self._running = self._running, None
        if running is None:
            return
        task, context, feedback, name = running
        if not ok:
            if feedback.isCanceled():
                self.message("Расчёт отменён.", Qgis.MessageLevel.Warning)
            else:
                text = feedback.errors[-1] if feedback.errors else "Не удалось посчитать координаты."
                self.message(text, Qgis.MessageLevel.Critical, 0)
            return
        from .processing.table_algorithm import apply_aliases

        table = context.takeResultLayer(results["OUTPUT"])
        if table is None:
            self.message("Таблица не получена.", Qgis.MessageLevel.Critical, 0)
            return
        table.setName("{} — {}".format(TITLE, name))
        apply_aliases(table, results.get("ALIASES") or {})
        QgsProject.instance().addMapLayer(table)
        self.iface.showAttributeTable(table)
        done = "Готово: {}.".format(core.plural(table.featureCount(), "строка", "строки", "строк"))
        excel = ("В Excel — «Экспорт → Сохранить как…», формат XLSX, флажок "
                 "«Use aliases for exported name» (русские заголовки).")
        if feedback.warnings:
            # итог виден всегда: предупреждение не должно прятать, где таблица и как её сохранить
            self.message(" ".join([done] + feedback.warnings + [excel]),
                         Qgis.MessageLevel.Warning, 0)
        else:
            self.message(" ".join([done, excel]), Qgis.MessageLevel.Success, 10)
