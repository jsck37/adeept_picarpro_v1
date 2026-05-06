#!/usr/bin/env python3
"""Buzzer Single Tone — Play a C4 note.

Uses injected hardware from the running server (no GPIO conflicts).
Can also run standalone if executed directly.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'buzzer' in hw and hw['buzzer'] and hw['buzzer']._initialized:
        print("[Buzzer Tone] Playing C4 (262Hz) for 1 second...")
        buzzer = hw['buzzer']
        buzzer.beep()
        time.sleep(1.5)
        buzzer.stop()
        print("[Buzzer Tone] Done.")
    else:
        # Standalone mode — create own instance
        from Server.hardware.buzzer import BuzzerController
        print("[Buzzer Tone] Playing C4 (262Hz) for 1 second...")
        buzzer = BuzzerController()
        buzzer.beep()
        time.sleep(1.5)
        buzzer.shutdown()
        print("[Buzzer Tone] Done.")


if __name__ == '__main__':
    main()
