"""OLED SSD1306 display — 4 lines: IP, CPU, RAM, scrolling text."""

import threading
import time
from Server.config import OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT


class OLEDDisplay:

    SCROLL_TEXT = "modded by turik from 8241117 <3"
    SCROLL_WIDTH = 21

    def __init__(self):
        self._device = None
        self._running = True
        self._lines = ["PiCar Pro", "Starting...", "", ""]
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._initialized = False
        self._scroll_pos = 0
        self._scroll_text = self.SCROLL_TEXT
        self._scroll_pad = "   "

        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            serial = i2c(port=1, address=OLED_I2C_ADDR)
            self._device = ssd1306(serial, width=OLED_WIDTH, height=OLED_HEIGHT)
            self._initialized = True
            self._thread.start()
            print("[OLED] Initialized (scrolling text)")
        except Exception as e:
            print(f"[OLED] Init failed: {e}")

    def set_line(self, line_num, text):
        if 0 <= line_num < 4:
            with self._lock:
                self._lines[line_num] = str(text)[:21]

    def set_lines(self, lines):
        with self._lock:
            for i, line in enumerate(lines[:4]):
                self._lines[i] = str(line)[:21]

    def _get_scroll_window(self):
        full = self._scroll_text + self._scroll_pad
        repeated = full * 3
        pos = self._scroll_pos % len(full)
        return repeated[pos:pos + self.SCROLL_WIDTH]

    def _refresh_loop(self):
        from PIL import Image, ImageDraw, ImageFont

        while self._running:
            if not self._initialized or self._device is None:
                time.sleep(1)
                continue

            try:
                image = Image.new("1", (OLED_WIDTH, OLED_HEIGHT))
                draw = ImageDraw.Draw(image)
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
                except Exception:
                    font = ImageFont.load_default()

                with self._lock:
                    lines = self._lines[:3]

                scroll_line = self._get_scroll_window()

                for i, line in enumerate(lines):
                    draw.text((0, i * 16), line, fill=255, font=font)
                draw.text((0, 3 * 16), scroll_line, fill=255, font=font)

                self._device.display(image)
                self._scroll_pos += 1

            except Exception as e:
                print(f"[OLED] Refresh error: {e}")

            time.sleep(0.3)

    def shutdown(self):
        self._running = False
        if self._initialized and self._device is not None:
            try:
                self._device.cleanup()
            except Exception:
                pass
        print("[OLED] Shutdown")
