import threading, time
from config import (
    OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT,
    OLED_LOW_VOLTAGE_TEXT,
)
from Server.logger import logger


class OLEDDisplay:
    """SSD1306 OLED driver.

    Layout (128x64):
      Line 0 (y=0)   — IP:port
      Line 1 (y=16)  — CPU / RAM
      Line 2 (y=32)  — extra status line
      Line 3 (y=48)  — normally the project tag; replaced by the
                       "Low voltage warning..." message when the
                       under-voltage flag is active.
    """

    def __init__(self):
        self._device = None
        self._running = True
        self._lines = ["PiCar Pro", "Starting...", "", ""]
        self._lock = threading.Lock()
        self._initialized = False
        self._low_voltage = False
        self._blink_state = False
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            self._device = ssd1306(
                i2c(port=1, address=OLED_I2C_ADDR),
                width=OLED_WIDTH, height=OLED_HEIGHT,
            )
            self._initialized = True
            threading.Thread(target=self._loop, daemon=True).start()
            logger.info("[OLED] OK")
        except Exception as e:
            logger.error(f"[OLED] Failed: {e}")

    def set_lines(self, lines):
        with self._lock:
            for i, l in enumerate(lines[:4]):
                self._lines[i] = str(l)[:21]

    def set_low_voltage(self, active: bool):
        """Toggle the low-voltage warning overlay (replaces line 4)."""
        with self._lock:
            if active != self._low_voltage:
                self._low_voltage = active
                logger.warning(f"[OLED] Low-voltage warning: {'ON' if active else 'OFF'}")

    @property
    def low_voltage(self) -> bool:
        with self._lock:
            return self._low_voltage

    # Legacy API kept for back-compat (no-op, low voltage replaces scrolling).
    def set_scroll_text(self, text):
        pass

    def _loop(self):
        from PIL import Image, ImageDraw, ImageFont
        try:
            font_big = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12
            )
        except Exception:
            try:
                font_big = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12
                )
            except Exception:
                font_big = ImageFont.load_default()
        try:
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10
            )
        except Exception:
            font_small = font_big

        # Pre-split the warning text into rows that fit on a 128px OLED.
        warn_text = OLED_LOW_VOLTAGE_TEXT
        # Split on the natural boundary between the two sentences:
        #   "Low voltage warning"  +  "Please check your power supply"
        warn_rows = []
        if "Please" in warn_text:
            head, tail = warn_text.split("Please", 1)
            warn_rows = [head.strip(), "Please" + tail.strip()]
        else:
            warn_rows = [warn_text]

        while self._running:
            if not self._device:
                time.sleep(1)
                continue
            try:
                img = Image.new("1", (OLED_WIDTH, OLED_HEIGHT))
                draw = ImageDraw.Draw(img)
                with self._lock:
                    lines = list(self._lines[:3])
                    warn = self._low_voltage

                # Always render the three info lines (y = 0 / 16 / 32).
                for i, l in enumerate(lines[:3]):
                    draw.text((0, i * 16), l, fill=255, font=font_big)

                if warn:
                    # Blink the bottom band so the warning is impossible
                    # to miss — even from across the room. The text itself
                    # stays visible at all times; only the background
                    # flashes between inverted and normal.
                    self._blink_state = not self._blink_state
                    band = (0, 48, OLED_WIDTH - 1, OLED_HEIGHT - 1)
                    if self._blink_state:
                        draw.rectangle(band, outline=255, fill=255)
                        text_fill = 0    # black text on white background
                    else:
                        draw.rectangle(band, outline=255, fill=0)
                        text_fill = 255  # white text on black background
                    # Wrap the warning text across the two available rows.
                    if len(warn_rows) >= 2:
                        draw.text((1, 48), warn_rows[0], fill=text_fill, font=font_small)
                        draw.text((1, 56), warn_rows[1], fill=text_fill, font=font_small)
                    else:
                        draw.text((1, 52), warn_rows[0], fill=text_fill, font=font_small)
                else:
                    # Normal line 4: short project tag.
                    draw.text((0, 50), "PiCar Pro v1", fill=255, font=font_small)

                self._device.display(img)
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
