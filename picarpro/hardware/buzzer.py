import threading
from picarpro.logger import logger
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

# Tetris (Korobeiniki) — «8-бит»: монофония square-wave 50 % duty (как в
# Game Boy), один полный проход темы A+B. 32 четверти × 0.3125 с = ровно
# 10.0 с (по требованию владельца — v1.6.4, вместо happy_birthday).
_TQ = 0.3125                   # длительность четверти, сек
_TETRIS_BEATS = [
    # тема A
    ('E5', 1), ('B4', .5), ('C5', .5), ('D5', 1), ('C5', .5), ('B4', .5),
    ('A4', 1), ('A4', .5), ('C5', .5), ('E5', 1), ('D5', .5), ('C5', .5),
    ('B4', 1.5), ('C5', .5), ('D5', 1), ('E5', 1),
    ('C5', 1), ('A4', 1), ('A4', 1), ('REST', 1),
    # тема B
    ('REST', .5), ('D5', 1), ('F5', .5), ('A5', 1), ('G5', .5), ('F5', .5),
    ('E5', 1.5), ('C5', .5), ('E5', 1), ('D5', .5), ('C5', .5),
    ('B4', 1), ('B4', .5), ('C5', .5), ('D5', 1), ('E5', 1),
    ('C5', 1), ('A4', 1), ('A4', 1), ('REST', 1),
]
TETRIS = [(n, round(d * _TQ, 4)) for n, d in _TETRIS_BEATS]

MELODIES = {
    'beep': [('A4', .15), ('REST', .1)],
    'tetris': TETRIS,
    'alarm': [('A5', .12), ('REST', .06), ('A5', .12), ('REST', .06),
              ('A5', .12), ('REST', .3), ('E5', .12), ('REST', .06),
              ('E5', .12), ('REST', .06), ('E5', .12), ('REST', .5)],
}


class BuzzerController:
    def __init__(self):
        self._pwm = None
        self._flag = threading.Event()      # set = мелодия играет
        self._stop_evt = threading.Event()  # set = прервать немедленно
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
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._stop_evt.clear()
        self._flag.set()
        self._thread = threading.Thread(target=self._play, args=(notes,), daemon=True)
        self._thread.start()

    def _play(self, notes):
        try:
            for n, d in notes:
                if self._stop_evt.is_set():
                    break
                freq = NOTES.get(n, 0) if isinstance(n, str) else n
                if freq <= 0:
                    self._off()
                else:
                    self._tone(freq)
                # Event.wait вместо sleep: stop() обрывает ноту сразу,
                # а не через 0.8 с (play_melody раньше блокировал вызывающего)
                self._stop_evt.wait(d)
        finally:
            self._off()
            self._flag.clear()

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
        self._stop_evt.set()
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
