#!/usr/bin/env python3
"""WS2812 Flowing Lights — Color chase animation."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.leds_ws2812 import LEDController


def main():
    print("[WS2812 Flow] Color chase... Press Ctrl+C to stop.")
    leds = LEDController()

    try:
        while True:
            leds.set_mode("flowing")
            time.sleep(8)
            leds.set_mode("rainbow")
            time.sleep(8)
    except KeyboardInterrupt:
        pass
    finally:
        leds.shutdown()
        print("[WS2812 Flow] Done.")


if __name__ == '__main__':
    main()
