"""Координаты угловых точек: градусы-минуты-секунды, поле номера, субъект и МСК.

Без GUI и без iface: вызывается из алгоритма Processing и из тестов.
"""

import time

from . import msk

# msk.py и msk.json — копии из модуля project_utm_crs, правятся там.
# Представляемся сервису адресов своим именем, файл при этом не меняем.
# Подстановка здесь, а не в plugin.py: алгоритм Processing (в том числе через
# qgis_process) идёт через core.py, а plugin.py при этом не загружается.
msk.USER_AGENT = ("QGIS plugin corner_points_catalog "
                  "(https://github.com/Slider007/qgis-corner-points-catalog)")
# Там же текст об обрыве связи: в оригинале он зовёт нажать «Определить заново» —
# это кнопка окна модуля «СК проекта», у нас её нет. Что делать, дописывает алгоритм.
msk.NO_ANSWER = "Нет связи с сервисом адресов OpenStreetMap."

# Nominatim (OpenStreetMap) разрешает не больше одного запроса в секунду
NOMINATIM_DELAY = 1.0


# ------------------------------------------------------------ градусы, минуты, секунды

def dms_parts(value, decimals):
    """(знак, градусы, минуты, секунды в единицах 10^-decimals) с переносом 60″ → 1′."""
    scale = 10 ** decimals
    units = int(round(abs(value) * 3600 * scale))
    degrees, rest = divmod(units, 3600 * scale)
    minutes, seconds = divmod(rest, 60 * scale)
    return (-1 if value < 0 else 1), degrees, minutes, seconds


def format_dms(value, decimals=2):
    """55.27783 → «55°16′40,21″» (секунды с запятой, как в ведомостях)."""
    sign, degrees, minutes, seconds = dms_parts(value, decimals)
    scale = 10 ** decimals
    text = "{}°{}′{}".format(degrees, minutes, seconds // scale)
    if decimals > 0:
        text += ",{:0{}d}".format(seconds % scale, decimals)
    return ("-" if sign < 0 else "") + text + "″"


# ------------------------------------------------------------ поле номера

def find_number_field(names):
    """Поле с номером точки по названию: «№…», «Угол…», «Номер…», num. Или None."""
    def kind(name):
        low = name.lower().strip()
        if "№" in low or "угол" in low or "номер" in low:
            return 0
        if low in ("num", "number", "nom", "no", "n") or low.startswith("num"):
            return 1
        return None
    found = [(kind(n), i, n) for i, n in enumerate(names) if kind(n) is not None]
    return min(found)[2] if found else None


def plural(count, one, few, many):
    """«1 точка», «3 точки», «5 точек» — число и слово в нужном падеже."""
    tail, hundred = count % 10, count % 100
    if tail == 1 and hundred != 11:
        word = one
    elif 2 <= tail <= 4 and not 12 <= hundred <= 14:
        word = few
    else:
        word = many
    return "{} {}".format(count, word)


def sort_key(value):
    """Номера по возрастанию: числа как числа («2» раньше «10»), пустые — в конце."""
    if value is None:
        return (2, 0, "")
    try:
        return (0, float(str(value).replace(",", ".")), "")
    except ValueError:
        return (1, 0, str(value))


# ------------------------------------------------------------ субъект и МСК

class SubjectLookup:
    """Субъект РФ по точке через Nominatim, не чаще раза в delay секунд.

    Повторная точка с теми же координатами берётся из памяти."""

    def __init__(self, geocode=None, delay=NOMINATIM_DELAY, feedback=None):
        self.geocode = geocode or msk.reverse_geocode
        self.delay = delay
        self.feedback = feedback
        self.cache = {}
        self.last = None

    def subject(self, lon, lat):
        """(код субъекта или None, название) или исключение LookupError с текстом."""
        key = (round(lon, 7), round(lat, 7))
        if key in self.cache:
            return self.cache[key]
        self._wait()
        answer, error = self.geocode(lon, lat)
        self.last = time.monotonic()
        if error:
            raise LookupError(error)
        result = msk.region_from_reverse(answer)
        self.cache[key] = result
        return result

    def _wait(self):
        if self.last is None:
            return
        while time.monotonic() - self.last < self.delay:
            if self.feedback is not None and self.feedback.isCanceled():
                return
            time.sleep(0.05)


def msk_item(code, lon):
    """МСК субъекта для долготы: основной вариант с ближайшим осевым меридианом."""
    if code is None:
        return None
    zones = msk.zones_for(code, lon)
    return zones[0] if zones else None


def crs_label(crs):
    """Название СК для заголовков: «МСК-50 зона 1», если СК из набора."""
    item = msk.item_for_crs(crs)
    if item is not None:
        return msk.short_name(item)
    return crs.description() or crs.authid() or "МСК"
