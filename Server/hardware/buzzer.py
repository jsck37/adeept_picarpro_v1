import threading, time
from Server.logger import logger
from config import BUZZER_PIN

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None

NOTES = {
    'C3': 131, 'D3': 147, 'E3': 165, 'F3': 175, 'G3': 196, 'A3': 220, 'B3': 247,
    'C4': 262, 'D4': 294, 'E4': 330, 'F4': 349, 'G4': 392, 'A4': 440, 'B4': 494,
    'C5': 523, 'D5': 587, 'E5': 659, 'F5': 698, 'G5': 784, 'A5': 880, 'B5': 988,
    'REST': 0,
}

BIRTHDAY = [
    ('C4', .3), ('C4', .1), ('D4', .4), ('C4', .4), ('F4', .4), ('E4', .8),
    ('C4', .3), ('C4', .1), ('D4', .4), ('C4', .4), ('G4', .4), ('F4', .8),
    ('C4', .3), ('C4', .1), ('C5', .4), ('A4', .4), ('F4', .4), ('E4', .4), ('D4', .8),
    ('B4', .3), ('B4', .1), ('A4', .4), ('F4', .4), ('G4', .4), ('F4', .8),
]

MELODIES = {
    'beep': [('A4', .15), ('REST', .1)],
    'happy_birthday': BIRTHDAY,
}


class BuzzerController:
    def __init__(self):
        self._pwm = None
        self._flag = threading.Event()
        self._thread = None
        self._initialized = False
        if not _HAS_GPIO:
            logger.warning('[Buzzer] RPi.GPIO not available')
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
            self._pwm = GPIO.PWM(BUZZER_PIN, 440)
            self._pwm.start(0)
            self._initialized = True
            logger.info(f'[Buzzer] OK — GPIO{BUZZER_PIN}')
        except Exception as e:
            logger.error(f'[Buzzer] init failed: {e}')

    def play_melody(self, name='beep'):
        if not self._initialized:
            return
        notes = MELODIES.get(name)
        if not notes:
            return
        self._flag.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._flag.set()
        self._thread = threading.Thread(target=self._play, args=(notes,), daemon=True)
        self._thread.start()

    def _play(self, notes):
        try:
            for n, d in notes:
                if not self._flag.is_set():
                    break
                freq = NOTES.get(n, 0) if isinstance(n, str) else n
                if freq <= 0:
                    self._off()
                else:
                    self._tone(freq)
                time.sleep(d)
        finally:
            self._off()

    def _tone(self, freq):
        if not self._pwm:
            return
        try:
            self._pwm.ChangeFrequency(freq)
            self._pwm.ChangeDutyCycle(50)
        except Exception:
            pass

    def _off(self):
        if not self._pwm:
            return
        try:
            self._pwm.ChangeDutyCycle(0)
        except Exception:
            pass

    def beep(self):
        self.play_melody('beep')

    def stop(self):
        self._flag.clear()
        self._off()

    def shutdown(self):
        self.stop()
        if self._pwm:
            try:
                self._pwm.stop()
            except Exception:
                pass
        logger.info('[Buzzer] shutdown')
