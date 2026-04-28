#!/usr/bin/env python3
"""Buzzer Single Tone — Play a C4 note."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.buzzer import BuzzerController


def main():
    print("[Buzzer Tone] Playing C4 (262Hz) for 1 second...")
    buzzer = BuzzerController()
    buzzer.beep()
    time.sleep(1.5)
    buzzer.shutdown()
    print("[Buzzer Tone] Done.")


if __name__ == '__main__':
    main()
