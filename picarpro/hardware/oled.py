import threading, time
from picarpro.logger import logger
from config import (I2C_BUS, OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT,
                    OLED_LOW_VOLTAGE_TEXT)

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
        self._blink = False
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

    def _loop(self):
        from PIL import Image, ImageDraw, ImageFont
        try:
            font_big = ImageFont.truetype(
                '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
        except Exception:
            try:
                font_big = ImageFont.truetype(
                    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 12)
            except Exception:
                font_big = ImageFont.load_default()
        try:
            font_small = ImageFont.truetype(
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 10)
        except Exception:
            font_small = font_big

        warn_text = OLED_LOW_VOLTAGE_TEXT
        if 'Please' in warn_text:
            head, tail = warn_text.split('Please', 1)
            warn_rows = [head.strip(), 'Please' + tail.strip()]
        else:
            warn_rows = [warn_text]

        last_key = None
        while self._running:
            if not self._device:
                time.sleep(1)
                continue
            try:
                with self._lock:
                    lines = list(self._lines[:3])
                    warn = self._low_voltage
                if warn:
                    self._blink = not self._blink
                key = (tuple(lines), warn, self._blink if warn else None)
                if key == last_key:
                    self._wake.wait(0.3 if warn else 1.0)
                    self._wake.clear()
                    continue
                last_key = key
                img = Image.new('1', (OLED_WIDTH, OLED_HEIGHT))
                draw = ImageDraw.Draw(img)
                for i, l in enumerate(lines[:3]):
                    draw.text((0, i * 16), l, fill=255, font=font_big)
                if warn:
                    band = (0, 48, OLED_WIDTH - 1, OLED_HEIGHT - 1)
                    if self._blink:
                        draw.rectangle(band, outline=255, fill=255)
                        text_fill = 0
                    else:
                        draw.rectangle(band, outline=255, fill=0)
                        text_fill = 255
                    if len(warn_rows) >= 2:
                        draw.text((1, 48), warn_rows[0], fill=text_fill, font=font_small)
                        draw.text((1, 56), warn_rows[1], fill=text_fill, font=font_small)
                    else:
                        draw.text((1, 52), warn_rows[0], fill=text_fill, font=font_small)
                else:
                    draw.text((0, 50), 'PiCar Pro', fill=255, font=font_small)
                self._device.display(img)
            except Exception:
                pass
            self._wake.wait(0.3 if self._low_voltage else 1.0)
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
