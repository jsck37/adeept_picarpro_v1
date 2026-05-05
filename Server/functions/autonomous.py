"""Autonomous robot functions — radar scan, obstacle avoidance, line following, keep distance.

When ULTRASONIC_ENABLED=False: radarScan, automatic, keepDistance are disabled.
When LINE_TRACKER_ENABLED=False: trackLine is disabled.
"""

import threading
import time
from Server.config import (
    RADAR_SCAN_SPEED, SERVO_STEERING,
    ULTRASONIC_ENABLED, LINE_TRACKER_ENABLED,
    LINE_LEFT_PIN, LINE_MIDDLE_PIN, LINE_RIGHT_PIN,
)


class AutonomousController:

    def __init__(self, motors, servos, ultrasonic):
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

        # Line tracker IR sensors (only if enabled in config)
        self._ir_sensors = []
        if LINE_TRACKER_ENABLED:
            try:
                from gpiozero import InputDevice
                self._ir_sensors = [
                    InputDevice(LINE_LEFT_PIN),
                    InputDevice(LINE_MIDDLE_PIN),
                    InputDevice(LINE_RIGHT_PIN),
                ]
                print("[Auto] IR line sensors initialized")
            except Exception as e:
                print(f"[Auto] IR sensors failed: {e}")
        else:
            print("[Auto] Line tracker DISABLED in config")

    def _run(self):
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
        """Start an autonomous mode. Returns (ok, message)."""
        # Check hardware availability
        if mode in ("radarScan", "automatic", "keepDistance"):
            if not ULTRASONIC_ENABLED or not self.ultrasonic._initialized:
                return False, "Ultrasonic sensor not available"
        if mode == "trackLine":
            if not LINE_TRACKER_ENABLED or len(self._ir_sensors) < 3:
                return False, "Line tracker not available"

        self.stop()
        self._current_mode = mode
        self._active = True
        self._flag.set()
        print(f"[Auto] Started: {mode}")
        return True, f"Started: {mode}"

    def stop(self):
        self._active = False
        self._flag.clear()
        self.motors.stop()
        self.servos.stop_all()
        self._current_mode = "none"

    def is_active(self):
        return self._active

    def get_radar_data(self):
        return self._radar_data

    def _radar_scan(self):
        if not ULTRASONIC_ENABLED:
            self.stop()
            return
        self._radar_data = []
        scan_servo = SERVO_STEERING

        for angle_offset in range(-60, 61, 5):
            if not self._active:
                break
            self.servos.move_angle(scan_servo, angle_offset)
            time.sleep(0.1)
            distance = self.ultrasonic.get_distance()
            self._radar_data.append({'angle': angle_offset, 'distance': distance})

        self.servos.move_angle(scan_servo, 0)
        self.stop()

    def _automatic(self):
        if not ULTRASONIC_ENABLED:
            self.stop()
            return
        scan_servo = SERVO_STEERING

        while self._active:
            distance = self.ultrasonic.get_distance()
            if distance < 15:
                self.motors.stop()
                time.sleep(0.2)
                self.servos.move_angle(scan_servo, -45)
                time.sleep(0.3)
                dist_left = self.ultrasonic.get_distance()
                self.servos.move_angle(scan_servo, 45)
                time.sleep(0.3)
                dist_right = self.ultrasonic.get_distance()
                self.servos.move_angle(scan_servo, 0)
                time.sleep(0.1)
                if dist_left > dist_right:
                    self.motors.move(30, 'forward', 'left', 0.5)
                else:
                    self.motors.move(30, 'forward', 'right', 0.5)
                time.sleep(0.5)
            elif distance < 30:
                self.motors.move(20, 'forward', 'no', 0.5)
                time.sleep(0.1)
            else:
                self.motors.move(40, 'forward', 'no', 0.5)
                time.sleep(0.1)

    def _track_line(self):
        if len(self._ir_sensors) < 3:
            print("[Auto] Line tracker sensors not available")
            self.stop()
            return

        while self._active:
            left   = not self._ir_sensors[0].value
            middle = not self._ir_sensors[1].value
            right  = not self._ir_sensors[2].value

            if middle and not left and not right:
                self.motors.move(35, 'forward', 'no', 0.5)
            elif middle and left:
                self.motors.move(30, 'forward', 'right', 0.6)
            elif middle and right:
                self.motors.move(30, 'forward', 'left', 0.6)
            elif left:
                self.motors.move(25, 'forward', 'left', 0.4)
            elif right:
                self.motors.move(25, 'forward', 'right', 0.4)
            else:
                self.motors.stop()

            time.sleep(0.05)

    def _keep_distance(self):
        if not ULTRASONIC_ENABLED:
            self.stop()
            return
        target = 20
        while self._active:
            distance = self.ultrasonic.get_distance()
            if distance < target - 3:
                self.motors.move(20, 'backward', 'no', 0.5)
            elif distance > target + 3:
                self.motors.move(20, 'forward', 'no', 0.5)
            else:
                self.motors.stop()
            time.sleep(0.1)

    def shutdown(self):
        self.stop()
        self._running = False
        self._flag.set()
        for sensor in self._ir_sensors:
            try:
                sensor.close()
            except Exception:
                pass
        print("[Auto] Shutdown")
