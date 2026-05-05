#!/usr/bin/env python3
"""Buzzer Scale — Play 7 musical notes (C4-B4) using direct note control.

Fixed: original played "beep" for every note instead of the actual frequency.
Now plays each note at its correct frequency.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.buzzer import BuzzerController


def main():
    print("[Buzzer Scale] Playing C4-B4 scale...")
    buzzer = BuzzerController()

    if not buzzer._initialized:
        print("[Buzzer Scale] Buzzer not available!")
        return

    # Build a melody from individual notes at their correct frequencies
    scale_notes = [
        ('C4', 0.4), ('D4', 0.4), ('E4', 0.4), ('F4', 0.4),
        ('G4', 0.4), ('A4', 0.4), ('B4', 0.6),
    ]

    for note_name, duration in scale_notes:
        freq = BuzzerController.NOTES.get(note_name, 0)
        if freq > 0:
            print(f"  {note_name} ({freq}Hz)")
            buzzer._note_on(freq)
            time.sleep(duration)
            buzzer._note_off()
            time.sleep(0.1)

    buzzer.shutdown()
    print("[Buzzer Scale] Done.")


if __name__ == '__main__':
    main()
