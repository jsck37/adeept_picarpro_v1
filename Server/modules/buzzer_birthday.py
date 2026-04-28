#!/usr/bin/env python3
"""Happy Birthday — Play the Happy Birthday melody."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.buzzer import BuzzerController


def main():
    print("[Happy Birthday] Playing melody...")
    buzzer = BuzzerController()
    buzzer.play_melody("happy_birthday")

    import time
    time.sleep(8)  # Wait for melody to finish

    buzzer.shutdown()
    print("[Happy Birthday] Done.")


if __name__ == '__main__':
    main()
