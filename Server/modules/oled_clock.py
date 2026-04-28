#!/usr/bin/env python3
"""OLED Clock — Real-time clock display on SSD1306."""

import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT


def main():
    print("[OLED Clock] Showing clock... Press Ctrl+C to stop.")

    try:
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        print(f"  Error: {e}")
        return

    serial = i2c(port=1, address=OLED_I2C_ADDR)
    device = ssd1306(serial, width=OLED_WIDTH, height=OLED_HEIGHT)

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except Exception:
        font_large = ImageFont.load_default()
        font_small = font_large

    try:
        while True:
            image = Image.new("1", (OLED_WIDTH, OLED_HEIGHT))
            draw = ImageDraw.Draw(image)

            now = datetime.now()
            time_str = now.strftime("%H:%M:%S")
            date_str = now.strftime("%Y-%m-%d")

            # Center time on display
            draw.text((10, 10), time_str, fill=255, font=font_large)
            draw.text((30, 40), date_str, fill=255, font=font_small)

            device.display(image)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        device.cleanup()
        print("[OLED Clock] Done.")


if __name__ == '__main__':
    main()
