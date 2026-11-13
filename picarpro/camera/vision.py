"""Общие примитивы компьютерного зрения (HSV-маска, поиск пятна).

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
