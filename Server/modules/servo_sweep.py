#!/usr/bin/env python3
"""Servo Sweep — Sweep a servo 0->180->0 degrees."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.servos import ServoController
from Server.config import SERVO_PAN


def main():
    print("[Servo Sweep] Sweeping servo channel 0 (Pan)...")
    print("  Press Ctrl+C to stop.")
    servos = ServoController()

    try:
        while True:
            for angle in range(0, 181, 5):
                servos.set_angle(SERVO_PAN, angle)
                time.sleep(0.03)
            for angle in range(180, -1, -5):
                servos.set_angle(SERVO_PAN, angle)
                time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    finally:
        servos.shutdown()
        print("[Servo Sweep] Done.")


if __name__ == '__main__':
    main()
