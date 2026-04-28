#!/usr/bin/env python3
"""Ultrasonic Distance — Measure distance with HC-SR04."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.ultrasonic import UltrasonicSensor


def main():
    print("[Ultrasonic] Measuring distance... Press Ctrl+C to stop.")
    sensor = UltrasonicSensor()

    try:
        while True:
            dist = sensor.get_distance()
            print(f"  Distance: {dist:.1f} cm")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        sensor.shutdown()
        print("[Ultrasonic] Done.")


if __name__ == '__main__':
    main()
