#!/usr/bin/env python3
"""Happy Birthday — Play the Happy Birthday melody.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'buzzer' in hw and hw['buzzer'] and hw['buzzer']._initialized:
        print("[Happy Birthday] Playing melody...")
        buzzer = hw['buzzer']
        buzzer.play_melody("happy_birthday")
        time.sleep(8)
        buzzer.stop()
        print("[Happy Birthday] Done.")
    else:
        from Server.hardware.buzzer import BuzzerController
        print("[Happy Birthday] Playing melody...")
        buzzer = BuzzerController()
        buzzer.play_melody("happy_birthday")
        time.sleep(8)
        buzzer.shutdown()
        print("[Happy Birthday] Done.")


if __name__ == '__main__':
    main()
