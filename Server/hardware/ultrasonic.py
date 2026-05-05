"""HC-SR04 ultrasonic distance sensor with median filtering."""

import time
import threading
from Server.config import ULTRASONIC_TRIGGER, ULTRASONIC_ECHO, ULTRASONIC_MAX_DISTANCE


class UltrasonicSensor:

    def __init__(self):
        self._sensor = None
        self._last_distance = 0.0
        self._lock = threading.Lock()
        self._initialized = False

        try:
            from gpiozero import DistanceSensor
            self._sensor = DistanceSensor(
                echo=ULTRASONIC_ECHO, trigger=ULTRASONIC_TRIGGER,
                max_distance=ULTRASONIC_MAX_DISTANCE,
            )
            self._initialized = True
            print("[Ultra] Initialized")
        except Exception as e:
            print(f"[Ultra] Init failed: {e}")

    def get_distance(self):
        if self._sensor is None:
            return 0.0
        try:
            readings = []
            for _ in range(5):
                dist = self._sensor.distance * 100
                if dist > 0:
                    readings.append(dist)
                time.sleep(0.01)
            if readings:
                readings.sort()
                median = readings[len(readings) // 2]
                with self._lock:
                    self._last_distance = round(median, 1)
                return self._last_distance
            return self._last_distance
        except Exception as e:
            print(f"[Ultra] Read error: {e}")
            return self._last_distance

    def get_last_distance(self):
        with self._lock:
            return self._last_distance

    def shutdown(self):
        if self._sensor is not None:
            try:
                self._sensor.close()
            except Exception:
                pass
        print("[Ultra] Shutdown")
