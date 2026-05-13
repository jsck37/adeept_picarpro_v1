"""Buzzer — plays tones and melodies on the RobotHat buzzer.

RobotHat buzzer is PASSIVE (no internal oscillator).
Passive buzzers need PWM at the correct frequency to produce tones.
Each musical note corresponds to a specific PWM frequency.

Driver order:
1. RPi.GPIO — PWM with frequency control for passive buzzer
2. gpiozero.TonalBuzzer — simpler API fallback

If you have an ACTIVE buzzer (built-in oscillator, fixed pitch), set
BUZZER_PASSIVE=False in config.py and simple on/off will be used.
"""

import threading
import time
from Server.config import BUZZER_PIN

try:
    from Server.config import BUZZER_PASSIVE
except ImportError:
    BUZZER_PASSIVE = True


class BuzzerController:

    NOTES = {
        'C3': 131, 'D3': 147, 'E3': 165, 'F3': 175, 'G3': 196,
        'A3': 220, 'B3': 247,
        'C4': 262, 'D4': 294, 'E4': 330, 'F4': 349,
        'G4': 392, 'A4': 440, 'B4': 494,
        'C5': 523, 'D5': 587, 'E5': 659, 'F5': 698,
        'G5': 784, 'A5': 880, 'B5': 988,
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
        self._driver = None        # 'rpigpio' or 'gpiozero'
        self._gpio_pin = BUZZER_PIN
        self._pwm = None           # RPi.GPIO PWM instance (passive mode)
        self._GPIO = None          # RPi.GPIO module ref
        self._buzzer = None        # gpiozero TonalBuzzer instance
        self._is_passive = BUZZER_PASSIVE
        self._current_freq = 0     # Track current PWM frequency

        self._try_rpi_gpio()
        if not self._initialized:
            self._try_gpiozero()

    def _try_rpi_gpio(self):
        """Try RPi.GPIO — PWM for passive, on/off for active."""
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self._gpio_pin, GPIO.OUT)

            if self._is_passive:
                # Passive buzzer: start PWM at 440Hz, 0% duty (silent)
                pwm = GPIO.PWM(self._gpio_pin, 440)
                pwm.start(0)
                self._pwm = pwm
            else:
                # Active buzzer: start with LOW (silent)
                GPIO.output(self._gpio_pin, GPIO.LOW)

            self._GPIO = GPIO
            self._driver = 'rpigpio'
            self._initialized = True
            mode = "PWM (passive)" if self._is_passive else "on/off (active)"
            print(f"[Buzzer] RPi.GPIO {mode} on GPIO {self._gpio_pin}")
        except ImportError:
            print("[Buzzer] RPi.GPIO not available")
        except Exception as e:
            print(f"[Buzzer] RPi.GPIO GPIO {self._gpio_pin} failed: {e}")
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

            if self._is_passive:
                pwm = GPIO.PWM(self._gpio_pin, 440)
                pwm.start(0)
                self._pwm = pwm
            else:
                GPIO.output(self._gpio_pin, GPIO.LOW)

            self._GPIO = GPIO
            self._driver = 'rpigpio'
            self._initialized = True
            mode = "PWM (passive)" if self._is_passive else "on/off (active)"
            print(f"[Buzzer] RPi.GPIO {mode} on GPIO {self._gpio_pin} (after cleanup)")
        except Exception as e2:
            print(f"[Buzzer] RPi.GPIO cleanup retry failed: {e2}")

    def _try_gpiozero(self):
        """Fallback: gpiozero Buzzer/TonalBuzzer."""
        try:
            if self._is_passive:
                from gpiozero import TonalBuzzer
                self._buzzer = TonalBuzzer(self._gpio_pin)
            else:
                from gpiozero import Buzzer
                self._buzzer = Buzzer(self._gpio_pin)
            self._driver = 'gpiozero'
            self._initialized = True
            mode = "TonalBuzzer (passive)" if self._is_passive else "Buzzer (active)"
            print(f"[Buzzer] gpiozero {mode} on GPIO {self._gpio_pin}")
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
                    freq = self.NOTES[note_name]
                    if self._is_passive:
                        self._note_on_pwm(freq)
                    else:
                        # Active buzzer: just on/off, same pitch for all notes
                        self._note_on_active()
                time.sleep(duration)
        finally:
            self._note_off()
            self._playing = False

    # ── Active buzzer control (simple on/off) ───────────────────────────

    def _note_on_active(self):
        """Turn on active buzzer — just GPIO HIGH."""
        if self._driver == 'rpigpio' and self._GPIO is not None:
            try:
                self._GPIO.output(self._gpio_pin, self._GPIO.HIGH)
            except Exception:
                pass
        elif self._driver == 'gpiozero' and self._buzzer is not None:
            try:
                self._buzzer.on()
            except Exception:
                pass

    # ── Passive buzzer control (PWM frequency) ──────────────────────────

    def _note_on_pwm(self, freq):
        """Play a specific frequency on passive buzzer via PWM.

        Uses 50% duty cycle for clean, loud tones.
        Frequency changes are applied via ChangeFrequency().
        """
        if freq <= 0:
            self._note_off()
            return

        if self._driver == 'rpigpio' and self._pwm is not None:
            try:
                if self._current_freq != freq:
                    self._pwm.ChangeFrequency(freq)
                    self._current_freq = freq
                self._pwm.ChangeDutyCycle(50)
            except Exception:
                pass
        elif self._driver == 'gpiozero' and self._buzzer is not None:
            try:
                from gpiozero.tones import Tone
                self._buzzer.play(Tone(freq))
            except Exception:
                pass

    # ── Common off ──────────────────────────────────────────────────────

    def _note_off(self):
        if self._driver == 'rpigpio':
            try:
                if self._is_passive and self._pwm is not None:
                    self._pwm.ChangeDutyCycle(0)
                    self._current_freq = 0
                elif self._GPIO is not None:
                    self._GPIO.output(self._gpio_pin, self._GPIO.LOW)
            except Exception:
                pass
        elif self._driver == 'gpiozero' and self._buzzer is not None:
            try:
                self._buzzer.off()
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
            if self._is_passive and self._pwm is not None:
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
