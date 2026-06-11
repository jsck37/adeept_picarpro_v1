import threading, time
from config import OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT, OLED_SCROLL_TEXT
from Server.logger import logger

class OLEDDisplay:

    def __init__(self):
        self._device = None
        self._running = True
        self._lines = ["PiCar Pro", "Starting...", "", ""]
        self._lock = threading.Lock()
        self._initialized = False
        self._scroll_pos = 0
        self._scroll_text = OLED_SCROLL_TEXT
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            self._device = ssd1306(i2c(port=1, address=OLED_I2C_ADDR), width=OLED_WIDTH, height=OLED_HEIGHT)
            self._initialized = True
            threading.Thread(target=self._loop, daemon=True).start()
            logger.info("[OLED] OK")
        except Exception as e:
            logger.error(f"[OLED] Failed: {e}")

    def set_lines(self, lines):
        with self._lock:
            for i, l in enumerate(lines[:4]):
                self._lines[i] = str(l)[:21]

    def set_scroll_text(self, text):
        with self._lock:
            self._scroll_text = text

    def _loop(self):
        from PIL import Image, ImageDraw, ImageFont
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except Exception:
            font = ImageFont.load_default()
        while self._running:
            if not self._device:
                time.sleep(1)
                continue
            try:
                img = Image.new("1", (OLED_WIDTH, OLED_HEIGHT))
                draw = ImageDraw.Draw(img)
                with self._lock:
                    lines = self._lines[:3]
                    scroll_text = self._scroll_text
                scroll = scroll_text * 3
                pos = self._scroll_pos % len(scroll_text) if scroll_text else 0
                for i, l in enumerate(lines):
                    draw.text((0, i * 16), l, fill=255, font=font)
                draw.text((0, 48), scroll[pos:pos+21], fill=255, font=font)
                self._device.display(img)
                self._scroll_pos += 1
            except Exception:
                pass
            time.sleep(0.3)

    def shutdown(self):
        self._running = False
        if self._device:
            try:
                self._device.cleanup()
            except Exception:
                pass
        logger.info("[OLED] Shutdown")
