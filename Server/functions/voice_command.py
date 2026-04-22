"""
Voice command module (backported from v2).
Uses Sherpa-NCNN for offline speech recognition.

Supported voice commands:
- lookleft, lookright: Camera pan
- armup, armdown: Arm shoulder
- handup, handdown: Arm elbow
- grab, loose: Gripper
- stop: Emergency stop

Note: Requires sherpa-ncnn to be installed separately.
See: https://github.com/k2-fsa/sherpa-ncnn
"""

import threading
import time
import os
import subprocess
from Server.config import VOICE_MODEL_PATH, VOICE_ALSA_DEVICE, VOICE_OUTPUT_FILE


class VoiceCommandController:
    """
    Offline voice command recognition using Sherpa-NCNN.
    
    Runs speech recognition in a background thread and executes
    recognized commands on the robot hardware.
    """

    # Supported voice commands mapping
    COMMAND_MAP = {
        'look left': 'lookLeft',
        'look right': 'lookRight',
        'look left.': 'lookLeft',
        'look right.': 'lookRight',
        'arm up': 'armUp',
        'arm down': 'armDown',
        'arm up.': 'armUp',
        'arm down.': 'armDown',
        'hand up': 'handUp',
        'hand down': 'handDown',
        'hand up.': 'handUp',
        'hand down.': 'handDown',
        'grab': 'grab',
        'grab.': 'grab',
        'loose': 'loose',
        'loose.': 'loose',
        'stop': 'stop',
        'stop.': 'stop',
    }

    def __init__(self, servos, motors):
        """
        Initialize voice command controller.
        
        Args:
            servos: ServoController instance
            motors: MotorController instance
        """
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

        # Check if sherpa-ncnn is available
        sherpa_binary = os.path.join(
            os.path.dirname(VOICE_MODEL_PATH), "..", "sherpa-ncnn-alsa"
        )

        if os.path.exists(sherpa_binary) and os.path.exists(VOICE_MODEL_PATH):
            self._sherpa_binary = sherpa_binary
            self._initialized = True
            self._thread.start()
            print("[Voice] Sherpa-NCNN voice control initialized")
        else:
            print("[Voice] Sherpa-NCNN not found - voice control disabled")
            print(f"[Voice] Expected binary: {sherpa_binary}")
            print(f"[Voice] Expected model: {VOICE_MODEL_PATH}")

    def start(self):
        """Start voice recognition."""
        if not self._initialized:
            return

        self._active = True
        self._start_sherpa()
        self._flag.set()
        print("[Voice] Recognition started")

    def stop(self):
        """Stop voice recognition."""
        self._active = False
        self._flag.clear()
        self._stop_sherpa()

    def _start_sherpa(self):
        """Start the Sherpa-NCNN recognition process."""
        if self._sherpa_process is not None:
            return

        try:
            cmd = [
                self._sherpa_binary,
                VOICE_MODEL_PATH,
                VOICE_ALSA_DEVICE,
            ]

            with open(VOICE_OUTPUT_FILE, 'w') as f:
                self._sherpa_process = subprocess.Popen(
                    cmd,
                    stdout=f,
                    stderr=subprocess.DEVNULL,
                )
            print("[Voice] Sherpa-NCNN process started")
        except Exception as e:
            print(f"[Voice] Failed to start Sherpa-NCNN: {e}")

    def _stop_sherpa(self):
        """Stop the Sherpa-NCNN recognition process."""
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
        """Main voice recognition loop."""
        while self._running:
            self._flag.wait()
            if not self._running:
                break

            try:
                self._read_and_execute()
            except Exception as e:
                print(f"[Voice] Error: {e}")

            time.sleep(0.2)

    def _read_and_execute(self):
        """Read the latest recognition result and execute command."""
        try:
            if not os.path.exists(VOICE_OUTPUT_FILE):
                return

            with open(VOICE_OUTPUT_FILE, 'r') as f:
                content = f.read().strip().lower()

            if not content or content == self._last_command:
                return

            self._last_command = content

            # Find matching command
            for key, command in self.COMMAND_MAP.items():
                if key in content:
                    self._execute_command(command)
                    break

        except Exception:
            pass

    def _execute_command(self, command):
        """Execute a recognized voice command."""
        print(f"[Voice] Command: {command}")

        if command == 'lookLeft':
            self.servos.move_angle(0, -30)
        elif command == 'lookRight':
            self.servos.move_angle(0, 30)
        elif command == 'armUp':
            self.servos.move_angle(3, 15)
        elif command == 'armDown':
            self.servos.move_angle(3, -15)
        elif command == 'handUp':
            self.servos.move_angle(4, 15)
        elif command == 'handDown':
            self.servos.move_angle(4, -15)
        elif command == 'grab':
            self.servos.move_angle(6, 30)
        elif command == 'loose':
            self.servos.move_angle(6, -30)
        elif command == 'stop':
            self.motors.stop()

    def shutdown(self):
        """Clean shutdown."""
        self.stop()
        self._running = False
        self._flag.set()
        print("[Voice] Shutdown complete")
