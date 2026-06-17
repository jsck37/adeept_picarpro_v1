"""
HC-SR04 ultrasonic sensor.

Two backends are tried in order:
  1. ``RPi.GPIO`` — manual trigger/echo with a hard timeout. This is
     the same approach the original Adeept ``Ultra.py`` example uses
     under the hood, and it tolerates the occasional missed echo
     pulse that hangs ``gpiozero``.
  2. ``gpiozero.DistanceSensor`` — clean OO API; used when RPi.GPIO
     is unavailable (e.g. on non-Pi machines running the simulator).

Both backends expose the same ``get_last_distance()`` method returning
a distance in centimetres (rounded to 1 decimal).
"""

import threading, time

from config import ULTRASONIC_TRIGGER, ULTRASONIC_ECHO, ULTRASONIC_MAX_DISTANCE
from Server.logger import logger


class UltrasonicSensor:
    def __init__(self):
        self._initialized = False
        self._distance = 0.0
        self._running = False
        self._thread = None
        self._driver = None
        self._GPIO = None

        if not self._try_rpi_gpio():
            self._try_gpiozero()

        if self._initialized:
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    # ---- backends ------------------------------------------------------
    def _try_rpi_gpio(self):
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(ULTRASONIC_TRIGGER, GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(ULTRASONIC_ECHO, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            self._GPIO = GPIO
            self._driver = "rpigpio"
            self._initialized = True
            logger.info(f"[Ultrasonic] RPi.GPIO OK (trig={ULTRASONIC_TRIGGER}, "
                        f"echo={ULTRASONIC_ECHO})")
            return True
        except Exception:
            return False

    def _try_gpiozero(self):
        try:
            from gpiozero import DistanceSensor
            self._sensor = DistanceSensor(
                echo=ULTRASONIC_ECHO, trigger=ULTRASONIC_TRIGGER,
                max_distance=ULTRASONIC_MAX_DISTANCE,
            )
            self._driver = "gpiozero"
            self._initialized = True
            logger.info(f"[Ultrasonic] gpiozero OK "
                        f"(trig={ULTRASONIC_TRIGGER}, echo={ULTRASONIC_ECHO})")
        except Exception as e:
            logger.warning(f"[Ultrasonic] Not available: {e}")

    # ---- measurement ---------------------------------------------------
    def _measure_rpigpio(self):
        GPIO = self._GPIO
        # 10 µs trigger pulse
        GPIO.output(ULTRASONIC_TRIGGER, GPIO.HIGH)
        time.sleep(0.00001)
        GPIO.output(ULTRASONIC_TRIGGER, GPIO.LOW)

        deadline = time.time() + 0.03   # 30 ms hard timeout
        while GPIO.input(ULTRASONIC_ECHO) == 0:
            if time.time() > deadline:
                return None
            pulse_start = time.time()
        pulse_start = time.time()
        deadline = time.time() + 0.03
        while GPIO.input(ULTRASONIC_ECHO) == 1:
            if time.time() > deadline:
                return None
            pulse_end = time.time()
        pulse_end = time.time()

        duration = pulse_end - pulse_start
        # speed of sound = 34300 cm/s, distance = duration * 34300 / 2
        return round(duration * 17150.0, 1)

    def _measure_gpiozero(self):
        try:
            return round(self._sensor.distance * 100.0, 1)
        except Exception:
            return None

    def _loop(self):
        while self._running:
            try:
                if self._driver == "rpigpio":
                    d = self._measure_rpigpio()
                elif self._driver == "gpiozero":
                    d = self._measure_gpiozero()
                else:
                    d = None
                if d is not None and 0 < d <= ULTRASONIC_MAX_DISTANCE * 100:
                    self._distance = d
            except Exception:
                pass
            time.sleep(0.1)

    # ---- public API ----------------------------------------------------
    def get_last_distance(self):
        return self._distance

    def shutdown(self):
        self._running = False
        if self._driver == "rpigpio" and self._GPIO:
            try:
                self._GPIO.cleanup(ULTRASONIC_TRIGGER)
                self._GPIO.cleanup(ULTRASONIC_ECHO)
            except Exception:
                pass
        elif self._driver == "gpiozero":
            try:
                self._sensor.close()
            except Exception:
                pass
        logger.info("[Ultrasonic] Shutdown")
