import threading
import time
from config import (
    SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT,
    LINE_LEFT_PIN, LINE_RIGHT_PIN,
    CV_LINE_FOLLOW_SPEED, CV_LINE_FOLLOW_STEER_GAIN,
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

        self._ir_left = None
        self._ir_right = None
        self._ir_available = False
        try:
            from gpiozero import InputDevice
            self._ir_left = InputDevice(LINE_LEFT_PIN)
            self._ir_right = InputDevice(LINE_RIGHT_PIN)
            self._ir_available = True
            logger.info("[Auto] IR line sensors initialized (L=GPIO%d, R=GPIO%d)"
                        % (LINE_LEFT_PIN, LINE_RIGHT_PIN))
        except Exception as e:
            logger.error(f"[Auto] IR sensors failed: {e}")

        self._camera = None

        self._hand_pan = 90
        self._hand_tilt = 90
        self._hand_smooth_x = 0.0
        self._hand_smooth_y = 0.0
        self._hand_history = []
        self._hand_shake_count = 0
        self._hand_last_seen = 0.0

    def set_camera(self, camera):
        self._camera = camera

    def _ultra_ok(self):
        return self.ultrasonic and self.ultrasonic._initialized

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
        if mode in ("radarScan", "automatic", "keepDistance"):
            if not self._ultra_ok():
                logger.warning(f"[Auto] Cannot start {mode}: ultrasonic not available")
                return False, "Ultrasonic sensor not available"
        if mode == "trackLine":
            if not self._ir_available:
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
        if self._camera:
            self._camera.cv_thread.on_hand_found = None
            self._camera.cv_thread.on_line_found = None
            self._camera.set_cv_mode("none")
        self._current_mode = "none"

    def is_active(self):
        return self._active

    def get_radar_data(self):
        return self._radar_data

    def _read_ir(self):
        if not self._ir_available:
            return False, False
        try:
            left  = not self._ir_right.value
            right = not self._ir_left.value
            return left, right
        except Exception:
            return False, False

    def get_ir_values(self):
        if not self._ir_available:
            return None, None
        try:
            return self._ir_left.value, self._ir_right.value
        except Exception:
            return None, None

    def _radar_scan(self):
        if not self._ultra_ok():
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

    def _automatic(self):
        if not self._ultra_ok():
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

    def _track_line(self):
        if not self._ir_available:
            logger.warning("[Auto] Line tracker sensors not available")
            self.stop()
            return

        while self._active:
            left, right = self._read_ir()

            if left and right:
                self.motors.move(35, 'forward', 'no', 0.5)
            elif left and not right:
                self.motors.move(25, 'forward', 'left', 0.4)
            elif right and not left:
                self.motors.move(25, 'forward', 'right', 0.4)
            else:
                self.motors.move(15, 'forward', 'no', 0.5)

            time.sleep(0.05)

    def _track_line_cv(self):
        from Server.camera.camera_opencv import CV_LINE

        if not self._camera:
            logger.warning("[Auto] No camera for CV line following")
            self.stop()
            return

        self._camera._init_camera()
        if not self._camera._picam:
            logger.error("[Auto] Camera init failed")
            self.stop()
            return

        self._camera.set_cv_mode(CV_LINE)

        frame_w = CAMERA_RESOLUTION[0]
        frame_h = CAMERA_RESOLUTION[1]
        centre_x = frame_w / 2.0
        speed = CV_LINE_FOLLOW_SPEED
        steer_gain = CV_LINE_FOLLOW_STEER_GAIN

        ir_available = self._ir_available
        if ir_available:
            logger.info(f"[Auto] CV line follow started (speed={speed}, gain={steer_gain}, IR sensors ON)")
        else:
            logger.info(f"[Auto] CV line follow started (speed={speed}, gain={steer_gain}, IR sensors OFF)")

        IR_STEER_BIAS    = 0.25
        IR_SPEED_PENALTY = 0.3

        smooth_offset = 0.0
        SMOOTH_ALPHA  = 0.4

        line_lost_count = 0
        last_known_offset = 0.0

        cv_line_pos = [0, 0]
        cv_line_found = False

        def on_line(pos, angle):
            nonlocal cv_line_pos, cv_line_found
            cv_line_pos = pos
            cv_line_found = pos[0] > 0 or pos[1] > 0

        self._camera.cv_thread.on_line_found = on_line

        while self._active:
            try:
                ir_left, ir_right = self._read_ir()

                if cv_line_found:
                    p1, p2 = cv_line_pos
                    line_centre = (p1 + p2) / 2.0 if (p1 > 0 and p2 > 0) else (p1 if p1 > 0 else p2)
                    raw_offset = (line_centre - centre_x) / centre_x if centre_x > 0 else 0.0

                    line_lost_count = 0
                    smooth_offset = SMOOTH_ALPHA * raw_offset + (1 - SMOOTH_ALPHA) * smooth_offset
                    last_known_offset = smooth_offset

                    ir_offset = 0.0
                    if ir_available:
                        if ir_left and not ir_right:
                            ir_offset = -1.0
                        elif ir_right and not ir_left:
                            ir_offset = 1.0

                    if ir_offset != 0.0:
                        fused_offset = smooth_offset + ir_offset * IR_STEER_BIAS
                        fused_offset = max(-1.0, min(1.0, fused_offset))
                    else:
                        fused_offset = smooth_offset

                    steer_angle = 90 - int(fused_offset * 60 * steer_gain)
                    steer_angle = max(30, min(150, steer_angle))

                    turn_factor = 1.0 - abs(fused_offset) * 0.4
                    if ir_available and (ir_left or ir_right) and not (ir_left and ir_right):
                        turn_factor -= IR_SPEED_PENALTY
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
                    line_lost_count += 1

                    if ir_available and (ir_left or ir_right):
                        if ir_left and not ir_right:
                            self.motors.move(20, 'forward', 'left', 0.3)
                            self.servos.set_angle(SERVO_STEERING, 120)
                        elif ir_right and not ir_left:
                            self.motors.move(20, 'forward', 'right', 0.3)
                            self.servos.set_angle(SERVO_STEERING, 60)
                        else:
                            self.motors.move(15, 'forward', 'no', 0.5)
                            self.servos.set_angle(SERVO_STEERING, 90)
                    elif line_lost_count < 15:
                        steer_angle = 90 - int(last_known_offset * 60 * steer_gain)
                        steer_angle = max(30, min(150, steer_angle))
                        self.servos.set_angle(SERVO_STEERING, steer_angle)
                        search_speed = max(10, speed // 3)
                        if last_known_offset < -0.2:
                            self.motors.move(search_speed, 'forward', 'left', 0.3)
                        elif last_known_offset > 0.2:
                            self.motors.move(search_speed, 'forward', 'right', 0.3)
                        else:
                            self.motors.move(search_speed, 'forward', 'no', 0.5)
                    else:
                        self.motors.move(max(8, speed // 4), 'forward', 'no', 0.5)
                        self.servos.set_angle(SERVO_STEERING, 90)

                time.sleep(0.03)

            except Exception as e:
                logger.error(f"[Auto] CV line error: {e}")
                time.sleep(0.1)

    def _keep_distance(self):
        if not self._ultra_ok():
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

    def _track_hand(self):
        from Server.camera.camera_opencv import CV_HAND

        if not self._camera:
            logger.warning("[Auto] No camera for hand tracking")
            self.stop()
            return

        self._camera._init_camera()
        if not self._camera._picam:
            logger.error("[Auto] Camera init failed")
            self.stop()
            return

        self._camera.set_cv_mode(CV_HAND)

        frame_w, frame_h = CAMERA_RESOLUTION
        centre_x = frame_w / 2.0
        centre_y = frame_h / 2.0

        self._hand_pan = 90
        self._hand_tilt = 90
        self._hand_smooth_x = 0.0
        self._hand_smooth_y = 0.0
        self._hand_history = []
        self._hand_shake_count = 0
        self._hand_last_seen = time.time()
        self.servos.set_angle(SERVO_CAM_PAN, 90)
        self.servos.set_angle(SERVO_CAM_TILT, 90)
        self.servos.set_angle(SERVO_STEERING, 90)

        self.motors.stop()
        logger.info("[Auto] Hand tracking: WHEELS DISABLED — camera tracking only")

        PAN_STEP = 4
        TILT_STEP = 3
        DEADZONE = 0.06
        SMOOTH_ALPHA = 0.5

        SHAKE_WINDOW = 1.5
        SHAKE_THRESHOLD = 5

        HAND_LOST_TIMEOUT = 2.0
        HAND_REMEMBER = 0.5

        def on_hand(pos, area):
            now = time.time()

            if area == 0:
                time_since_seen = now - self._hand_last_seen
                if time_since_seen > HAND_LOST_TIMEOUT:
                    pass
                return False

            self._hand_last_seen = now

            x, y = pos
            raw_offset_x = (x - centre_x) / centre_x
            raw_offset_y = (y - centre_y) / centre_y

            self._hand_smooth_x = SMOOTH_ALPHA * raw_offset_x + (1 - SMOOTH_ALPHA) * self._hand_smooth_x
            self._hand_smooth_y = SMOOTH_ALPHA * raw_offset_y + (1 - SMOOTH_ALPHA) * self._hand_smooth_y

            offset_x = self._hand_smooth_x
            offset_y = self._hand_smooth_y

            self._hand_history.append((now, offset_x))
            self._hand_history = [(t, ox) for t, ox in self._hand_history if now - t < SHAKE_WINDOW]
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
                self._hand_shake_count += 1
                return True

            if abs(offset_x) > DEADZONE:
                pan_delta = int(offset_x * PAN_STEP * (1 + abs(offset_x)))
                self._hand_pan -= pan_delta
                self._hand_pan = max(0, min(180, self._hand_pan))
            if abs(offset_y) > DEADZONE:
                tilt_delta = int(offset_y * TILT_STEP * (1 + abs(offset_y)))
                self._hand_tilt += tilt_delta
                self._hand_tilt = max(0, min(180, self._hand_tilt))

            self.servos.set_angle(SERVO_CAM_PAN, self._hand_pan)
            self.servos.set_angle(SERVO_CAM_TILT, self._hand_tilt)

            return False

        self._camera.cv_thread.on_hand_found = on_hand

        logger.info("[Auto] Hand tracking started (wheels disabled, camera only) — shake hand to stop")

        try:
            while self._active and self._hand_shake_count == 0:
                time.sleep(0.1)
        finally:
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
