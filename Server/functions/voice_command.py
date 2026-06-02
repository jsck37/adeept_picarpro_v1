"""Voice command module — Sherpa-NCNN offline speech recognition.

Fixed: removed references to servo channels 3/4/6 which don't exist
on PiCar Pro v1 (only has 3 servos: 0=steering, 1=cam_pan, 2=cam_tilt).
Now uses only the available servos for voice commands.
"""

import threading
import time
import os
import subprocess
from Server.config import VOICE_MODEL_PATH, VOICE_ALSA_DEVICE, VOICE_OUTPUT_FILE
from Server.logger import logger


class VoiceCommandController:

    # Map voice phrases to robot actions.
    # v1 only has 3 servos: 0=steering, 1=cam_pan, 2=cam_tilt
    COMMAND_MAP = {
        'look left': 'lookLeft',
        'look right': 'lookRight',
        'look left.': 'lookLeft',
        'look right.': 'lookRight',
        'camera up': 'camUp',
        'camera down': 'camDown',
        'camera up.': 'camUp',
        'camera down.': 'camDown',
        'forward': 'forward',
        'forward.': 'forward',
        'backward': 'backward',
        'backward.': 'backward',
        'stop': 'stop',
        'stop.': 'stop',
    }

    def __init__(self, servos, motors):
        self.servos = servos
        self.motors = motors

        self._running = True
        self._active = False
        self._flag = threading.Event()
        self._flag.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._sherpa_process = None
        self._initialized = False
        self._last_command = ""

        sherpa_binary = os.path.join(
            os.path.dirname(VOICE_MODEL_PATH), "..", "sherpa-ncnn-alsa"
        )

        if os.path.exists(sherpa_binary) and os.path.exists(VOICE_MODEL_PATH):
            self._sherpa_binary = sherpa_binary
            self._initialized = True
            self._thread.start()
            logger.info("[Voice] Sherpa-NCNN initialized")
        else:
            logger.warning("[Voice] Sherpa-NCNN not found - voice control disabled")

    def start(self):
        if not self._initialized:
            return
        self._active = True
        self._start_sherpa()
        self._flag.set()

    def stop(self):
        self._active = False
        self._flag.clear()
        self._stop_sherpa()

    def _start_sherpa(self):
        if self._sherpa_process is not None:
            return
        try:
            with open(VOICE_OUTPUT_FILE, 'w') as f:
                self._sherpa_process = subprocess.Popen(
                    [self._sherpa_binary, VOICE_MODEL_PATH, VOICE_ALSA_DEVICE],
                    stdout=f, stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            logger.error(f"[Voice] Failed to start Sherpa-NCNN: {e}")

    def _stop_sherpa(self):
        if self._sherpa_process is not None:
            try:
                self._sherpa_process.terminate()
                self._sherpa_process.wait(timeout=5)
            except Exception:
                try:
                    self._sherpa_process.kill()
                except Exception:
                    pass
            self._sherpa_process = None

    def _run(self):
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            try:
                self._read_and_execute()
            except Exception:
                pass
            time.sleep(0.2)

    def _read_and_execute(self):
        try:
            if not os.path.exists(VOICE_OUTPUT_FILE):
                return
            with open(VOICE_OUTPUT_FILE, 'r') as f:
                content = f.read().strip().lower()
            if not content or content == self._last_command:
                return
            self._last_command = content
            for key, command in self.COMMAND_MAP.items():
                if key in content:
                    self._execute_command(command)
                    break
        except Exception:
            pass

    def _execute_command(self, command):
        logger.info(f"[Voice] Command: {command}")
        # Servo 0 = steering, 1 = cam_pan, 2 = cam_tilt
        if command == 'lookLeft':
            self.servos.move_angle(0, -30)
        elif command == 'lookRight':
            self.servos.move_angle(0, 30)
        elif command == 'camUp':
            self.servos.move_angle(2, 15)    # cam_tilt up
        elif command == 'camDown':
            self.servos.move_angle(2, -15)   # cam_tilt down
        elif command == 'forward':
            self.motors.move(40, 'forward', 'no', 0.5)
        elif command == 'backward':
            self.motors.move(40, 'backward', 'no', 0.5)
        elif command == 'stop':
            self.motors.stop()

    def shutdown(self):
        self.stop()
        self._running = False
        self._flag.set()
        logger.info("[Voice] Shutdown")
