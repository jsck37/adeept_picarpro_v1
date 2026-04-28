#!/usr/bin/env python3
"""Line Tracking — Read 3-channel IR line sensor."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import LINE_LEFT_PIN, LINE_MIDDLE_PIN, LINE_RIGHT_PIN


def main():
    print("[LineTrack] Reading IR sensors... Press Ctrl+C to stop.")

    try:
        from gpiozero import InputDevice
        left = InputDevice(LINE_LEFT_PIN)
        middle = InputDevice(LINE_MIDDLE_PIN)
        right = InputDevice(LINE_RIGHT_PIN)
    except Exception as e:
        print(f"  Error: {e}")
        return

    try:
        while True:
            L = not left.value
            M = not middle.value
            R = not right.value
            print(f"  Left: {'█' if L else '░'}  Mid: {'█' if M else '░'}  Right: {'█' if R else '░'}")
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        left.close()
        middle.close()
        right.close()
        print("[LineTrack] Done.")


if __name__ == '__main__':
    main()
