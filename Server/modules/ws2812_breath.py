#!/usr/bin/env python3
"""WS2812 Breathing Light — Pulsing brightness effect."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.leds_ws2812 import LEDController


def main():
    print("[WS2812 Breath] Pulsing red... Press Ctrl+C to stop.")
    leds = LEDController()

    try:
        while True:
            leds.set_mode("breath", (255, 0, 0))
            time.sleep(5)
            leds.set_mode("breath", (0, 255, 0))
            time.sleep(5)
            leds.set_mode("breath", (0, 0, 255))
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        leds.shutdown()
        print("[WS2812 Breath] Done.")


if __name__ == '__main__':
    main()
