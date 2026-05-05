"""
Buzzer module — supports both RobotHat pin and direct GPIO connection.

Two modes:
1. TonalBuzzer (gpiozero) — for active buzzers on RobotHat port (GPIO 5)
2. RPi.GPIO PWM — for passive buzzers on any free GPIO pin

Features:
- Play named melodies (happy_birthday, alarm, beep)
- Thread-safe playback
- Stop button support from web UI
- Configurable GPIO pin via config.py
"""

import threading
import time
from Server.config import BUZZER_PIN, BUZZER_GPIO_DIRECT


class BuzzerController:
    """Buzzer controller with melody playback — supports RobotHat + direct GPIO."""

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
        self._use_gpio_direct = False
        self._gpio_pin = None
        self._pwm = None

        # Try direct GPIO first if configured
        if BUZZER_GPIO_DIRECT is not None and BUZZER_GPIO_DIRECT > 0:
            if self._init_gpio_direct(BUZZER_GPIO_DIRECT):
                return

        # Fallback to TonalBuzzer (gpiozero) for RobotHat pin
        try:
            from gpiozero import TonalBuzzer
            self._buzzer = TonalBuzzer(BUZZER_PIN)
            self._initialized = True
            print(f"[Buzzer] Initialized via gpiozero TonalBuzzer on GPIO {BUZZER_PIN}")
        except Exception as e:
            print(f"[Buzzer] gpiozero failed: {e}")
            # Last resort: try direct GPIO on the RobotHat pin
            if self._init_gpio_direct(BUZZER_PIN):
                return
            print("[Buzzer] All initialization methods failed")

    def _init_gpio_direct(self, pin):
        """Initialize buzzer using RPi.GPIO PWM on a direct GPIO pin."""
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.OUT)
            self._pwm = GPIO.PWM(pin, 440)  # Start with 440Hz
            self._pwm.start(0)  # 0% duty = silent
            self._gpio_pin = pin
            self._use_gpio_direct = True
            self._initialized = True
            print(f"[Buzzer] Initialized via RPi.GPIO PWM on GPIO {pin}")
            return True
        except Exception as e:
            print(f"[Buzzer] RPi.GPIO direct init failed on GPIO {pin}: {e}")
            return False

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
                    self._note_off()
                else:
                    freq = self.NOTES[note_name]
                    self._note_on(freq)

                time.sleep(duration)

        finally:
            self._note_off()
            self._playing = False

    def _note_on(self, freq):
        """Play a note at the given frequency."""
        if self._use_gpio_direct and self._pwm is not None:
            try:
                self._pwm.ChangeFrequency(freq)
                self._pwm.ChangeDutyCycle(50)  # 50% duty cycle
            except Exception:
                pass
        elif self._buzzer is not None:
            try:
                from gpiozero.tones import Tone
                self._buzzer.play(Tone(freq))
            except Exception:
                self._buzzer.stop()

    def _note_off(self):
        """Stop playing current note."""
        if self._use_gpio_direct and self._pwm is not None:
            try:
                self._pwm.ChangeDutyCycle(0)  # 0% = silent
            except Exception:
                pass
        elif self._buzzer is not None:
            try:
                self._buzzer.stop()
            except Exception:
                pass

    def play_alarm(self):
        """Play low battery alarm."""
        self.play_melody("alarm")

    def beep(self):
        """Play a short beep."""
        self.play_melody("beep")

    def stop(self):
        """Stop current playback immediately."""
        self._flag.clear()
        self._note_off()

    def shutdown(self):
        """Clean shutdown."""
        self._running = False
        self.stop()
        if self._buzzer is not None:
            try:
                self._buzzer.close()
            except Exception:
                pass
        if self._use_gpio_direct and self._pwm is not None:
            try:
                self._pwm.stop()
                import RPi.GPIO as GPIO
                if self._gpio_pin is not None:
                    GPIO.cleanup(self._gpio_pin)
            except Exception:
                pass
        print("[Buzzer] Shutdown complete")
