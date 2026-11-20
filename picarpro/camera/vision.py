"""Общие примитивы компьютерного зрения (HSV-маска, поиск пятна, линия).

Раньше одна и та же цепочка «RGB->BGR->HSV, inRange с переходом через 0° оттенка,
крупнейший контур, момент» была скопирована в несколько мест (trackHand,
findColor). Здесь она в одном экземпляре, без лишней конвертации
RGB->BGR->HSV (теперь RGB->HSV напрямую).
"""
try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:                 # pragma: no cover
    cv2 = None
    np = None
    HAS_CV2 = False


def downscale(frame, scale):
    """Уменьшить кадр в 1/scale раз по линейному размеру (scale <= 1)."""
    if not HAS_CV2 or scale >= 0.999:
        return frame
    h, w = frame.shape[:2]
    size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def hsv_mask(frame_rgb, low, high):
    """Бинарная маска пикселей кадра (RGB) внутри HSV-диапазона low..high.

    Если low.H > high.H, диапазон переходит через 0° (красный цвет) и
    собирается из двух частей.
    """
    hsv = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2HSV)
    if low[0] <= high[0]:
        return cv2.inRange(hsv, low, high)
    # dtype обязателен: cv2.inRange требует одинаковые типы границ, а смесь
    # Python-int и np.uint8 давала int64/uint8 и роняла вызов (поэтому
    # отслеживание красного цвета в исходной версии не работало вовсе)
    u8 = np.uint8
    m1 = cv2.inRange(hsv, np.array([0, low[1], low[2]], dtype=u8),
                     np.array([high[0], high[1], high[2]], dtype=u8))
    m2 = cv2.inRange(hsv, np.array([low[0], low[1], low[2]], dtype=u8),
                     np.array([179, high[1], high[2]], dtype=u8))
    return cv2.bitwise_or(m1, m2)


def largest_blob(mask, min_area):
    """Крупнейший внешний контур маски.

    Возвращает (cx, cy, area, contour) или None, если контуров нет, площадь
    меньше min_area либо момент вырожден (m00 == 0).
    """
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                cv2.CHAIN_APPROX_SIMPLE)[-2]
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < min_area:
        return None
    m = cv2.moments(c)
    if not m or m.get('m00', 0) <= 0:
        return None
    return int(m['m10'] / m['m00']), int(m['m01'] / m['m00']), int(area), c


# ---------------------------------------------------------------------------
# Линия на полу (v1.6.10, режим CV-Line)
# ---------------------------------------------------------------------------
# Раньше линию искали порогом Оцу по полосе кадра и брали СРЕДНЮЮ позицию всех
# «тёмных» пикселей строки. На белом фоне с чёрной линией это ломалось:
#  * если линии в полосе нет, Оцу всё равно делит шум на «тёмное/светлое» и
#    выдаёт ложную линию;
#  * тень, край листа, виньетирование объектива давали вторую «линию», а
#    среднее по двум пятнам лежало между ними — в пустоте.
# Теперь для каждой опорной строки (усреднённой по нескольким соседним, чтобы
# убрать шум): оценивается уровень фона и самый тёмный участок; линия есть, только
# если контраст достаточный; из всех тёмных отрезков выбирается ОДИН — самый
# подходящий по ширине и ближайший к прошлой позиции.

def row_profile(gray, y, pad):
    """Усреднённый по строкам y-pad..y+pad профиль яркости (1-D float32)."""
    h = gray.shape[0]
    top = max(0, y - pad)
    bot = min(h, y + pad + 1)
    if bot <= top:
        top, bot = max(0, h - 1), h
    prof = gray[top:bot].mean(axis=0, dtype=np.float32)
    k = 5
    if prof.shape[0] > k:                       # лёгкое сглаживание вдоль строки
        ker = np.ones(k, dtype=np.float32) / k
        prof = np.convolve(prof, ker, mode='same')
        prof[:k // 2] = prof[k // 2]
        prof[-(k // 2):] = prof[-(k // 2) - 1]
    return prof


def dark_runs(mask):
    """Отрезки подряд идущих True: список (start, end_exclusive)."""
    if not mask.any():
        return []
    m = np.concatenate(([False], mask, [False]))
    d = np.diff(m.astype(np.int8))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def find_line_in_profile(prof, prev_x=None, min_contrast=40, min_w=4,
                         max_w_frac=0.55, dark_on_light=True):
    """Найти линию в профиле яркости строки.

    Возвращает (x_центра, ширина, контраст) или None. dark_on_light=True —
    тёмная линия на светлом фоне (чёрная лента на белом), False — наоборот.
    """
    if prof is None or prof.shape[0] < 8:
        return None
    g = prof if dark_on_light else (255.0 - prof)
    n = g.shape[0]
    bg = float(np.percentile(g, 85))            # фон — светлый край распределения
    lo = float(g.min())
    contrast = bg - lo
    if contrast < min_contrast:                 # нет заметной тёмной полосы
        return None
    thr = lo + 0.5 * contrast                   # граница посередине «фон..линия»
    runs = dark_runs(g < thr)
    if not runs:
        return None
    max_w = max(min_w + 1, int(n * max_w_frac))
    best, best_score = None, None
    for a, b in runs:
        w = b - a
        if w < min_w or w > max_w:
            continue
        cx = 0.5 * (a + b - 1)
        # центр масс по «глубине тьмы» точнее геометрического центра отрезка
        weights = np.clip(thr - g[a:b], 0.0, None)
        if weights.sum() > 0:
            cx = float((np.arange(a, b) * weights).sum() / weights.sum())
        depth = float(bg - g[a:b].mean())
        score = depth + 0.15 * min(w, 40)       # глубже и шире — надёжнее
        if prev_x is not None:
            score -= 0.25 * abs(cx - prev_x) * (255.0 / n)   # непрерывность
        if best_score is None or score > best_score:
            best, best_score = (cx, w, contrast), score
    return best
