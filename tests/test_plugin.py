"""Проверки модуля без интерфейса QGIS. Запуск: tests/run_tests.sh

Настройки QGIS уводятся во временный профиль tests/_profile. Сервис адресов
OpenStreetMap подменяется (table_algorithm.GEOCODE): в сеть тесты не ходят.
"""

import os
import shutil
import sys
import time
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from qgis.PyQt.QtCore import QCoreApplication, QSettings  # noqa: E402

PROFILE = os.path.join(HERE, "_profile")
shutil.rmtree(PROFILE, ignore_errors=True)
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, PROFILE)
ORG = "corner-points-catalog-tests"
QCoreApplication.setOrganizationName(ORG)
QCoreApplication.setApplicationName(ORG)

from qgis.core import (  # noqa: E402
    NULL,
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingFeedback,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt import sip  # noqa: E402
from qgis.PyQt.QtCore import QEvent  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow, QMenu, QToolBar  # noqa: E402

app = QgsApplication([], True, PROFILE)
if os.environ.get("QGIS_PREFIX_PATH"):
    app.setPrefixPath(os.environ["QGIS_PREFIX_PATH"], True)
app.initQgis()
# initQgis() переносит настройки в профиль default организации: возвращаем во временный
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, PROFILE)
assert QSettings().fileName().startswith(PROFILE), QSettings().fileName()

# Processing из поставки QGIS
for path in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    plugins = os.path.join(path, "plugins")
    if os.path.isdir(os.path.join(plugins, "processing")) and plugins not in sys.path:
        sys.path.append(plugins)
import processing  # noqa: E402
from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()

with warnings.catch_warnings():
    warnings.simplefilter("error", DeprecationWarning)
    import corner_points_catalog  # noqa: E402
    from corner_points_catalog import core, msk  # noqa: E402
    from corner_points_catalog import plugin as plugin_module  # noqa: E402
    from corner_points_catalog.processing import table_algorithm  # noqa: E402
    from corner_points_catalog.processing.provider import CornerPointsProvider  # noqa: E402

PROVIDER = CornerPointsProvider()
QgsApplication.processingRegistry().addProvider(PROVIDER)
ALG = "cornerpoints:coordinates"
MSK50_1 = next(i for i in msk.items() if i["name"].startswith("МСК-50 зона 1"))

# Вершины контура 3 из «Границы запроса МО (изм).gpkg» (КВЛ Дорохово — Созвездие)
# и строки 1–9 «Ведомость координат МО.xlsx» (ГГЭ, 2026-08-27) для них
SAMPLE = [
    (1, 36.648158184264, 55.277835174641, "55°16′40,21″", "36°38′53,37″", 415985.909, 1324143.949),
    (2, 36.644062839845, 55.277759515109, "55°16′39,94″", "36°38′38,63″", 415973.146, 1323883.843),
    (3, 36.642379719373, 55.283672375541, "55°17′1,22″", "36°38′32,57″", 416629.591, 1323765.943),
    (4, 36.630523675548, 55.290234663931, "55°17′24,85″", "36°37′49,89″", 417347.652, 1323000.618),
    (5, 36.612105999968, 55.293819940733, "55°17′37,75″", "36°36′43,58″", 417727.669, 1321824.134),
    (6, 36.613025680187, 55.294177518269, "55°17′39,04″", "36°36′46,89″", 417768.421, 1321881.908),
    (7, 36.612070338363, 55.296810055991, "55°17′48,52″", "36°36′43,45″", 418060.496, 1321816.478),
    (8, 36.632513065249, 55.292409495142, "55°17′32,67″", "36°37′57,05″", 417591.838, 1323123.006),
    (9, 36.64604206552, 55.285023317665, "55°17′6,08″", "36°38′45,75″", 416783.854, 1323996.13),
]
# Широта точек 2 и 4 в ведомости больше на 0,01″: от вершин выходит 39,934″ и 24,845″
# (QgsCoordinateFormatter даёт то же). Модуль округляет правильно.
SAMPLE_OFF = {2: "55°16′39,93″", 4: "55°17′24,84″"}


