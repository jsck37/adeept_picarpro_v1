import threading, time
from picarpro.logger import logger
from config import (I2C_BUS, OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT,
                    OLED_LOW_VOLTAGE_TEXT, OLED_FONT_SIZE, OLED_MARQUEE_TEXT)

# --- геометрия/тайминг нижней зоны экрана (пунктир + бегущая строка) ---
LINE_SPACING = 14       # межстрочный интервал верхних трёх строк, px
SEPARATOR_Y = 43        # пунктирный разделитель над бегущей строкой
MARQUEE_Y = 46          # верх зоны бегущей строки
MARQUEE_GAP = 24        # зазор между повторами текста, px
SCROLL_STEP = 2         # сдвиг текста за кадр, px
FRAME_INTERVAL = 0.12   # период кадра прокрутки, с (~8 FPS)
BLINK_HALF = 0.4        # полупериод мигания предупреждения, с
_WARN_MAX_W = OLED_WIDTH - 2

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    _HAS_OLED = True
except ImportError:
    _HAS_OLED = False
    i2c = None
    ssd1306 = None


class OLEDDisplay:
    def __init__(self):
        self._device = None
        self._running = True
        self._lines = ['PiCar Pro', 'Starting...', '', '']
        self._lock = threading.Lock()
        self._low_voltage = False
        self._initialized = False
        self._wake = threading.Event()
        if not _HAS_OLED:
            logger.warning('[OLED] luma.oled not available')
            return
        try:
            self._device = ssd1306(i2c(port=I2C_BUS, address=OLED_I2C_ADDR),
                                   width=OLED_WIDTH, height=OLED_HEIGHT)
            self._initialized = True
            threading.Thread(target=self._loop, daemon=True).start()
            logger.info('[OLED] OK')
        except Exception as e:
            logger.error(f'[OLED] init failed: {e}')

    def set_lines(self, lines):
        with self._lock:
            for i, l in enumerate(lines[:4]):
                self._lines[i] = str(l)[:21]
        self._wake.set()

    def set_low_voltage(self, active):
        with self._lock:
            if active == self._low_voltage:
                return
            self._low_voltage = active
        self._wake.set()

    @staticmethod
    def _load_fonts():
        """Шрифты по размерам: bold — для строк данных, regular — для низа.
        Отсутствующий шрифт просто не попадает в словарь (draw.text с
        font=None использует встроенный растровый шрифт Pillow)."""
        from PIL import ImageFont
        sizes = sorted({OLED_FONT_SIZE, OLED_FONT_SIZE - 1, 7})
        base = '/usr/share/fonts/truetype/dejavu/'
        bold, reg = {}, {}
        for s in sizes:
            try:
                bold[s] = ImageFont.truetype(base + 'DejaVuSans-Bold.ttf', s)
            except Exception:
                pass
            try:
                reg[s] = ImageFont.truetype(base + 'DejaVuSans.ttf', s)
            except Exception:
                pass
        return bold, reg

    @staticmethod
    def _build_marquee(font):
        """Битовая карта бегущей строки: текст + зазор для бесшовного повтора."""
        from PIL import Image, ImageDraw
        if not OLED_MARQUEE_TEXT:
            return None, 0
        try:
            tmp = ImageDraw.Draw(Image.new('1', (8, 8)))
            w = int(tmp.textlength(OLED_MARQUEE_TEXT, font=font)) + MARQUEE_GAP
            h = OLED_HEIGHT - MARQUEE_Y
            img = Image.new('1', (max(w, 1), h))
            y = max(0, (h - OLED_FONT_SIZE) // 2)
            ImageDraw.Draw(img).text((0, y), OLED_MARQUEE_TEXT,
                                     fill=255, font=font)
            return img, w
        except Exception:
            return None, 0

    @staticmethod
    def _layout_warning(draw, reg):
        """Предупреждение о питании: перенос по словам на <=2 строки,
        помещающиеся в 128 px, с автоподбором размера (9 -> 8 -> 7 px)."""
        text = str(OLED_LOW_VOLTAGE_TEXT)
        for size in (OLED_FONT_SIZE, OLED_FONT_SIZE - 1, 7):
            font = reg.get(size)
            if font is None:
                break
            rows, cur = [], ''
            ok = True
            for word in text.split():
                cand = f'{cur} {word}'.strip()
                if draw.textlength(cand, font=font) <= _WARN_MAX_W:
                    cur = cand
                    continue
                if cur:
                    rows.append(cur)
                cur = word
                if len(rows) >= 2 or draw.textlength(word, font=font) > _WARN_MAX_W:
                    ok = False
                    break
            if ok:
                if cur:
                    rows.append(cur)
                if len(rows) <= 2:
                    return rows, font, size
        # встроенный шрифт: грубое деление по 'Please' (прежнее поведение)
        if 'Please' in text:
            head, tail = text.split('Please', 1)
            return [head.strip(), 'Please' + tail.strip()], None, 8
        return [text], None, 8

    def _loop(self):
        from PIL import Image, ImageDraw
        bold, reg = self._load_fonts()
        font_line = bold.get(OLED_FONT_SIZE)
        font_low = reg.get(OLED_FONT_SIZE)
        tmp = ImageDraw.Draw(Image.new('1', (8, 8)))
        marquee_img, marquee_w = self._build_marquee(font_low)
        warn_rows, warn_font, warn_size = self._layout_warning(tmp, reg)
        logger.info(f'[OLED] font {OLED_FONT_SIZE}px, marquee '
                    f'{marquee_w or "off"}px, warn rows: {len(warn_rows)}')
        next_frame = 0.0
        scroll_x = 0
        last_key = None
        while self._running:
            if not self._device:
                time.sleep(1)
                continue
            try:
                now = time.monotonic()
                with self._lock:
                    lines = list(self._lines[:3])
                    warn = self._low_voltage
                blink = (int(now / BLINK_HALF) % 2 == 0) if warn else None
                key = (tuple(lines), warn, blink)
                scrolling = bool(marquee_w > OLED_WIDTH) and not warn
                due = now >= next_frame
                if not due and key == last_key:
                    self._wake.wait(0.05)
                    self._wake.clear()
                    continue
                if due:
                    next_frame = now + FRAME_INTERVAL
                    if scrolling:
                        scroll_x = (scroll_x + SCROLL_STEP) % marquee_w
                last_key = key
                img = Image.new('1', (OLED_WIDTH, OLED_HEIGHT))
                draw = ImageDraw.Draw(img)
                for i, l in enumerate(lines[:3]):
                    draw.text((0, i * LINE_SPACING), l, fill=255,
                              font=font_line)
                if warn:
                    band = (0, MARQUEE_Y - 2, OLED_WIDTH - 1, OLED_HEIGHT - 1)
                    if blink:
                        draw.rectangle(band, outline=255, fill=255)
                        fill = 0
                    else:
                        draw.rectangle(band, outline=255, fill=0)
                        fill = 255
                    y = MARQUEE_Y - 1
                    for r in warn_rows:
                        draw.text((1, y), r, fill=fill, font=warn_font)
                        y += warn_size + 1
                elif marquee_img is not None:
                    for x in range(0, OLED_WIDTH, 2):
                        draw.point((x, SEPARATOR_Y), fill=255)
                    img.paste(marquee_img, (-scroll_x, MARQUEE_Y))
                    if marquee_w - scroll_x < OLED_WIDTH:
                        img.paste(marquee_img,
                                  (marquee_w - scroll_x, MARQUEE_Y))
                self._device.display(img)
            except Exception:
                pass
            self._wake.wait(0.05)
            self._wake.clear()

    def shutdown(self):
        self._running = False
        self._wake.set()
        if self._device:
            try:
                self._device.cleanup()
            except Exception:
                pass
        logger.info('[OLED] shutdown')
