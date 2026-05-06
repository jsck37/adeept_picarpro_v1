#!/usr/bin/env python3
"""WS2812 Breathing Light — Pulsing brightness effect.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'leds' in hw and hw['leds'] and hw['leds']._initialized:
        print("[WS2812 Breath] Pulsing 3 colors...")
        leds = hw['leds']
        for color, name in [((255,0,0), "Red"), ((0,255,0), "Green"), ((0,0,255), "Blue")]:
            print(f"  {name}")
            leds.set_mode("breath", color)
            time.sleep(4)
        leds.set_mode("off")
        print("[WS2812 Breath] Done.")
    else:
        from Server.hardware.leds_ws2812 import LEDController
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
