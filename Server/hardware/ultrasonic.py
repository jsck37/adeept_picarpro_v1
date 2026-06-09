import threading, time
from config import ULTRASONIC_ENABLED, ULTRASONIC_TRIGGER, ULTRASONIC_ECHO, ULTRASONIC_MAX_DISTANCE
from Server.logger import logger

class UltrasonicSensor:
    def __init__(self):
        self._initialized = False
        self._distance = 0.0
        self._running = False
        self._thread = None
        if not ULTRASONIC_ENABLED:
            return
        try:
            from gpiozero import DistanceSensor
            self._sensor = DistanceSensor(echo=ULTRASONIC_ECHO, trigger=ULTRASONIC_TRIGGER, max_distance=ULTRASONIC_MAX_DISTANCE)
            self._initialized = True
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            logger.info("[Ultrasonic] OK")
        except Exception as e:
            logger.error(f"[Ultrasonic] Failed: {e}")

    def _loop(self):
        while self._running:
            try:
                self._distance = round(self._sensor.distance * 100, 1)
            except Exception:
                pass
            time.sleep(0.1)

    def get_last_distance(self):
        return self._distance

    def shutdown(self):
        self._running = False
        logger.info("[Ultrasonic] Shutdown")
