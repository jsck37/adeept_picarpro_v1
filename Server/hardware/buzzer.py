"""Buzzer — direct GPIO PWM via RPi.GPIO only (no RobotHat/gpiozero)."""

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
        self._gpio_pin = BUZZER_PIN
        self._pwm = None
        self._GPIO = None

        self._init_gpio()

    def _init_gpio(self):
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self._gpio_pin, GPIO.OUT)
            self._pwm = GPIO.PWM(self._gpio_pin, 440)
            self._pwm.start(0)
            self._GPIO = GPIO
            self._initialized = True
            print(f"[Buzzer] RPi.GPIO PWM on GPIO {self._gpio_pin}")
        except ImportError:
            print("[Buzzer] RPi.GPIO not available!")
        except Exception as e:
            print(f"[Buzzer] GPIO {self._gpio_pin} init failed: {e}")

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
        if self._pwm is not None:
            try:
                self._pwm.ChangeFrequency(freq)
                self._pwm.ChangeDutyCycle(50)
            except Exception:
                pass

    def _note_off(self):
        if self._pwm is not None:
            try:
                self._pwm.ChangeDutyCycle(0)
            except Exception:
                pass

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
        print("[Buzzer] Shutdown")
