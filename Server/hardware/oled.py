"""
OLED SSD1306 display module.
Shows 4 lines: IP:PORT, CPU info, RAM info, command status.

Display layout (SSD1306 128x64, 4 lines @ 16px each):
- Line 1: IP:PORT (e.g., "192.168.1.100:5000")
- Line 2: CPU: temp°C usage%
- Line 3: RAM: used/total GB percent%
- Line 4: Command status (running module or last command)

Improvements over v1:
- Structured 4-line display matching original PiCar Pro pattern
- Thread-safe updates
- Proper shutdown
- No battery display (not all hardware has ADS7830)
"""

import threading
import time
from Server.config import OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT


class OLEDDisplay:
    """SSD1306 OLED display controller with auto-refresh."""

    def __init__(self):
        self._device = None
        self._running = True
        self._lines = ["PiCar Pro", "Starting...", "", ""]
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._initialized = False

        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306

            serial = i2c(port=1, address=OLED_I2C_ADDR)
            self._device = ssd1306(serial, width=OLED_WIDTH, height=OLED_HEIGHT)
            self._initialized = True
            self._thread.start()
            print("[OLED] Display initialized (4-line mode)")
        except Exception as e:
            print(f"[OLED] Failed to initialize: {e}")

    def set_line(self, line_num, text):
        """Update a specific line of the display (0-3)."""
        if 0 <= line_num < 4:
            with self._lock:
                self._lines[line_num] = str(text)[:21]  # Max chars per line at 12pt

    def set_lines(self, lines):
        """Update all display lines at once."""
        with self._lock:
            for i, line in enumerate(lines[:4]):
                self._lines[i] = str(line)[:21]

    def _refresh_loop(self):
        """Periodically refresh the OLED display."""
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
                    lines = self._lines[:]

                for i, line in enumerate(lines):
                    draw.text((0, i * 16), line, fill=255, font=font)

                self._device.display(image)

            except Exception as e:
                print(f"[OLED] Refresh error: {e}")

            time.sleep(0.5)  # 2Hz refresh rate

    def show_startup(self):
        """Show startup message."""
        self.set_lines([
            "PiCar Pro",
            "Starting...",
            "",
            "",
        ])

    def show_status(self, ip, port, cpu_temp, cpu_usage, ram_used_mb, ram_total_mb, ram_percent, command="Ready"):
        """Show full status display (convenience method). RAM in MB for 1GB Pi."""
        self.set_lines([
            f"{ip}:{port}",
            f"CPU:{cpu_temp}C {cpu_usage}%",
            f"RAM:{ram_used_mb}/{ram_total_mb}M {ram_percent}%",
            command,
        ])

    def shutdown(self):
        """Clear display and release resources."""
        self._running = False
        if self._initialized and self._device is not None:
            try:
                self._device.cleanup()
            except Exception:
                pass
        print("[OLED] Shutdown complete")
