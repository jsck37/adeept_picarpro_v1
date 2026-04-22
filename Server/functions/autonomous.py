"""
Autonomous robot functions.
- radarScan: Sweep ultrasonic sensor and build distance map
- automatic: Autonomous obstacle avoidance
- trackLine: IR line following
- keepDistance: Maintain fixed distance from obstacle

Improvements over v1:
- Clean class-based design (no global variables)
- Proper thread management with pause/resume
- Shared hardware references instead of duplicate instances
- Non-blocking operation
"""

import threading
import time
from Server.config import RADAR_SCAN_SPEED


class AutonomousController:
    """
    Autonomous robot functions controller.
    
    All autonomous modes run in a background thread with pause/resume control.
    Hardware references are passed in (not created anew).
    """

    def __init__(self, motors, servos, ultrasonic):
        """
        Initialize autonomous controller.
        
        Args:
            motors: MotorController instance
            servos: ServoController instance
            ultrasonic: UltrasonicSensor instance
        """
        self.motors = motors
        self.servos = servos
        self.ultrasonic = ultrasonic

        self._running = True
        self._active = False
        self._flag = threading.Event()
        self._flag.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        self._current_mode = "none"
        self._radar_data = []

        # Line tracker IR sensors
        self._ir_sensors = []
        try:
            from gpiozero import InputDevice
            from Server.config import LINE_LEFT_PIN, LINE_MIDDLE_PIN, LINE_RIGHT_PIN
            self._ir_sensors = [
                InputDevice(LINE_LEFT_PIN),
                InputDevice(LINE_MIDDLE_PIN),
                InputDevice(LINE_RIGHT_PIN),
            ]
            print("[Auto] IR line sensors initialized")
        except Exception as e:
            print(f"[Auto] IR sensors failed: {e}")

    def _run(self):
        """Main thread loop."""
        while self._running:
            self._flag.wait()
            if not self._running:
                break

            try:
                if self._current_mode == "radarScan":
                    self._radar_scan()
                elif self._current_mode == "automatic":
                    self._automatic()
                elif self._current_mode == "trackLine":
                    self._track_line()
                elif self._current_mode == "keepDistance":
                    self._keep_distance()
            except Exception as e:
                print(f"[Auto] Error in {self._current_mode}: {e}")
                self.stop()

    def start(self, mode):
        """
        Start an autonomous mode.
        
        Args:
            mode: 'radarScan', 'automatic', 'trackLine', 'keepDistance'
        """
        self.stop()  # Stop any current mode
        self._current_mode = mode
        self._active = True
        self._flag.set()
        print(f"[Auto] Started: {mode}")

    def stop(self):
        """Stop current autonomous mode."""
        self._active = False
        self._flag.clear()
        self.motors.stop()
        self.servos.stop_all()
        self._current_mode = "none"

    def is_active(self):
        """Check if a mode is currently active."""
        return self._active

    def get_radar_data(self):
        """Get the latest radar scan data."""
        return self._radar_data

    def _radar_scan(self):
        """Sweep the ultrasonic sensor from left to right, recording distances."""
        self._radar_data = []
        scan_servo = 0  # Pan servo for radar

        for angle_offset in range(-60, 61, 5):
            if not self._active:
                break

            self.servos.move_angle(scan_servo, angle_offset)
            time.sleep(0.1)

            distance = self.ultrasonic.get_distance()
            self._radar_data.append({
                'angle': angle_offset,
                'distance': distance,
            })

        # Return to center
        self.servos.move_angle(scan_servo, 0)
        self.stop()

    def _automatic(self):
        """Autonomous obstacle avoidance mode."""
        scan_servo = 0

        while self._active:
            distance = self.ultrasonic.get_distance()

            if distance < 15:  # Too close - stop and turn
                self.motors.stop()
                time.sleep(0.2)

                # Check left
                self.servos.move_angle(scan_servo, -45)
                time.sleep(0.3)
                dist_left = self.ultrasonic.get_distance()

                # Check right
                self.servos.move_angle(scan_servo, 45)
                time.sleep(0.3)
                dist_right = self.ultrasonic.get_distance()

                # Return to center
                self.servos.move_angle(scan_servo, 0)
                time.sleep(0.1)

                # Turn towards more open direction
                if dist_left > dist_right:
                    self.motors.move(30, 'forward', 'left', 0.5)
                else:
                    self.motors.move(30, 'forward', 'right', 0.5)
                time.sleep(0.5)

            elif distance < 30:  # Getting close - slow down
                self.motors.move(20, 'forward', 'no', 0.5)
                time.sleep(0.1)

            else:  # Clear path - move forward
                self.motors.move(40, 'forward', 'no', 0.5)
                time.sleep(0.1)

    def _track_line(self):
        """IR line following mode."""
        while self._active:
            if len(self._ir_sensors) < 3:
                break

            left = not self._ir_sensors[0].value    # Low = on line
            middle = not self._ir_sensors[1].value
            right = not self._ir_sensors[2].value

            if middle and not left and not right:
                # On line - go straight
                self.motors.move(35, 'forward', 'no', 0.5)
            elif middle and left:
                # Slightly off to the right
                self.motors.move(30, 'forward', 'right', 0.6)
            elif middle and right:
                # Slightly off to the left
                self.motors.move(30, 'forward', 'left', 0.6)
            elif left:
                # Off to the right - turn left harder
                self.motors.move(25, 'forward', 'left', 0.4)
            elif right:
                # Off to the left - turn right harder
                self.motors.move(25, 'forward', 'right', 0.4)
            else:
                # Lost the line - stop and search
                self.motors.stop()

            time.sleep(0.05)

    def _keep_distance(self):
        """Maintain a fixed distance from an obstacle."""
        target_distance = 20  # cm

        while self._active:
            distance = self.ultrasonic.get_distance()

            if distance < target_distance - 3:
                self.motors.move(20, 'backward', 'no', 0.5)
            elif distance > target_distance + 3:
                self.motors.move(20, 'forward', 'no', 0.5)
            else:
                self.motors.stop()

            time.sleep(0.1)

    def shutdown(self):
        """Clean shutdown."""
        self.stop()
        self._running = False
        self._flag.set()  # Unblock thread
        for sensor in self._ir_sensors:
            try:
                sensor.close()
            except Exception:
                pass
        print("[Auto] Shutdown complete")
