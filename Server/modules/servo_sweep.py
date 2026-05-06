#!/usr/bin/env python3
"""Servo Sweep — Sweep a servo 0->180->0 degrees.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import SERVO_CAM_PAN


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'servos' in hw and hw['servos'] and hw['servos']._pwm_initialized:
        print("[Servo Sweep] Sweeping camera pan servo (3 cycles)...")
        servos = hw['servos']
        try:
            for _ in range(3):
                for angle in range(0, 181, 5):
                    servos.set_angle(SERVO_CAM_PAN, angle)
                    time.sleep(0.03)
                for angle in range(180, -1, -5):
                    servos.set_angle(SERVO_CAM_PAN, angle)
                    time.sleep(0.03)
        except KeyboardInterrupt:
            pass
        finally:
            servos.set_angle(SERVO_CAM_PAN, 90)
            print("[Servo Sweep] Done.")
    else:
        from Server.hardware.servos import ServoController
        print("[Servo Sweep] Sweeping camera pan servo...")
        servos = ServoController()
        try:
            while True:
                for angle in range(0, 181, 5):
                    servos.set_angle(SERVO_CAM_PAN, angle)
                    time.sleep(0.03)
                for angle in range(180, -1, -5):
                    servos.set_angle(SERVO_CAM_PAN, angle)
                    time.sleep(0.03)
        except KeyboardInterrupt:
            pass
        finally:
            servos.shutdown()
            print("[Servo Sweep] Done.")


if __name__ == '__main__':
    main()
