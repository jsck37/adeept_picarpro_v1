"""
Ultrasonic distance sensor module.
Uses gpiozero DistanceSensor for reliable measurements.

Improvements over v1:
- Added median filtering for stable readings
- Timeout handling
- Non-blocking read
"""

import time
import threading
from Server.config import ULTRASONIC_TRIGGER, ULTRASONIC_ECHO, ULTRASONIC_MAX_DISTANCE


class UltrasonicSensor:
    """HC-SR04 ultrasonic distance sensor with median filtering."""

    def __init__(self):
        self._sensor = None
        self._last_distance = 0.0
        self._samples = []
        self._sample_count = 5
        self._lock = threading.Lock()

        try:
            from gpiozero import DistanceSensor
            self._sensor = DistanceSensor(
                echo=ULTRASONIC_ECHO,
                trigger=ULTRASONIC_TRIGGER,
                max_distance=ULTRASONIC_MAX_DISTANCE,
            )
            print("[Ultra] Ultrasonic sensor initialized")
        except Exception as e:
            print(f"[Ultra] Failed to initialize: {e}")

    def get_distance(self):
        """
        Get filtered distance reading in cm.
        Uses median of multiple samples for noise rejection.
        """
        if self._sensor is None:
            return 0.0

        try:
            # Take multiple readings for median filter
            readings = []
            for _ in range(self._sample_count):
                dist = self._sensor.distance * 100  # Convert m to cm
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
        """Return the last known distance without taking a new reading."""
        with self._lock:
            return self._last_distance

    def shutdown(self):
        """Clean up sensor resources."""
        if self._sensor is not None:
            try:
                self._sensor.close()
            except Exception:
                pass
        print("[Ultra] Shutdown complete")