# ------------------------------------------------------------ подмена Nominatim

def answer(iso, state, country="ru"):
    return {"address": {"state": state, "ISO3166-2-lvl4": iso, "country": "Россия",
                        "country_code": country}}


class FakeGeocode:
    """Ответ по долготе: восточнее border — Москва, западнее — Московская область."""

    def __init__(self, border=None, error=None, foreign=False):
        self.border = border
        self.error = error
        self.foreign = foreign
        self.calls = []

    def __call__(self, lon, lat):
        self.calls.append((lon, lat))
        if self.error:
            return None, self.error
        if self.foreign:
            return {"address": {"country": "Казахстан", "country_code": "kz"}}, None
        if self.border is not None and lon > self.border:
            return answer("RU-MOW", "Москва"), None
        return answer("RU-MOS", "Московская область"), None


def use_geocode(fake):
    table_algorithm.GEOCODE = fake
    table_algorithm.DELAY = 0
    return fake


def points_layer(rows, crs="EPSG:4326", field="Угол поворота", kind="integer", name="углы"):
    uri = "Point?crs={}".format(crs) + ("&field={}:{}".format(field, kind) if field else "")
    layer = QgsVectorLayer(uri, name, "memory")
    feats = []
    for num, lon, lat in rows:
        f = QgsFeature(layer.fields())
        if field:
            f.setAttributes([num])
        if lon is not None:
            f.setGeometry(QgsGeometry.fromWkt("Point ({!r} {!r})".format(lon, lat)))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


def sample_layer():
    # в слое точки лежат не по порядку номеров — таблица должна их упорядочить
    rows = [(r[0], r[1], r[2]) for r in SAMPLE]
    return points_layer(rows[4:] + rows[:4])


class Feedback(QgsProcessingFeedback):
    def __init__(self):
        super().__init__()
        self.warnings = []

    def pushWarning(self, text):
        self.warnings.append(text)
        super().pushWarning(text)


def run(layer, **params):
    values = {"INPUT": layer, "OUTPUT": "memory:"}
    values.update(params)
    feedback = Feedback()
    result = processing.run(ALG, values, feedback=feedback)
    result["feedback"] = feedback
    result["rows"] = [[None if v == NULL else v for v in f.attributes()]
                      for f in result["OUTPUT"].getFeatures()]
    return result


class DmsTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(core.format_dms(55.277835174641), "55°16′40,21″")
        self.assertEqual(core.format_dms(36.642379719373), "36°38′32,57″")
        self.assertEqual(core.format_dms(55.2836723, 3), "55°17′1,220″")
        self.assertEqual(core.format_dms(55.5, 0), "55°30′0″")
        self.assertEqual(core.format_dms(-0.5), "-0°30′0,00″")

    def test_carry(self):
        # 59,996″ округляются до 60″ → следующая минута, а не «59′60,00″»
        self.assertEqual(core.format_dms(55 + 59 / 60 + 59.996 / 3600), "56°0′0,00″")


class PluralTest(unittest.TestCase):
    def test_forms(self):
        cases = {1: "1 точка", 2: "2 точки", 4: "4 точки", 5: "5 точек", 11: "11 точек",
                 12: "12 точек", 14: "14 точек", 21: "21 точка", 22: "22 точки",
                 25: "25 точек", 101: "101 точка", 111: "111 точек", 0: "0 точек"}
        for count, text in cases.items():
            self.assertEqual(core.plural(count, "точка", "точки", "точек"), text, count)


