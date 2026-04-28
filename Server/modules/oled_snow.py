#!/usr/bin/env python3
"""OLED Snowflakes — Animated snowflake display on SSD1306."""

import sys
import os
import time
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import OLED_I2C_ADDR, OLED_WIDTH, OLED_HEIGHT


def main():
    print("[OLED Snow] Animated snowflakes... Press Ctrl+C to stop.")

    try:
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306
        from PIL import Image, ImageDraw
    except ImportError as e:
        print(f"  Error: {e}")
        return

    serial = i2c(port=1, address=OLED_I2C_ADDR)
    device = ssd1306(serial, width=OLED_WIDTH, height=OLED_HEIGHT)

    # Create snowflakes
    flakes = [(random.randint(0, OLED_WIDTH), random.randint(0, OLED_HEIGHT),
               random.choice([1, 2])) for _ in range(20)]

    try:
        while True:
            image = Image.new("1", (OLED_WIDTH, OLED_HEIGHT))
            draw = ImageDraw.Draw(image)

            new_flakes = []
            for x, y, size in flakes:
                draw.ellipse([x, y, x + size, y + size], fill=255)
                y += 2
                if y >= OLED_HEIGHT:
                    y = 0
                    x = random.randint(0, OLED_WIDTH)
                new_flakes.append((x, y, size))
            flakes = new_flakes

            device.display(image)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        device.cleanup()
        print("[OLED Snow] Done.")


if __name__ == '__main__':
    main()
