#!/usr/bin/env python3
"""LED Blink — Cycle through 3 on-board LEDs."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.switch import SwitchController


def main():
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