class NumberFieldTest(unittest.TestCase):
    def test_find(self):
        # поля слоя «углы поворота МО.shp»: имя «Угол поворота» урезано shapefile
        self.assertEqual(core.find_number_field(
            ["fid", "Name", "Субъе", "Угол �", "Долго", "Широт"]), "Угол �")
        self.assertEqual(core.find_number_field(["id", "num"]), "num")
        self.assertEqual(core.find_number_field(["id", "№ п/п"]), "№ п/п")
        self.assertEqual(core.find_number_field(["number", "Номер точки"]), "Номер точки")
        self.assertIsNone(core.find_number_field(["id", "name", "Nominal"]))

    def test_sort(self):
        values = ["10", "2", None, "н1", 3, "1,5"]
        self.assertEqual(sorted(values, key=core.sort_key), ["1,5", "2", 3, "10", "н1", None])


class LookupTest(unittest.TestCase):
    def test_delay_and_cache(self):
        fake = FakeGeocode()
        lookup = core.SubjectLookup(fake, delay=0.3)
        start = time.monotonic()
        self.assertEqual(lookup.subject(37.0, 55.0), (50, "Московская область"))
        lookup.subject(37.1, 55.0)
        self.assertGreaterEqual(time.monotonic() - start, 0.3)
        lookup.subject(37.0, 55.0)  # та же точка — из памяти
        self.assertEqual(len(fake.calls), 2)

    def test_error(self):
        lookup = core.SubjectLookup(FakeGeocode(error="Нет ответа"), delay=0)
        with self.assertRaisesRegex(LookupError, "Нет ответа"):
            lookup.subject(37.0, 55.0)

    def test_cancel_stops_waiting(self):
        feedback = QgsProcessingFeedback()
        lookup = core.SubjectLookup(FakeGeocode(), delay=30, feedback=feedback)
        lookup.subject(37.0, 55.0)
        feedback.cancel()
        start = time.monotonic()
        lookup.subject(37.1, 55.0)
        self.assertLess(time.monotonic() - start, 1)

    def test_zone_by_longitude(self):
        self.assertEqual(msk.short_name(core.msk_item(50, 36.6)), "МСК-50 зона 1")
        self.assertEqual(msk.short_name(core.msk_item(50, 37.8)), "МСК-50 зона 2")
        self.assertEqual(core.msk_item(77, 37.6)["name"], "Московская СК (МГГТ)")
        self.assertIsNone(core.msk_item(None, 37.6))


class TextsTest(unittest.TestCase):
    """Тексты, которые человек читает до того, как нажмёт кнопку."""

    def read(self, name):
        with open(os.path.join(os.path.dirname(HERE), name), encoding="utf-8") as f:
            return f.read()

    def test_says_what_goes_to_the_service(self):
        # человек должен узнать, что уходит наружу, не заглядывая в код
        for name in ("corner_points_catalog/metadata.txt", "README.md"):
            self.assertIn("уходят только координаты выделенных точек", self.read(name), name)
        algorithm = table_algorithm.CoordinateTableAlgorithm().shortHelpString()
        self.assertIn("уходят только координаты точек", algorithm)


