#!/usr/bin/env python3
"""
Initialize all servos to their default (90°) position.
Only initializes the 3 active servos (steering, camera pan/tilt).
Crane/manipulator is disabled — not physically connected.
Run this after first setup or when servos need calibration.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Server.config import SERVO_COUNT, SERVO_INIT_ANGLE, SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT
from Server.logger import logger


def main():
    """Set all servos to their init angle."""
    logger.info("Initializing servo positions...")
    logger.info(f"  Active servos: {SERVO_COUNT}")
    logger.info(f"    Channel {SERVO_STEERING}: Steering (front wheels)")
    logger.info(f"    Channel {SERVO_CAM_PAN}: Camera Pan")
    logger.info(f"    Channel {SERVO_CAM_TILT}: Camera Tilt")
    logger.info(f"  Crane: DISABLED (not connected)")

    try:
        from Server.hardware.servos import ServoController
        servos = ServoController()

        logger.info(f"Moving {SERVO_COUNT} servos to {SERVO_INIT_ANGLE}°...")
        servos.move_init()

        import time
        time.sleep(2)  # Wait for servos to reach position

        servos.shutdown()
        logger.info("Servo initialization complete!")

    except Exception as e:
        logger.error(f"Error: {e}")
        logger.error("Make sure I2C is enabled: sudo raspi-config -> Interface Options -> I2C")


if __name__ == '__main__':
    main()
