#!/usr/bin/env python3
"""Buzzer Scale — Play 7 musical notes (C4-B4)."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.buzzer import BuzzerController


def main():
    print("[Buzzer Scale] Playing C4-B4 scale...")
    buzzer = BuzzerController()

    scale = [
        ('C4', 0.4), ('D4', 0.4), ('E4', 0.4), ('F4', 0.4),
        ('G4', 0.4), ('A4', 0.4), ('B4', 0.6),
    ]

    for note, dur in scale:
        freq = BuzzerController.NOTES.get(note, 0)
        if freq > 0:
            print(f"  {note} ({freq}Hz)")
            try:
                from gpiozero.tones import Tone
                buzzer._buzzer.play(Tone(freq))
            except Exception:
                pass
            time.sleep(dur)
            buzzer._buzzer.stop()
            time.sleep(0.1)

    buzzer.shutdown()
    print("[Buzzer Scale] Done.")


if __name__ == '__main__':
    main()
