#!/usr/bin/env python3
"""Voice Commands — Offline speech recognition via Sherpa-NCNN.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    servos = None
    motors = None
    own_servos = False
    own_motors = False

    if hw:
        servos = hw.get('servos')
        motors = hw.get('motors')

    if not servos or not servos._pwm_initialized:
        from Server.hardware.servos import ServoController
        servos = ServoController()
        own_servos = True
    if not motors or not motors._initialized:
        from Server.hardware.motors import MotorController
        motors = MotorController()
        own_motors = True

    print("[Voice] Starting voice command recognition...")
    print("  Say: 'look left', 'look right', 'arm up', 'arm down', 'stop'")

    try:
        from Server.functions.voice_command import VoiceCommandController
        voice = VoiceCommandController(servos, motors)
    except Exception as e:
        print(f"  Error: {e}")
        if own_servos: servos.shutdown()
        if own_motors: motors.shutdown()
        return

    if not voice._initialized:
        print("  Sherpa-NCNN not found. Install it first.")
        if own_servos: servos.shutdown()
        if own_motors: motors.shutdown()
        return

    voice.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        voice.shutdown()
        if own_servos: servos.shutdown()
        if own_motors: motors.shutdown()
        print("[Voice] Done.")


if __name__ == '__main__':
    main()
