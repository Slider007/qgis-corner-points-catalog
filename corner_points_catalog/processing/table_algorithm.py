"""Алгоритм «Ведомость координат угловых точек»: точки → таблица с координатами."""

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
)
from qgis.PyQt.QtCore import QCoreApplication, QMetaType

from .. import core, msk

# Географическая СК ведомости: (название в заголовке, код)
GEO_CRS = [("WGS-84", "EPSG:4326"), ("ГСК-2011", "EPSG:7683")]
WGS84 = "EPSG:4326"

# Тесты подменяют запрос к Nominatim и паузу между запросами
GEOCODE = None
DELAY = core.NOMINATIM_DELAY


def aliases(geo_name, msk_labels):
    """Русские заголовки столбцов. МСК в заголовке X/Y — если она одна на всю таблицу."""
    labels = sorted(set(label for label in msk_labels if label))
    suffix = " ({})".format(labels[0]) if len(labels) == 1 else ""
    return {
        "num": "№ угла",
        "lat_dms": "Широта ({})".format(geo_name),
        "lon_dms": "Долгота ({})".format(geo_name),
        "x_msk": "X, м" + suffix,
        "y_msk": "Y, м" + suffix,
        "subject": "Субъект",
        "msk": "МСК",
    }


def apply_aliases(layer, names):
    for field, alias in names.items():
        index = layer.fields().indexOf(field)
        if index >= 0:
            layer.setFieldAlias(index, alias)


class _Aliases(QgsProcessingLayerPostProcessorInterface):
    """Ставит заголовки столбцов, когда Processing добавляет слой в проект."""

    instances = []

    def __init__(self, names):
        super().__init__()
        self.names = names

    def postProcessLayer(self, layer, context, feedback):
        apply_aliases(layer, self.names)

    @classmethod
    def create(cls, names):
        # Processing не владеет объектом: держим ссылку, иначе Python его удалит
        cls.instances = cls.instances[-9:] + [cls(names)]
        return cls.instances[-1]


class CoordinateTableAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    NUM_FIELD = "NUM_FIELD"
    GEO = "GEO_CRS"
    MSK = "MSK"
    SEC_DECIMALS = "SEC_DECIMALS"
    M_DECIMALS = "M_DECIMALS"
    OUTPUT = "OUTPUT"
    ALIASES = "ALIASES"  # в результатах: заголовки столбцов

    def tr(self, text):
        return QCoreApplication.translate("CornerPointsCatalog", text)

    def createInstance(self):
        return CoordinateTableAlgorithm()

    def name(self):
        return "coordinates"

    def displayName(self):
        return self.tr("Ведомость координат угловых точек")

    def group(self):
        return self.tr("Ведомости координат")

    def groupId(self):
        return "catalogs"

    def shortHelpString(self):
        return self.tr(
            "Таблица координат угловых точек: № угла, широта и долгота в виде 55°16′40,21″ "
            "(WGS-84 или ГСК-2011), X (на север) и Y (на восток) в МСК, субъект и МСК.\n\n"
            "МСК определяется для каждой точки, как в модуле «СК проекта: UTM и МСК»: субъект РФ "
            "— через сервис адресов OpenStreetMap (нужен интернет, не больше запроса в секунду), "
            "зона — с ближайшим осевым меридианом. В OpenStreetMap уходят только координаты точек "
            "(широта и долгота); названия слоя, поля и прочие данные не передаются. Если МСК "
            "задана вручную, интернет не нужен и все точки считаются в ней.\n\n"
            "Номер берётся из поля слоя; если поле не выбрано — ищется поле «№…», «Угол…», "
            "«Номер…» или num, а если его нет — точки нумеруются по порядку.\n\n"
            "Параметры МСК взяты из рабочего набора и официально не проверялись.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Угловые точки"), [Qgis.ProcessingSourceType.VectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.NUM_FIELD, self.tr("Поле с номером точки"),
            parentLayerParameterName=self.INPUT, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.GEO, self.tr("Широта и долгота в системе"),
            options=[name for name, _ in GEO_CRS], defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            self.MSK, self.tr("МСК для всех точек (пусто — определить по месту через интернет)"),
            optional=True))
        for name, text in ((self.SEC_DECIMALS, "Знаков после запятой в секундах"),
                           (self.M_DECIMALS, "Знаков после запятой в метрах")):
            param = QgsProcessingParameterNumber(
                name, self.tr(text), Qgis.ProcessingNumberParameterType.Integer, 2, False, 0, 4)
            param.setFlags(param.flags() | Qgis.ProcessingParameterFlag.Advanced)
            self.addParameter(param)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Ведомость координат"), Qgis.ProcessingSourceType.VectorPoint))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        geo_name, geo_code = GEO_CRS[self.parameterAsEnum(parameters, self.GEO, context)]
        sec_decimals = self.parameterAsInt(parameters, self.SEC_DECIMALS, context)
        m_decimals = self.parameterAsInt(parameters, self.M_DECIMALS, context)
        fixed_crs = self.parameterAsCrs(parameters, self.MSK, context)
        if fixed_crs.isValid() and fixed_crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "МСК должна быть в метрах, а «{}» — в градусах.").format(fixed_crs.description()))

        num_field = self.parameterAsString(parameters, self.NUM_FIELD, context)
        if num_field and source.fields().indexOf(num_field) < 0:
            # поле могло пропасть: сохранённая модель, пакетный запуск, qgis_process
            raise QgsProcessingException(self.tr(
                "В слое нет поля «{}». Выберите другое поле с номером точки или оставьте "
                "параметр пустым — номер подберётся сам.\nПоля слоя: {}")
                .format(num_field, ", ".join(source.fields().names())))
        if not num_field:
            num_field = core.find_number_field(source.fields().names())
        if num_field:
            feedback.pushInfo(self.tr("Номер точки — из поля «{}»").format(num_field))
        else:
            feedback.pushWarning(self.tr("Поля с номером нет: точки пронумерованы по порядку"))

        ctx = context.transformContext()
        src_crs = source.sourceCrs()
        to_geo = QgsCoordinateTransform(src_crs, QgsCoordinateReferenceSystem(geo_code), ctx)
        to_wgs = QgsCoordinateTransform(src_crs, QgsCoordinateReferenceSystem(WGS84), ctx)
        lookup = core.SubjectLookup(GEOCODE, DELAY, feedback)
        msk_crs = {}     # название МСК → СК
        transforms = {}  # WKT СК → пересчёт из СК слоя

        # 1. Считаем строки
        rows = []
        empty, outside = [], []
        total = source.featureCount() or 1
        for n, feature in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                return {}
            feedback.setProgress(90.0 * n / total)
            num = feature[num_field] if num_field else n + 1
            if num is not None and hasattr(num, "isNull") and num.isNull():
                num = None
            row = {"num": num, "point": None}
            rows.append(row)
            geometry = feature.geometry()
            if geometry is None or geometry.isNull() or geometry.isEmpty():
                empty.append(num)
                continue
            point = QgsPointXY(geometry.vertexAt(0))
            row["point"] = point
            geo = to_geo.transform(point)
            row["lat_dms"] = core.format_dms(geo.y(), sec_decimals)
            row["lon_dms"] = core.format_dms(geo.x(), sec_decimals)

            if fixed_crs.isValid():
                crs, label, subject = fixed_crs, core.crs_label(fixed_crs), None
            else:
                wgs = to_wgs.transform(point)
                try:
                    code, subject = lookup.subject(wgs.x(), wgs.y())
                except LookupError as e:
                    raise QgsProcessingException(self.tr(
                        "{} Проверьте интернет или задайте МСК вручную: панель инструментов "
                        "анализа → «Угловые точки» → «Ведомость координат угловых точек», "
                        "поле «МСК для всех точек».").format(e))
                item = core.msk_item(code, wgs.x())
                if item is None:
                    outside.append(num)
                    row["subject"] = subject or None
                    continue
                label = msk.short_name(item)
                if label not in msk_crs:
                    # register=False: МСК нужна только как цель пересчёта,
                    # записывать её в «Пользовательские СК» профиля не нужно
                    msk_crs[label] = msk.crs_for(item, register=False)
                crs = msk_crs[label]
            key = crs.toWkt()
            if key not in transforms:
                transforms[key] = QgsCoordinateTransform(src_crs, crs, ctx)
            p = transforms[key].transform(point)
            row.update(x=round(p.y(), m_decimals), y=round(p.x(), m_decimals),
                       subject=subject, msk=label)
        if feedback.isCanceled():
            return {}

        if empty:
            feedback.pushWarning(self.tr("Точки без геометрии: {}").format(_nums(empty)))
        if outside:
            feedback.pushWarning(self.tr(
                "Для точек {} МСК не найдена (не Россия или субъекта нет в наборе): "
                "X и Y пустые").format(_nums(outside)))

        # 2. Пишем таблицу по возрастанию номера
        fields = QgsFields()
        num_type = QMetaType.Type.Int
        if num_field:
            src_field = source.fields().field(num_field)
            num_type = src_field.type()
        for name, kind in (("num", num_type), ("lat_dms", QMetaType.Type.QString),
                           ("lon_dms", QMetaType.Type.QString), ("x_msk", QMetaType.Type.Double),
                           ("y_msk", QMetaType.Type.Double), ("subject", QMetaType.Type.QString),
                           ("msk", QMetaType.Type.QString)):
            field = QgsField(name, kind)
            if kind == QMetaType.Type.Double:
                # точность поля: таблица показывает «418060,50», а не «418060,5»
                field.setLength(20)
                field.setPrecision(m_decimals)
            if name == "num" and num_field:
                field = QgsField(src_field)
                field.setName("num")
            fields.append(field)
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, Qgis.WkbType.Point, src_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for row in sorted(rows, key=lambda r: core.sort_key(r["num"])):
            feature = QgsFeature(fields)
            if row["point"] is not None:
                feature.setGeometry(QgsGeometry.fromPointXY(row["point"]))
            feature.setAttributes([row["num"], row.get("lat_dms"), row.get("lon_dms"),
                                   row.get("x"), row.get("y"), row.get("subject"), row.get("msk")])
            sink.addFeature(feature, QgsFeatureSink.Flag.FastInsert)
        if hasattr(sink, "finalize"):
            sink.finalize()

        names = aliases(geo_name, [row.get("msk") for row in rows])
        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(_Aliases.create(names))
        feedback.setProgress(100)
        return {self.OUTPUT: dest_id, self.ALIASES: names}


def _nums(values, limit=15):
    text = ", ".join(str(v) for v in values[:limit])
    return text + (" …" if len(values) > limit else "")
