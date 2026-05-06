#!/usr/bin/env python3
"""LED Blink — Cycle through 3 on-board LEDs.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'switches' in hw and hw['switches'] and hw['switches']._initialized:
        print("[LED Blink] Starting... Press Ctrl+C to stop (module will auto-stop).")
        switches = hw['switches']
        for _ in range(5):  # 5 cycles instead of infinite
            for i in range(switches.count):
                print(f"  LED {i+1} ON")
                switches.on(i)
                time.sleep(0.5)
                switches.off(i)
                time.sleep(0.2)
        print("[LED Blink] Done.")
    else:
        from Server.hardware.switch import SwitchController
        print("[LED Blink] Starting... Press Ctrl+C to stop.")
        switches = SwitchController()
        try:
            while True:
                for i in range(3):
                    print(f"  LED {i+1} ON")
                    switches.on(i)
                    time.sleep(0.5)
                    switches.off(i)
                    time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            switches.shutdown()
            print("[LED Blink] Done.")


if __name__ == '__main__':
    main()
