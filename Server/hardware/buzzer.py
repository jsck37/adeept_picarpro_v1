"""
Buzzer module (backported from v2).
Uses gpiozero TonalBuzzer for playing melodies.

Features:
- Play named melodies (happy_birthday, alarm, beep)
- Thread-safe playback
- Low battery alarm integration
"""

import threading
import time
from Server.config import BUZZER_PIN


class BuzzerController:
    """Tonal buzzer with melody playback."""

    # Musical note frequencies
    NOTES = {
        'C4': 262, 'D4': 294, 'E4': 330, 'F4': 349,
        'G4': 392, 'A4': 440, 'B4': 494,
        'C5': 523, 'D5': 587, 'E5': 659, 'F5': 698,
        'G5': 784, 'A5': 880,
        'REST': 0,
    }

    # Happy Birthday melody
    HAPPY_BIRTHDAY = [
        ('C4', 0.3), ('C4', 0.1), ('D4', 0.4), ('C4', 0.4),
        ('F4', 0.4), ('E4', 0.8),
        ('C4', 0.3), ('C4', 0.1), ('D4', 0.4), ('C4', 0.4),
        ('G4', 0.4), ('F4', 0.8),
        ('C4', 0.3), ('C4', 0.1), ('C5', 0.4), ('A4', 0.4),
        ('F4', 0.4), ('E4', 0.4), ('D4', 0.8),
        ('B4', 0.3), ('B4', 0.1), ('A4', 0.4), ('F4', 0.4),
        ('G4', 0.4), ('F4', 0.8),
    ]

    def __init__(self):
        self._buzzer = None
        self._running = True
        self._playing = False
        self._thread = None
        self._flag = threading.Event()
        self._flag.clear()
        self._initialized = False

        try:
            from gpiozero import TonalBuzzer
            self._buzzer = TonalBuzzer(BUZZER_PIN)
            self._initialized = True
            print("[Buzzer] Initialized on GPIO", BUZZER_PIN)
        except Exception as e:
            print(f"[Buzzer] Failed to initialize: {e}")

    def play_melody(self, melody_name="happy_birthday"):
        """
        Play a named melody in a background thread.
        
        Args:
            melody_name: 'happy_birthday', 'alarm', 'beep'
        """
        if not self._initialized:
            return

        if melody_name == "happy_birthday":
            notes = self.HAPPY_BIRTHDAY
        elif melody_name == "alarm":
            notes = [('A5', 0.2), ('REST', 0.1)] * 5
        elif melody_name == "beep":
            notes = [('A4', 0.15), ('REST', 0.1)]
        else:
            return

        # Stop any current playback
        self._flag.clear()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1)

        self._flag.set()
        self._thread = threading.Thread(
            target=self._play_notes, args=(notes,), daemon=True
        )
        self._thread.start()

    def _play_notes(self, notes):
        """Play a sequence of notes."""
        self._playing = True

        try:
            for note_name, duration in notes:
                if not self._flag.is_set():
                    break

                if note_name == 'REST' or note_name not in self.NOTES:
                    self._buzzer.stop()
                else:
                    freq = self.NOTES[note_name]
                    try:
                        from gpiozero.tones import Tone
                        self._buzzer.play(Tone(freq))
                    except Exception:
                        self._buzzer.stop()

                time.sleep(duration)

        finally:
            self._buzzer.stop()
            self._playing = False

    def play_alarm(self):
        """Play low battery alarm."""
        self.play_melody("alarm")

    def beep(self):
        """Play a short beep."""
        self.play_melody("beep")

    def stop(self):
        """Stop current playback."""
        self._flag.clear()
        if self._buzzer is not None:
            try:
                self._buzzer.stop()
            except Exception:
                pass

    def shutdown(self):
        """Clean shutdown."""
        self._running = False
        self.stop()
        if self._buzzer is not None:
            try:
                self._buzzer.close()
            except Exception:
                pass
        print("[Buzzer] Shutdown complete")
