"""Autonomous robot functions.

Modes:
  - radarScan:      Ultrasonic radar sweep (requires ULTRASONIC_ENABLED)
  - automatic:      Obstacle avoidance driving  (requires ULTRASONIC_ENABLED)
  - trackLine:      IR-sensor line following    (requires LINE_TRACKER_ENABLED)
  - trackLineCV:    OpenCV + IR sensor line following (requires camera)
  - keepDistance:    Hold distance from obstacle  (requires ULTRASONIC_ENABLED)
"""

import threading
import time
from Server.config import (
    SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT,
    ULTRASONIC_ENABLED, LINE_TRACKER_ENABLED,
    LINE_LEFT_PIN, LINE_RIGHT_PIN,
    CV_LINE_FOLLOW_SPEED, CV_LINE_FOLLOW_STEER_GAIN, CV_LINE_FOLLOW_SCAN_Y_RATIO,
    CAMERA_RESOLUTION,
)
from Server.logger import logger


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

        # Line tracker IR sensors — 2 sensors (left + right)
        self._ir_left = None
        self._ir_right = None
        self._ir_available = False
        if LINE_TRACKER_ENABLED:
            try:
                from gpiozero import InputDevice
                self._ir_left = InputDevice(LINE_LEFT_PIN)
                self._ir_right = InputDevice(LINE_RIGHT_PIN)
                self._ir_available = True
                logger.info("[Auto] IR line sensors initialized (L=GPIO%d, R=GPIO%d)"
                            % (LINE_LEFT_PIN, LINE_RIGHT_PIN))
            except Exception as e:
                logger.error(f"[Auto] IR sensors failed: {e}")
        else:
            logger.warning("[Auto] Line tracker DISABLED in config")

        # Camera ref for CV line following (set later)
        self._camera = None

        # Hand tracking state
        self._hand_pan = 90
        self._hand_tilt = 90
        self._hand_history = []       # list of (timestamp, x, y) for shake detection
        self._hand_shake_count = 0

    def set_camera(self, camera):
        """Set camera reference for CV-based line following."""
        self._camera = camera

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
                elif self._current_mode == "trackLineCV":
                    self._track_line_cv()
                elif self._current_mode == "keepDistance":
                    self._keep_distance()
                elif self._current_mode == "trackHand":
                    self._track_hand()
            except Exception as e:
                logger.error(f"[Auto] Error in {self._current_mode}: {e}")
                self.stop()

    def start(self, mode):
        """Start an autonomous mode."""
        if mode in ("radarScan", "automatic", "keepDistance"):
            if not ULTRASONIC_ENABLED or not self.ultrasonic._initialized:
                logger.warning(f"[Auto] Cannot start {mode}: ultrasonic not available")
                return False, "Ultrasonic sensor not available"
        if mode == "trackLine":
            if not LINE_TRACKER_ENABLED or not self._ir_available:
                logger.warning("[Auto] Cannot start trackLine: IR sensors not available")
                return False, "Line tracker not available"
        if mode == "trackLineCV":
            if not self._camera:
                logger.warning("[Auto] Cannot start trackLineCV: camera not available")
                return False, "Camera not available"
        if mode == "trackHand":
            if not self._camera:
                logger.warning("[Auto] Cannot start trackHand: camera not available")
                return False, "Camera not available"

        self.stop()
        self._current_mode = mode
        self._active = True
        self._flag.set()
        logger.info(f"[Auto] Started: {mode}")
        return True, f"Started: {mode}"

    def stop(self):
        self._active = False
        self._flag.clear()
        self.motors.stop()
        self.servos.stop_all()
        # Reset CV mode when stopping autonomous functions
        if self._camera:
            self._camera.cv_thread.on_hand_found = None
            self._camera.set_cv_mode("none")
        self._current_mode = "none"

    def is_active(self):
        return self._active

    def get_radar_data(self):
        return self._radar_data

    # ── IR sensor reader ───────────────────────────────────────────────

    def _read_ir(self):
        """Read IR line sensors.  Returns (left_on_line, right_on_line).

        IR sensors output LOW (0) when detecting a black line,
        HIGH (1) on white/background.  We invert so True = on line.
        """
        if not self._ir_available:
            return False, False
        try:
            left  = not self._ir_left.value   # True = line detected under left sensor
            right = not self._ir_right.value   # True = line detected under right sensor
            return left, right
        except Exception:
            return False, False

    # ── Ultrasonic radar scan ──────────────────────────────────────────

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
            distance = self.ultrasonic.get_last_distance()
            self._radar_data.append({'angle': angle_offset, 'distance': distance})

        self.servos.move_angle(scan_servo, 0)
        self.stop()

    # ── Obstacle avoidance ─────────────────────────────────────────────

    def _automatic(self):
        if not ULTRASONIC_ENABLED:
            self.stop()
            return
        scan_servo = SERVO_STEERING

        while self._active:
            distance = self.ultrasonic.get_last_distance()
            if distance < 15:
                self.motors.stop()
                time.sleep(0.2)
                self.servos.move_angle(scan_servo, -45)
                time.sleep(0.3)
                dist_left = self.ultrasonic.get_last_distance()
                self.servos.move_angle(scan_servo, 45)
                time.sleep(0.3)
                dist_right = self.ultrasonic.get_last_distance()
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

    # ── IR line following (2 sensors) ──────────────────────────────────

    def _track_line(self):
        """IR-only line following with 2 sensors (left + right)."""
        if not self._ir_available:
            logger.warning("[Auto] Line tracker sensors not available")
            self.stop()
            return

        while self._active:
            left, right = self._read_ir()

            if left and right:
                # Both sensors on line — go straight (line is wide / crossroads)
                self.motors.move(35, 'forward', 'no', 0.5)
            elif left and not right:
                # Line drifted left — steer left
                self.motors.move(25, 'forward', 'left', 0.4)
            elif right and not left:
                # Line drifted right — steer right
                self.motors.move(25, 'forward', 'right', 0.4)
            else:
                # No line under either sensor — search slowly
                self.motors.move(15, 'forward', 'no', 0.5)

            time.sleep(0.05)

    # ── OpenCV + IR line following ─────────────────────────────────────

    def _track_line_cv(self):
        """Follow a black line using OpenCV + IR sensor fusion.

        The camera sees the line ahead (long range) and IR sensors
        detect the line right under the wheels (close range).

        Fusion strategy:
          - CV is the primary steering source (sees ahead, allows smooth curves)
          - IR provides confirmation and close-range correction:
            • CV sees line + IR confirms → boost steering confidence
            • CV sees line + IR disagrees → add small IR bias (trust CV)
            • CV loses line + IR still sees → IR fallback (slow, strong turn)
            • CV loses line + IR lost too  → slow search forward

        This makes CV Line mode significantly more robust:
          - Less likely to lose the line on sharp bends
          - Faster recovery when the line reappears under the wheels
          - IR acts as a safety net when camera glare/blur occurs
        """
        import cv2
        import numpy as np

        if not self._camera:
            logger.warning("[Auto] No camera for CV line following")
            self.stop()
            return

        # Ensure camera is initialised
        self._camera._init_camera()
        if not self._camera._picam:
            logger.error("[Auto] Camera init failed")
            self.stop()
            return

        frame_w = CAMERA_RESOLUTION[0]
        centre_x = frame_w / 2.0
        scan_y_ratio = CV_LINE_FOLLOW_SCAN_Y_RATIO
        speed = CV_LINE_FOLLOW_SPEED
        steer_gain = CV_LINE_FOLLOW_STEER_GAIN

        ir_available = self._ir_available
        if ir_available:
            logger.info(f"[Auto] CV line follow started (speed={speed}, gain={steer_gain}, IR sensors ON)")
        else:
            logger.info(f"[Auto] CV line follow started (speed={speed}, gain={steer_gain}, IR sensors OFF)")

        # ── IR influence weights ──
        # When IR sensors detect the line near the wheels (close-range),
        # they can confirm or override the CV steering decision.
        # IR is more reliable at close range, CV at longer range.
        IR_STEER_BIAS    = 0.25    # how much IR adds to steering offset
        IR_SPEED_PENALTY = 0.3     # speed reduction when IR detects off-centre

        while self._active:
            try:
                # ── Read IR sensors first (fast, no frame needed) ──
                ir_left, ir_right = self._read_ir()

                # ── Capture frame ──
                raw = self._camera._picam.capture_array()
                if raw is None or len(raw.shape) != 3:
                    time.sleep(0.05)
                    continue

                frame = raw
                h, w = frame.shape[:2]
                scan_y = int(h * scan_y_ratio)

                # Convert to grayscale and threshold
                gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                _, binary = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)

                # Find centre of mass of black pixels at scan row
                scan_line = binary[scan_y]
                indices = np.where(scan_line > 0)[0]

                cv_line_found = len(indices) > 10

                if cv_line_found:
                    # ── CV: line detected — calculate offset ──
                    line_centre = int(np.mean(indices))
                    cv_offset = (line_centre - w / 2.0) / (w / 2.0)  # normalised -1..+1

                    # ── IR: compute supplementary offset ──
                    ir_offset = 0.0
                    if ir_available:
                        if ir_left and not ir_right:
                            ir_offset = -1.0   # line under left sensor → steer left
                        elif ir_right and not ir_left:
                            ir_offset = 1.0    # line under right sensor → steer right
                        elif ir_left and ir_right:
                            ir_offset = 0.0    # line under both → centred
                        # If neither sensor sees the line, ir_offset stays 0

                    # ── Fuse CV + IR offsets ──
                    # CV is the primary source (sees ahead), IR confirms at wheel level
                    # If CV and IR agree → boost steering
                    # If they disagree → trust CV more but add small IR bias
                    if ir_offset != 0.0:
                        # IR has a reading — blend with CV
                        fused_offset = cv_offset + ir_offset * IR_STEER_BIAS
                        fused_offset = max(-1.0, min(1.0, fused_offset))
                    else:
                        fused_offset = cv_offset

                    # Steer
                    steer_angle = 90 - int(fused_offset * 60 * steer_gain)
                    steer_angle = max(30, min(150, steer_angle))

                    # Speed: reduce on sharp turns, extra penalty if IR says off-centre
                    turn_factor = 1.0 - abs(fused_offset) * 0.4
                    if ir_available and (ir_left or ir_right) and not (ir_left and ir_right):
                        turn_factor -= IR_SPEED_PENALTY   # one sensor on line = sharp correction
                    actual_speed = max(15, int(speed * turn_factor))

                    self.servos.set_angle(SERVO_STEERING, steer_angle)

                    if fused_offset < -0.3:
                        self.motors.move(actual_speed, 'forward', 'left',
                                         max(0.2, 0.5 + fused_offset * 0.3))
                    elif fused_offset > 0.3:
                        self.motors.move(actual_speed, 'forward', 'right',
                                         max(0.2, 0.5 - fused_offset * 0.3))
                    else:
                        self.motors.move(actual_speed, 'forward', 'no', 0.5)

                else:
                    # ── CV: no line detected in camera frame ──
                    # Use IR sensors as fallback — they can still see the line
                    if ir_available and (ir_left or ir_right):
                        if ir_left and not ir_right:
                            self.motors.move(20, 'forward', 'left', 0.3)
                            self.servos.set_angle(SERVO_STEERING, 120)
                        elif ir_right and not ir_left:
                            self.motors.move(20, 'forward', 'right', 0.3)
                            self.servos.set_angle(SERVO_STEERING, 60)
                        else:
                            # Both on line — probably at crossroads, go straight slowly
                            self.motors.move(15, 'forward', 'no', 0.5)
                            self.servos.set_angle(SERVO_STEERING, 90)
                    else:
                        # No CV, no IR — line completely lost, search slowly
                        self.motors.move(max(10, speed // 3), 'forward', 'no', 0.5)

                time.sleep(0.05)

            except Exception as e:
                logger.error(f"[Auto] CV line error: {e}")
                time.sleep(0.1)

    # ── Keep distance ──────────────────────────────────────────────────

    def _keep_distance(self):
        if not ULTRASONIC_ENABLED:
            self.stop()
            return
        target = 20
        while self._active:
            distance = self.ultrasonic.get_last_distance()
            if distance < target - 3:
                self.motors.move(20, 'backward', 'no', 0.5)
            elif distance > target + 3:
                self.motors.move(20, 'forward', 'no', 0.5)
            else:
                self.motors.stop()
            time.sleep(0.1)

    # ── Hand tracking ──────────────────────────────────────────────────

    def _track_hand(self):
        """Track a hand using OpenCV skin-colour detection.

        The camera (pan/tilt servos) follows the hand to keep it centred
        in the frame.  If the hand moves too far horizontally for the
        camera to track, the car rotates in place to follow it.

        Shake detection: if rapid horizontal position reversals are
        detected (the hand is shaken left-right), the mode automatically
        stops.  This is detected by tracking the hand x-position history
        and counting direction reversals within a short time window.
        """
        from Server.camera.camera_opencv import CV_HAND

        if not self._camera:
            logger.warning("[Auto] No camera for hand tracking")
            self.stop()
            return

        # Ensure camera is initialised
        self._camera._init_camera()
        if not self._camera._picam:
            logger.error("[Auto] Camera init failed")
            self.stop()
            return

        # Set CV mode to hand tracking
        self._camera.set_cv_mode(CV_HAND)

        frame_w, frame_h = CAMERA_RESOLUTION
        centre_x = frame_w / 2.0
        centre_y = frame_h / 2.0

        # Reset state
        self._hand_pan = 90
        self._hand_tilt = 90
        self._hand_history = []
        self._hand_shake_count = 0
        self.servos.set_angle(SERVO_CAM_PAN, 90)
        self.servos.set_angle(SERVO_CAM_TILT, 90)
        self.servos.set_angle(SERVO_STEERING, 90)

        PAN_STEP = 3       # degrees per update
        TILT_STEP = 2      # degrees per update
        STEER_STEP = 5     # steering servo degrees per update
        DEADZONE = 0.08    # fraction of frame — ignore small offsets
        SHAKE_WINDOW = 1.5 # seconds to look back for shake detection
        SHAKE_THRESHOLD = 5 # number of direction reversals = shake

        # Callback for hand detection from CV thread
        def on_hand(pos, area):
            now = time.time()

            if area == 0:
                # Hand lost
                self.motors.stop()
                self.servos.set_angle(SERVO_STEERING, 90)
                return False

            x, y = pos
            # Normalised offset from centre (-1..+1)
            offset_x = (x - centre_x) / centre_x
            offset_y = (y - centre_y) / centre_y

            # ── Shake detection ──
            self._hand_history.append((now, offset_x))
            # Prune old entries
            self._hand_history = [(t, ox) for t, ox in self._hand_history if now - t < SHAKE_WINDOW]
            # Count direction reversals in the window
            reversals = 0
            if len(self._hand_history) > 2:
                prev_dir = None
                for t, ox in self._hand_history:
                    cur_dir = 1 if ox > 0 else -1
                    if prev_dir is not None and cur_dir != prev_dir:
                        reversals += 1
                    prev_dir = cur_dir

            if reversals >= SHAKE_THRESHOLD:
                logger.info("[Auto] Hand shake detected — stopping hand tracking")
                self._hand_shake_count += 1  # flag for main loop
                return True  # signal shake detected

            # ── Camera pan/tilt ──
            if abs(offset_x) > DEADZONE:
                self._hand_pan -= int(offset_x * PAN_STEP)
                self._hand_pan = max(0, min(180, self._hand_pan))
            if abs(offset_y) > DEADZONE:
                self._hand_tilt += int(offset_y * TILT_STEP)
                self._hand_tilt = max(0, min(180, self._hand_tilt))

            self.servos.set_angle(SERVO_CAM_PAN, self._hand_pan)
            self.servos.set_angle(SERVO_CAM_TILT, self._hand_tilt)

            # ── Car rotation when hand is near camera edge ──
            # If camera pan is near its limit, rotate the car to follow
            if self._hand_pan < 30 or self._hand_pan > 150:
                if self._hand_pan < 30:
                    # Hand is far left — rotate car left
                    steer_angle = max(30, 90 - STEER_STEP * 3)
                    self.motors.move(20, 'forward', 'left', 0.4)
                else:
                    # Hand is far right — rotate car right
                    steer_angle = min(150, 90 + STEER_STEP * 3)
                    self.motors.move(20, 'forward', 'right', 0.4)
                self.servos.set_angle(SERVO_STEERING, steer_angle)

                # Re-centre camera slightly so it doesn't stay at the edge
                if self._hand_pan < 30:
                    self._hand_pan += STEER_STEP
                else:
                    self._hand_pan -= STEER_STEP
                self._hand_pan = max(0, min(180, self._hand_pan))
                self.servos.set_angle(SERVO_CAM_PAN, self._hand_pan)
            else:
                # Hand is within camera range — stay still, just track
                self.motors.stop()
                self.servos.set_angle(SERVO_STEERING, 90)

            return False

        # Register callback
        self._camera.cv_thread.on_hand_found = on_hand

        logger.info("[Auto] Hand tracking started — shake hand to stop")

        # Keep the thread alive until stopped or shake detected
        try:
            while self._active and self._hand_shake_count == 0:
                time.sleep(0.1)
        finally:
            # Clean up
            self._camera.cv_thread.on_hand_found = None
            self._camera.set_cv_mode("none")
            self.motors.stop()
            self.servos.set_angle(SERVO_STEERING, 90)
            self.servos.set_angle(SERVO_CAM_PAN, 90)
            self.servos.set_angle(SERVO_CAM_TILT, 90)
            logger.info("[Auto] Hand tracking stopped")

    def shutdown(self):
        self.stop()
        self._running = False
        self._flag.set()
        for sensor in (self._ir_left, self._ir_right):
            if sensor:
                try:
                    sensor.close()
                except Exception:
                    pass
        logger.info("[Auto] Shutdown")
