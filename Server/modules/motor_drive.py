#!/usr/bin/env python3
"""Motor Drive — Drive motors forward and backward.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'motors' in hw and hw['motors'] and hw['motors']._initialized:
        print("[Motor Drive] Testing motor control...")
        motors = hw['motors']
        moves = [
            ('Forward', 'forward', 'no', 40),
            ('Stop', None, None, 0),
            ('Backward', 'backward', 'no', 40),
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
        print("[Motor Drive] Done.")
    else:
        from Server.hardware.motors import MotorController
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