class TableTest(unittest.TestCase):
    def setUp(self):
        self.fake = use_geocode(FakeGeocode())

    def test_matches_issued_catalog(self):
        result = run(sample_layer(), M_DECIMALS=3)
        rows = result["rows"]
        self.assertEqual([r[0] for r in rows], list(range(1, 10)))
        for row, sample in zip(rows, SAMPLE):
            num, lat, lon, x, y, subject, name = row
            self.assertEqual(lat, SAMPLE_OFF.get(num, sample[3]), num)
            self.assertEqual(lon, sample[4], num)
            self.assertAlmostEqual(x, sample[5], delta=0.0011)
            self.assertAlmostEqual(y, sample[6], delta=0.0011)
            self.assertEqual((subject, name), ("Московская область", "МСК-50 зона 1"))
        self.assertEqual(len(self.fake.calls), 9)
        self.assertEqual(result["ALIASES"]["num"], "№ угла")
        self.assertEqual(result["ALIASES"]["lat_dms"], "Широта (WGS-84)")
        self.assertEqual(result["ALIASES"]["x_msk"], "X, м (МСК-50 зона 1)")
        self.assertEqual(result["ALIASES"]["y_msk"], "Y, м (МСК-50 зона 1)")

    def test_only_selected(self):
        layer = sample_layer()
        QgsProject.instance().addMapLayer(layer)
        try:
            layer.selectByIds([f.id() for f in layer.getFeatures() if f["Угол поворота"] in (7, 2)])
            result = run(QgsProcessingFeatureSourceDefinition(layer.id(), True))
        finally:
            QgsProject.instance().removeMapLayer(layer.id())
        self.assertEqual([r[0] for r in result["rows"]], [2, 7])
        self.assertEqual(result["rows"][1][3], 418060.5)

    def test_two_subjects(self):
        use_geocode(FakeGeocode(border=37.83))
        result = run(points_layer([(1, 37.82, 55.65), (2, 37.84, 55.65)]))
        rows = result["rows"]
        self.assertEqual(rows[0][5:], ["Московская область", "МСК-50 зона 2"])
        self.assertEqual(rows[1][5:], ["Москва", "Московская СК (МГГТ)"])
        self.assertLess(abs(rows[1][3]), 5000)          # МГГТ: начало в Москве
        self.assertAlmostEqual(rows[0][3], 456950, delta=500)
        self.assertEqual(result["ALIASES"]["x_msk"], "X, м")  # МСК — в столбце «МСК»

    def test_outside_russia(self):
        use_geocode(FakeGeocode(foreign=True))
        result = run(points_layer([(1, 71.4, 51.1)]))
        self.assertEqual(result["rows"][0], [1, "51°6′0,00″", "71°24′0,00″", None, None,
                                             "Казахстан", None])
        self.assertIn("МСК не найдена", " ".join(result["feedback"].warnings))

    def test_offline_message_fits_this_module(self):
        # msk.py общий с «СК проекта», и его текст зовёт нажать «Определить заново» —
        # кнопку того окна. core.py подставляет свой текст, алгоритм дописывает, что делать
        self.assertNotIn("Определить заново", msk.NO_ANSWER)
        self.assertIn("corner_points_catalog", msk.USER_AGENT)
        use_geocode(FakeGeocode(error=msk.NO_ANSWER))
        with self.assertRaises(Exception) as caught:
            run(sample_layer())
        text = str(caught.exception)
        self.assertIn("Нет связи с сервисом адресов OpenStreetMap.", text)
        self.assertIn("МСК для всех точек", text)
        self.assertNotIn("Определить заново", text)
        self.assertEqual(text.count("Проверьте интернет"), 1)

    def test_offline(self):
        use_geocode(FakeGeocode(error="Нет ответа от сервиса адресов OpenStreetMap: timeout"))
        with self.assertRaisesRegex(Exception, "задайте МСК вручную"):
            run(sample_layer())

    def test_fixed_msk_needs_no_internet(self):
        fake = use_geocode(FakeGeocode(error="сеть не нужна"))
        result = run(sample_layer(), MSK=msk.crs_for(MSK50_1), M_DECIMALS=3)
        self.assertEqual(fake.calls, [])
        row = result["rows"][6]
        self.assertEqual(row[0], 7)
        self.assertAlmostEqual(row[3], 418060.496, delta=0.0011)
        self.assertEqual(row[5:], [None, "МСК-50 зона 1"])

    def test_geographic_msk_refused(self):
        with self.assertRaisesRegex(Exception, "в метрах"):
            run(sample_layer(), MSK=QgsCoordinateReferenceSystem("EPSG:4326"))

    def test_source_in_other_crs(self):
        utm = QgsCoordinateReferenceSystem("EPSG:32637")
        moved = processing.run("native:reprojectlayer",
                               {"INPUT": sample_layer(), "TARGET_CRS": utm, "OUTPUT": "memory:"})["OUTPUT"]
        result = run(moved, M_DECIMALS=3)
        self.assertEqual(result["OUTPUT"].crs(), utm)
        row = result["rows"][6]
        self.assertEqual(row[1:3], ["55°17′48,52″", "36°36′43,45″"])
        self.assertAlmostEqual(row[3], 418060.496, delta=0.002)
        lon, lat = self.fake.calls[0]
        self.assertAlmostEqual(lat, 55.2938, places=3)  # в сервис ушли градусы, не метры UTM

    def test_gsk2011(self):
        result = run(sample_layer(), GEO_CRS=1)
        self.assertEqual(result["ALIASES"]["lat_dms"], "Широта (ГСК-2011)")

    def test_missing_number_field(self):
        # поле могло пропасть: сохранённая модель, пакетный запуск, qgis_process —
        # вместо KeyError должно быть русское сообщение со списком полей слоя
        with self.assertRaises(Exception) as caught:
            run(sample_layer(), NUM_FIELD="нет_такого")
        text = str(caught.exception)
        self.assertIn("В слое нет поля «нет_такого»", text)
        self.assertIn("Угол поворота", text)
        self.assertNotIn("KeyError", text)
        self.assertNotIn("Traceback", text)

    def test_numbering_without_field(self):
        layer = points_layer([(None, 37.0, 55.0), (None, 37.1, 55.0)], field=None)
        result = run(layer)
        self.assertEqual([r[0] for r in result["rows"]], [1, 2])
        self.assertIn("по порядку", " ".join(result["feedback"].warnings))

    def test_text_numbers_and_empty_geometry(self):
        layer = points_layer([("10", 37.0, 55.0), ("2", 37.1, 55.0), ("н1", None, None)],
                             field="№", kind="string")
        result = run(layer)
        self.assertEqual([r[0] for r in result["rows"]], ["2", "10", "н1"])
        self.assertIsNone(result["rows"][2][1])
        self.assertIn("без геометрии: н1", " ".join(result["feedback"].warnings))
        self.assertEqual(len(self.fake.calls), 2)


