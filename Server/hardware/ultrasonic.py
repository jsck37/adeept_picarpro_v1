import threading, time
from Server.logger import logger
from config import ULTRASONIC_TRIGGER, ULTRASONIC_ECHO, ULTRASONIC_MAX_DISTANCE

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None


class UltrasonicSensor:
    def __init__(self):
        self._distance = 0.0
        self._running = False
        self._thread = None
        self._initialized = False
        if not _HAS_GPIO:
            logger.warning('[Ultrasonic] RPi.GPIO not available')
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(ULTRASONIC_TRIGGER, GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(ULTRASONIC_ECHO, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            self._initialized = True
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            logger.info(f'[Ultrasonic] OK — trig={ULTRASONIC_TRIGGER} echo={ULTRASONIC_ECHO}')
        except Exception as e:
            logger.error(f'[Ultrasonic] init failed: {e}')

    def _measure(self):
        GPIO.output(ULTRASONIC_TRIGGER, GPIO.HIGH)
        time.sleep(0.00001)
        GPIO.output(ULTRASONIC_TRIGGER, GPIO.LOW)
        deadline = time.time() + 0.03
        while GPIO.input(ULTRASONIC_ECHO) == 0:
            if time.time() > deadline:
                return None
        t_start = time.time()
        deadline = time.time() + 0.03
        while GPIO.input(ULTRASONIC_ECHO) == 1:
            if time.time() > deadline:
                return None
        t_end = time.time()
        duration = t_end - t_start
        return round(duration * 17150.0, 1)

    def _loop(self):
        while self._running:
            try:
                d = self._measure()
                if d is not None and 0 < d <= ULTRASONIC_MAX_DISTANCE * 100:
                    self._distance = d
            except Exception:
                pass
            time.sleep(0.1)

    def get_last_distance(self):
        return self._distance

    def shutdown(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info('[Ultrasonic] shutdown')
