"""Buzzer — plays tones and melodies on an active buzzer via GPIO PWM.

Supports two drivers (tried in order):
1. RPi.GPIO software PWM — works on any GPIO pin
2. gpiozero.TonalBuzzer — simpler API but may conflict with other gpiozero
   devices on the same pin

Common issue on PiCar Pro v1: GPIO 24 is used for the buzzer. If gpiozero
has already claimed GPIO 24 (e.g. for DistanceSensor echo), RPi.GPIO will
fail. This module handles that gracefully by trying both drivers.
"""

import threading
import time
from Server.config import BUZZER_PIN


class BuzzerController:

    NOTES = {
        'C4': 262, 'D4': 294, 'E4': 330, 'F4': 349,
        'G4': 392, 'A4': 440, 'B4': 494,
        'C5': 523, 'D5': 587, 'E5': 659, 'F5': 698,
        'G5': 784, 'A5': 880,
        'REST': 0,
    }

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
        self._running = True
        self._playing = False
        self._thread = None
        self._flag = threading.Event()
        self._flag.clear()
        self._initialized = False
        self._driver = None      # 'rpigpio' or 'gpiozero'
        self._gpio_pin = BUZZER_PIN
        self._pwm = None         # RPi.GPIO PWM instance
        self._GPIO = None        # RPi.GPIO module ref
        self._buzzer = None      # gpiozero.TonalBuzzer instance

        self._try_rpi_gpio()
        if not self._initialized:
            self._try_gpiozero()

    def _try_rpi_gpio(self):
        """Try RPi.GPIO software PWM — most reliable for active buzzers."""
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self._gpio_pin, GPIO.OUT)
            initial = GPIO.PWM(self._gpio_pin, 440)
            initial.start(0)   # Start with 0% duty cycle (silent)
            self._pwm = initial
            self._GPIO = GPIO
            self._driver = 'rpigpio'
            self._initialized = True
            print(f"[Buzzer] RPi.GPIO PWM on GPIO {self._gpio_pin}")
        except ImportError:
            print("[Buzzer] RPi.GPIO not available")
        except Exception as e:
            print(f"[Buzzer] RPi.GPIO GPIO {self._gpio_pin} failed: {e}")
            # Pin might be claimed by gpiozero — try cleanup
            self._try_rpi_gpio_cleanup()

    def _try_rpi_gpio_cleanup(self):
        """Force-release the GPIO pin and try again."""
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.cleanup(self._gpio_pin)
            time.sleep(0.05)
            GPIO.setup(self._gpio_pin, GPIO.OUT)
            initial = GPIO.PWM(self._gpio_pin, 440)
            initial.start(0)
            self._pwm = initial
            self._GPIO = GPIO
            self._driver = 'rpigpio'
            self._initialized = True
            print(f"[Buzzer] RPi.GPIO PWM on GPIO {self._gpio_pin} (after cleanup)")
        except Exception as e2:
            print(f"[Buzzer] RPi.GPIO cleanup retry failed: {e2}")

    def _try_gpiozero(self):
        """Fallback: gpiozero TonalBuzzer."""
        try:
            from gpiozero import TonalBuzzer
            self._buzzer = TonalBuzzer(self._gpio_pin)
            self._driver = 'gpiozero'
            self._initialized = True
            print(f"[Buzzer] gpiozero TonalBuzzer on GPIO {self._gpio_pin}")
        except Exception as e:
            print(f"[Buzzer] gpiozero fallback failed: {e}")
            print(f"[Buzzer] Buzzer is NOT available on GPIO {self._gpio_pin}")

    # ── Melody playback ─────────────────────────────────────────────────

    def play_melody(self, melody_name="happy_birthday"):
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

        self._flag.clear()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1)

        self._flag.set()
        self._thread = threading.Thread(
            target=self._play_notes, args=(notes,), daemon=True
        )
        self._thread.start()

    def _play_notes(self, notes):
        self._playing = True
        try:
            for note_name, duration in notes:
                if not self._flag.is_set():
                    break
                if note_name == 'REST' or note_name not in self.NOTES:
                    self._note_off()
                else:
                    self._note_on(self.NOTES[note_name])
                time.sleep(duration)
        finally:
            self._note_off()
            self._playing = False

    def _note_on(self, freq):
        if self._driver == 'rpigpio' and self._pwm is not None:
            try:
                self._pwm.ChangeFrequency(freq)
                self._pwm.ChangeDutyCycle(50)
            except Exception:
                pass
        elif self._driver == 'gpiozero' and self._buzzer is not None:
            try:
                from gpiozero.tones import Tone
                self._buzzer.play(Tone(freq))
            except Exception:
                pass

    def _note_off(self):
        if self._driver == 'rpigpio' and self._pwm is not None:
            try:
                self._pwm.ChangeDutyCycle(0)
            except Exception:
                pass
        elif self._driver == 'gpiozero' and self._buzzer is not None:
            try:
                self._buzzer.stop()
            except Exception:
                pass

    # ── Public API ──────────────────────────────────────────────────────

    def play_alarm(self):
        self.play_melody("alarm")

    def beep(self):
        self.play_melody("beep")

    def stop(self):
        self._flag.clear()
        self._note_off()

    def shutdown(self):
        self._running = False
        self.stop()
        if self._driver == 'rpigpio':
            if self._pwm is not None:
                try:
                    self._pwm.stop()
                except Exception:
                    pass
            if self._GPIO is not None and self._gpio_pin is not None:
                try:
                    self._GPIO.cleanup(self._gpio_pin)
                except Exception:
                    pass
        elif self._driver == 'gpiozero':
            if self._buzzer is not None:
                try:
                    self._buzzer.close()
                except Exception:
                    pass
        print("[Buzzer] Shutdown")