# ------------------------------------------------------------ модуль в QGIS

class MessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, title, text, level, duration):
        self.messages.append((text, level))


class Iface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.bar = MessageBar()
        self.menu = []
        self.plugin_menu = QMenu("Модули")
        self.layer = None
        self.tables = []

    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def messageBar(self): return self.bar
    def activeLayer(self): return self.layer
    def showAttributeTable(self, layer): self.tables.append(layer)
    def addToolBar(self, name):
        # как в настоящем QGIS: панель в окне, но владеет ею Python
        bar = QToolBar(name)
        self.window.addToolBar(bar)
        sip.transferback(bar)
        return bar
    def pluginMenu(self): return self.plugin_menu
    def addPluginToMenu(self, m, a): self.menu.append((m, a.text()))
    def removePluginMenu(self, m, a): self.menu.remove((m, a.text()))


def toolbars(iface):
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return [b for b in iface.window.findChildren(QToolBar) if b.objectName() == "AltanEcoToolbar"]


class ButtonTest(unittest.TestCase):
    def setUp(self):
        QgsProject.instance().clear()
        use_geocode(FakeGeocode())
        self.registry = QgsApplication.processingRegistry()
        self.registry.removeProvider(PROVIDER)
        self.iface = Iface()
        self.plugin = corner_points_catalog.classFactory(self.iface)
        self.plugin.initProcessing()  # так делает QGIS до initGui() при hasProcessingProvider=yes
        self.plugin.initGui()
        self._get_item = plugin_module.QInputDialog.getItem
        box = getattr(plugin_module, "QMessageBox", None)   # на старом коде его нет
        self._question = box.exec if box is not None else None

    def tearDown(self):
        global PROVIDER
        plugin_module.QInputDialog.getItem = self._get_item
        if self._question is not None:
            plugin_module.QMessageBox.exec = self._question
        self.plugin.unload()
        # ссылку держим: иначе Python удалит провайдер вместе с алгоритмами
        PROVIDER = CornerPointsProvider()
        self.registry.addProvider(PROVIDER)
        QgsProject.instance().clear()

    def wait(self):
        end = time.monotonic() + 20
        while self.plugin._running is not None and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.plugin._running, "расчёт не закончился")

    def last(self):
        return self.iface.bar.messages[-1]

    def add(self, layer):
        QgsProject.instance().addMapLayer(layer)
        self.iface.layer = layer
        return layer

    def test_button_and_menu(self):
        self.assertEqual(len(toolbars(self.iface)), 1)
        self.assertEqual(self.iface.menu,
                         [("&Альтан-Эко", "Ведомость координат выделенных угловых точек")])
        self.assertIsNotNone(self.registry.algorithmById(ALG))

    def test_needs_point_layer_and_selection(self):
        self.plugin.run()
        self.assertIn("слой угловых точек", self.last()[0])
        self.add(QgsVectorLayer("Polygon?crs=EPSG:4326", "полигоны", "memory"))
        self.plugin.run()
        self.assertIn("слой угловых точек", self.last()[0])
        self.add(sample_layer())
        self.plugin.run()
        self.assertEqual(self.last(), ("Выделите угловые точки на слое «углы».",
                                       Qgis.MessageLevel.Warning))
        self.assertIsNone(self.plugin._running)

    def test_selected_points_to_table(self):
        layer = self.add(sample_layer())
        layer.selectByIds([f.id() for f in layer.getFeatures() if f["Угол поворота"] <= 3])
        self.plugin.run()
        self.wait()
        self.assertEqual(len(self.iface.tables), 1)
        table = self.iface.tables[0]
        self.assertEqual(table.name(), "Ведомость координат — углы")
        self.assertIn(table.id(), QgsProject.instance().mapLayers())
        self.assertEqual([f["num"] for f in table.getFeatures()], [1, 2, 3])
        self.assertEqual(table.attributeDisplayName(table.fields().indexOf("x_msk")),
                         "X, м (МСК-50 зона 1)")
        self.assertEqual(table.attributeDisplayName(0), "№ угла")
        x = table.fields().field("x_msk")
        self.assertEqual(x.precision(), 2)
        self.assertRegex(x.displayString(418060.5), r"[.,]50$")  # разделитель — по языку
        self.assertEqual(self.last()[1], Qgis.MessageLevel.Success)
        self.assertEqual(layer.featureCount(), 9)  # слой точек не менялся
        self.assertEqual(layer.fields().names(), ["Угол поворота"])

    def test_asks_for_number_field(self):
        layer = self.add(points_layer([("a", 37.0, 55.0), ("b", 37.1, 55.0)],
                                      field="name", kind="string"))
        layer.selectAll()
        asked = []

        def get_item(parent, title, text, items, current, editable):
            asked.append(items)
            return "по порядку (1, 2, 3…)", True
        plugin_module.QInputDialog.getItem = get_item
        self.plugin.run()
        self.wait()
        self.assertEqual(asked, [["name", "по порядку (1, 2, 3…)"]])
        self.assertEqual([f["num"] for f in self.iface.tables[0].getFeatures()], [1, 2])

        plugin_module.QInputDialog.getItem = lambda *a: ("", False)
        self.plugin.run()  # отмена выбора — ничего не считается
        self.assertIsNone(self.plugin._running)
        self.assertEqual(len(self.iface.tables), 1)

    def test_result_message_keeps_total_with_warnings(self):
        # предупреждение не должно прятать итог и подсказку, куда делась таблица
        layer = self.add(points_layer([(1, 37.0, 55.0), (2, 37.1, 55.0), (3, None, None)]))
        layer.selectAll()
        self.plugin.run()
        self.wait()
        text, level = self.last()
        self.assertEqual(level, Qgis.MessageLevel.Warning)
        self.assertIn("Готово: 3 строки", text)
        self.assertIn("без геометрии: 3", text)
        self.assertIn("Экспорт", text)

    def test_result_message_plural(self):
        layer = self.add(points_layer([(1, 37.0, 55.0)]))
        layer.selectAll()
        self.plugin.run()
        self.wait()
        text, level = self.last()
        self.assertEqual(level, Qgis.MessageLevel.Success)
        self.assertIn("Готово: 1 строка", text)
        self.assertIn("Считаю координаты: 1 точка", self.iface.bar.messages[-2][0])

    def test_many_points_asks_first(self):
        # 101 точка — это больше полутора минут: сначала спрашиваем
        rows = [(n + 1, 37.0 + n / 1000.0, 55.0) for n in range(101)]
        layer = self.add(points_layer(rows))
        layer.selectAll()
        asked = []

        def exec_(box):
            asked.append(box.text() + " | " + box.informativeText())
            return plugin_module.QMessageBox.StandardButton.No
        plugin_module.QMessageBox.exec = exec_
        self.plugin.run()
        self.assertEqual(len(asked), 1)
        self.assertIn("Выделено 101 точка", asked[0])
        self.assertIn("около 2 минут", asked[0])
        self.assertIn("МСК для всех точек", asked[0])
        self.assertIsNone(self.plugin._running)       # «нет» — ничего не считается
        self.assertEqual(self.iface.tables, [])

        plugin_module.QMessageBox.exec = lambda box: plugin_module.QMessageBox.StandardButton.Yes
        self.plugin.run()
        self.wait()
        self.assertEqual(len(self.iface.tables), 1)
        self.assertEqual(self.iface.tables[0].featureCount(), 101)

    def test_small_selection_asks_nothing(self):
        layer = self.add(sample_layer())
        layer.selectAll()
        plugin_module.QMessageBox.exec = lambda box: self.fail("спросил про большое выделение")
        self.plugin.run()
        self.wait()
        self.assertEqual(len(self.iface.tables), 1)

    def test_error_shown(self):
        use_geocode(FakeGeocode(error="Нет ответа от сервиса адресов OpenStreetMap"))
        layer = self.add(sample_layer())
        layer.selectAll()
        self.plugin.run()
        self.wait()
        text, level = self.last()
        self.assertEqual(level, Qgis.MessageLevel.Critical)
        self.assertIn("Нет ответа", text)
        self.assertEqual(self.iface.tables, [])

    def test_unload_leaves_nothing(self):
        self.plugin.unload()
        self.assertEqual(toolbars(self.iface), [])
        self.assertEqual(self.iface.menu, [])
        self.assertIsNone(self.registry.algorithmById(ALG))
        self.plugin.initGui()  # для tearDown


