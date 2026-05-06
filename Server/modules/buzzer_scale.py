#!/usr/bin/env python3
"""Buzzer Scale — Play 7 musical notes (C4-B4).

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'buzzer' in hw and hw['buzzer'] and hw['buzzer']._initialized:
        buzzer = hw['buzzer']
        print("[Buzzer Scale] Playing C4-B4 scale...")
        scale_notes = [
            ('C4', 0.4), ('D4', 0.4), ('E4', 0.4), ('F4', 0.4),
            ('G4', 0.4), ('A4', 0.4), ('B4', 0.6),
        ]
        for note_name, duration in scale_notes:
            freq = buzzer.NOTES.get(note_name, 0)
            if freq > 0:
                print(f"  {note_name} ({freq}Hz)")
                if buzzer._is_passive:
                    buzzer._note_on_pwm(freq)
                else:
                    buzzer._note_on_active()
                time.sleep(duration)
                buzzer._note_off()
                time.sleep(0.1)
        print("[Buzzer Scale] Done.")
    else:
        from Server.hardware.buzzer import BuzzerController
        print("[Buzzer Scale] Playing C4-B4 scale...")
        buzzer = BuzzerController()
        if not buzzer._initialized:
            print("[Buzzer Scale] Buzzer not available!")
            return
        scale_notes = [
            ('C4', 0.4), ('D4', 0.4), ('E4', 0.4), ('F4', 0.4),
            ('G4', 0.4), ('A4', 0.4), ('B4', 0.6),
        ]
        for note_name, duration in scale_notes:
            freq = BuzzerController.NOTES.get(note_name, 0)
            if freq > 0:
                print(f"  {note_name} ({freq}Hz)")
                if buzzer._is_passive:
                    buzzer._note_on_pwm(freq)
                else:
                    buzzer._note_on_active()
                time.sleep(duration)
                buzzer._note_off()
                time.sleep(0.1)
        buzzer.shutdown()
        print("[Buzzer Scale] Done.")


if __name__ == '__main__':
    main()
