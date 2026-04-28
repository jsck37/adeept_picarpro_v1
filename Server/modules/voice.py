#!/usr/bin/env python3
"""Voice Commands — Offline speech recognition via Sherpa-NCNN."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.functions.voice_command import VoiceCommandController
from Server.hardware.servos import ServoController
from Server.hardware.motors import MotorController


def main():
    print("[Voice] Starting voice command recognition...")
    print("  Say: 'look left', 'look right', 'arm up', 'arm down', 'stop'")

    servos = ServoController()
    motors = MotorController()
    voice = VoiceCommandController(servos, motors)

    if not voice._initialized:
        print("  Sherpa-NCNN not found. Install it first.")
        print("  See: https://github.com/k2-fsa/sherpa-ncnn")
        servos.shutdown()
        motors.shutdown()
        return

    voice.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        voice.shutdown()
        servos.shutdown()
        motors.shutdown()
        print("[Voice] Done.")


if __name__ == '__main__':
    main()