class DuplicateProviderTest(unittest.TestCase):
    """Провайдер с таким id уже есть: QGIS отвергает второй и удаляет объект.
    Модуль не должен держать на него ссылку, иначе unload() падает и не убирает за собой."""

    def test_unload_after_duplicate(self):
        registry = QgsApplication.processingRegistry()
        iface = Iface()
        plugin = corner_points_catalog.classFactory(iface)
        plugin.initGui()        # PROVIDER уже зарегистрирован — этот отвергнут
        try:
            self.assertIsNone(plugin.provider)
            self.assertIsNotNone(registry.algorithmById(ALG))
        finally:
            plugin.unload()     # не должно падать
        self.assertEqual(toolbars(iface), [])
        self.assertEqual(iface.menu, [])
        # чужой (первый) провайдер остался на месте
        self.assertIsNotNone(registry.algorithmById(ALG))


def _leftover_dirs():
    """Папки тестовой организации вне tests/_profile: их создают Qt и initQgis()."""
    from qgis.PyQt.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    found = [os.path.join(base, ORG)]
    path = QgsApplication.qgisSettingsDirPath().rstrip("/")
    while path and os.path.dirname(path) != path:
        if os.path.basename(path) == ORG:
            found.append(path)
        path = os.path.dirname(path)
    return [p for p in found if os.path.basename(p) == ORG and not p.startswith(PROFILE)]


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    leftovers = _leftover_dirs()
    QgsProject.instance().clear()
    app.exitQgis()
    shutil.rmtree(PROFILE, ignore_errors=True)
    for path in leftovers:
        shutil.rmtree(path, ignore_errors=True)
    sys.exit(0 if result.wasSuccessful() else 1)
