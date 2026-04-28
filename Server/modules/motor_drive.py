#!/usr/bin/env python3
"""Motor Drive — Drive motors forward, backward, left, right."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.motors import MotorController


def main():
    print("[Motor Drive] Testing motor control...")
    motors = MotorController()

    moves = [
        ('Forward', 'forward', 'no', 40),
        ('Stop', None, None, 0),
        ('Backward', 'backward', 'no', 40),
        ('Stop', None, None, 0),
        ('Left', 'forward', 'left', 35),
        ('Stop', None, None, 0),
        ('Right', 'forward', 'right', 35),
        ('Stop', None, None, 0),
    ]

    for name, direction, turn, speed in moves:
        if direction is None:
            print(f"  {name}")
            motors.stop()
        else:
            print(f"  {name}: dir={direction}, turn={turn}, speed={speed}")
            motors.move(speed, direction, turn, 0.5)
        time.sleep(1.5)

    motors.shutdown()
    print("[Motor Drive] Done.")


if __name__ == '__main__':
    main()
