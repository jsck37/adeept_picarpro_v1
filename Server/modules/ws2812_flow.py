#!/usr/bin/env python3
"""WS2812 Flowing Lights — Color chase animation.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'leds' in hw and hw['leds'] and hw['leds']._initialized:
        print("[WS2812 Flow] Color chase animation...")
        leds = hw['leds']
        leds.set_mode("flow")
        time.sleep(8)
        leds.set_mode("rainbow")
        time.sleep(8)
        leds.set_mode("off")
        print("[WS2812 Flow] Done.")
    else:
        from Server.hardware.leds_ws2812 import LEDController
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
