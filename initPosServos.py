#!/usr/bin/env python3
"""
Initialize all servos to their default (90°) position.
Run this after first setup or when servos need calibration.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Server.config import SERVO_COUNT, SERVO_INIT_ANGLE


def main():
    """Set all servos to their init angle."""
    print("Initializing servo positions...")

    try:
        from Server.hardware.servos import ServoController
        servos = ServoController()

        print(f"Moving {SERVO_COUNT} servos to {SERVO_INIT_ANGLE}°...")
        servos.move_init()

        import time
        time.sleep(2)  # Wait for servos to reach position

        servos.shutdown()
        print("Servo initialization complete!")

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure I2C is enabled: sudo raspi-config -> Interface Options -> I2C")


if __name__ == '__main__':
    main()
