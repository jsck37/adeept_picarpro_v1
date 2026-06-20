import threading, time, math
from Server.logger import logger
from config import (
    SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT,
    LINE_LEFT_PIN, LINE_MIDDLE_PIN, LINE_RIGHT_PIN,
    CV_LINE_POS_1, CV_LINE_POS_2,
    CV_LINE_FOLLOW_SPEED, CV_LINE_FOLLOW_STEER_GAIN,
    CAMERA_RESOLUTION,
)

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None


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
        self._current_mode = 'none'
        self._radar_data = []
        self._ir_left_pin = LINE_LEFT_PIN
        self._ir_mid_pin = LINE_MIDDLE_PIN
        self._ir_right_pin = LINE_RIGHT_PIN
        self._ir_available = False
        if _HAS_GPIO:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
                GPIO.setup(self._ir_left_pin, GPIO.IN)
                GPIO.setup(self._ir_mid_pin, GPIO.IN)
                GPIO.setup(self._ir_right_pin, GPIO.IN)
                self._ir_available = True
                logger.info(f'[Auto] IR sensors OK — L{LINE_LEFT_PIN} M{LINE_MIDDLE_PIN} R{LINE_RIGHT_PIN}')
            except Exception as e:
                logger.error(f'[Auto] IR init failed: {e}')
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
                if self._current_mode == 'radarScan':
                    self._radar_scan()
                elif self._current_mode == 'automatic':
                    self._automatic()
                elif self._current_mode == 'trackLine':
                    self._track_line()
                elif self._current_mode == 'trackLineCV':
                    self._track_line_cv()
                elif self._current_mode == 'keepDistance':
                    self._keep_distance()
                elif self._current_mode == 'trackHand':
                    self._track_hand()
            except Exception as e:
                logger.error(f'[Auto] error in {self._current_mode}: {e}')
                self.stop()

    def start(self, mode):
        if mode in ('radarScan', 'automatic', 'keepDistance'):
            if not self._ultra_ok():
                logger.warning(f'[Auto] cannot start {mode}: ultrasonic not available')
                return False, 'Ultrasonic sensor not available'
        if mode in ('trackLineCV', 'trackHand') and not self._camera:
            logger.warning(f'[Auto] cannot start {mode}: camera not available')
            return False, 'Camera not available'
        self.stop()
        self._current_mode = mode
        self._active = True
        self._flag.set()
        logger.info(f'[Auto] started: {mode}')
        return True, f'Started: {mode}'

    def stop(self):
        self._active = False
        self._flag.clear()
        if self.motors:
            self.motors.stop()
        if self.servos:
            self.servos.stop_all()
        if self._camera:
            try:
                self._camera.cv_thread.on_hand_found = None
                self._camera.cv_thread.on_line_found = None
                self._camera.set_cv_mode('none')
            except Exception:
                pass
        self._current_mode = 'none'

    def is_active(self):
        return self._active

    def get_radar_data(self):
        return self._radar_data

    def _read_ir(self):
        if not self._ir_available:
            return False, False, False
        try:
            return (GPIO.input(self._ir_left_pin) == 0,
                    GPIO.input(self._ir_mid_pin) == 0,
                    GPIO.input(self._ir_right_pin) == 0)
        except Exception:
            return False, False, False

    def get_ir_values(self):
        if not self._ir_available:
            return None, None, None
        try:
            return (GPIO.input(self._ir_left_pin),
                    GPIO.input(self._ir_mid_pin),
                    GPIO.input(self._ir_right_pin))
        except Exception:
            return None, None, None

    def _radar_scan(self):
        if not self._ultra_ok():
            self.stop()
            return
        self._radar_data = []
        for angle_off in range(-60, 61, 5):
            if not self._active:
                break
            self.servos.move_angle(SERVO_STEERING, angle_off)
            time.sleep(0.1)
            d = self.ultrasonic.get_last_distance()
            self._radar_data.append({'angle': angle_off, 'distance': d})
        self.servos.move_angle(SERVO_STEERING, 0)
        self.stop()

    def _automatic(self):
        if not self._ultra_ok():
            self.stop()
            return
        while self._active:
            d = self.ultrasonic.get_last_distance()
            if d < 15:
                self.motors.stop()
                time.sleep(0.2)
                self.servos.move_angle(SERVO_STEERING, -45)
                time.sleep(0.3)
                d_left = self.ultrasonic.get_last_distance()
                self.servos.move_angle(SERVO_STEERING, 45)
                time.sleep(0.3)
                d_right = self.ultrasonic.get_last_distance()
                self.servos.move_angle(SERVO_STEERING, 0)
                time.sleep(0.1)
                if d_left > d_right:
                    self.motors.move(30, 'forward', 'left', 0.5)
                else:
                    self.motors.move(30, 'forward', 'right', 0.5)
                time.sleep(0.5)
            elif d < 30:
                self.motors.move(20, 'forward', 'no', 0.5)
                time.sleep(0.1)
            else:
                self.motors.move(40, 'forward', 'no', 0.5)
                time.sleep(0.1)

    def _track_line(self):
        if not self._ir_available:
            logger.warning('[Auto] IR sensors not available')
            self.stop()
            return
        speed = 35
        while self._active:
            left, mid, right = self._read_ir()
            if mid:
                if left and not right:
                    self.motors.move(speed, 'forward', 'left', 0.4)
                elif right and not left:
                    self.motors.move(speed, 'forward', 'right', 0.4)
                else:
                    self.motors.move(speed, 'forward', 'no', 0.5)
            elif left and not right:
                self.motors.move(speed - 10, 'forward', 'left', 0.4)
            elif right and not left:
                self.motors.move(speed - 10, 'forward', 'right', 0.4)
            else:
                self.motors.move(15, 'forward', 'no', 0.5)
            time.sleep(0.05)

    def _track_line_cv(self):
        from Server.camera.camera_opencv import CV_LINE
        if not self._camera:
            self.stop()
            return
        self._camera.set_cv_mode(CV_LINE)
        centre_x = CAMERA_RESOLUTION[0] / 2.0
        speed = CV_LINE_FOLLOW_SPEED
        gain = CV_LINE_FOLLOW_STEER_GAIN
        ir_ok = self._ir_available
        smooth = 0.0
        SMOOTH_A = 0.4
        lost_count = 0
        last_offset = 0.0
        cv_pos = [0, 0]
        cv_found = False
        def on_line(pos, angle):
            nonlocal cv_pos, cv_found
            cv_pos = pos
            cv_found = pos[0] > 0 or pos[1] > 0
        self._camera.cv_thread.on_line_found = on_line
        while self._active:
            try:
                ir_l, ir_m, ir_r = self._read_ir()
                ir_off = 0.0
                if ir_ok:
                    if ir_l and not ir_r:
                        ir_off = -1.0
                    elif ir_r and not ir_l:
                        ir_off = 1.0
                if cv_found:
                    p1, p2 = cv_pos
                    line_c = (p1 + p2) / 2.0 if (p1 > 0 and p2 > 0) else (p1 if p1 > 0 else p2)
                    raw = (line_c - centre_x) / centre_x if centre_x > 0 else 0.0
                    lost_count = 0
                    smooth = SMOOTH_A * raw + (1 - SMOOTH_A) * smooth
                    last_offset = smooth
                    fused = (smooth + ir_off * 0.25) if ir_off != 0 else smooth
                    fused = max(-1.0, min(1.0, fused))
                    steer = 90 - int(fused * 60 * gain)
                    steer = max(30, min(150, steer))
                    turn_f = 1.0 - abs(fused) * 0.4
                    if ir_ok and (ir_l or ir_r) and not (ir_l and ir_r):
                        turn_f -= 0.3
                    actual_speed = max(15, int(speed * turn_f))
                    self.servos.set_angle(SERVO_STEERING, steer)
                    if fused < -0.3:
                        self.motors.move(actual_speed, 'forward', 'left', max(0.2, 0.5 + fused * 0.3))
                    elif fused > 0.3:
                        self.motors.move(actual_speed, 'forward', 'right', max(0.2, 0.5 - fused * 0.3))
                    else:
                        self.motors.move(actual_speed, 'forward', 'no', 0.5)
                else:
                    lost_count += 1
                    if ir_ok and (ir_l or ir_r):
                        if ir_l and not ir_r:
                            self.motors.move(20, 'forward', 'left', 0.3)
                            self.servos.set_angle(SERVO_STEERING, 120)
                        elif ir_r and not ir_l:
                            self.motors.move(20, 'forward', 'right', 0.3)
                            self.servos.set_angle(SERVO_STEERING, 60)
                        else:
                            self.motors.move(15, 'forward', 'no', 0.5)
                            self.servos.set_angle(SERVO_STEERING, 90)
                    elif lost_count < 15:
                        steer = 90 - int(last_offset * 60 * gain)
                        steer = max(30, min(150, steer))
                        self.servos.set_angle(SERVO_STEERING, steer)
                        s = max(10, speed // 3)
                        if last_offset < -0.2:
                            self.motors.move(s, 'forward', 'left', 0.3)
                        elif last_offset > 0.2:
                            self.motors.move(s, 'forward', 'right', 0.3)
                        else:
                            self.motors.move(s, 'forward', 'no', 0.5)
                    else:
                        self.motors.move(max(8, speed // 4), 'forward', 'no', 0.5)
                        self.servos.set_angle(SERVO_STEERING, 90)
                time.sleep(0.03)
            except Exception as e:
                logger.error(f'[Auto] CV line error: {e}')
                time.sleep(0.1)

    def _keep_distance(self):
        if not self._ultra_ok():
            self.stop()
            return
        target = 20
        while self._active:
            d = self.ultrasonic.get_last_distance()
            if d < target - 3:
                self.motors.move(20, 'backward', 'no', 0.5)
            elif d > target + 3:
                self.motors.move(20, 'forward', 'no', 0.5)
            else:
                self.motors.stop()
            time.sleep(0.1)

    def _track_hand(self):
        from Server.camera.camera_opencv import CV_HAND
        if not self._camera:
            self.stop()
            return
        self._camera.set_cv_mode(CV_HAND)
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
        PAN_STEP = 4
        TILT_STEP = 3
        DEADZONE = 0.06
        SMOOTH_A = 0.5
        SHAKE_WIN = 1.5
        SHAKE_TH = 5
        def on_hand(pos, area):
            now = time.time()
            if area == 0:
                return False
            self._hand_last_seen = now
            x, y = pos
            cx = CAMERA_RESOLUTION[0] / 2.0
            cy = (CAMERA_RESOLUTION[0] * 0.75) / 2.0
            raw_x = (x - cx) / cx
            raw_y = (y - cy) / cy
            self._hand_smooth_x = SMOOTH_A * raw_x + (1 - SMOOTH_A) * self._hand_smooth_x
            self._hand_smooth_y = SMOOTH_A * raw_y + (1 - SMOOTH_A) * self._hand_smooth_y
            self._hand_history.append((now, self._hand_smooth_x))
            self._hand_history = [(t, ox) for t, ox in self._hand_history if now - t < SHAKE_WIN]
            reversals = 0
            if len(self._hand_history) > 2:
                prev = None
                for _, ox in self._hand_history:
                    cur = 1 if ox > 0 else -1
                    if prev is not None and cur != prev:
                        reversals += 1
                    prev = cur
            if reversals >= SHAKE_TH:
                self._hand_shake_count += 1
                return True
            if abs(self._hand_smooth_x) > DEADZONE:
                delta = int(self._hand_smooth_x * PAN_STEP * (1 + abs(self._hand_smooth_x)))
                self._hand_pan -= delta
                self._hand_pan = max(0, min(180, self._hand_pan))
            if abs(self._hand_smooth_y) > DEADZONE:
                delta = int(self._hand_smooth_y * TILT_STEP * (1 + abs(self._hand_smooth_y)))
                self._hand_tilt += delta
                self._hand_tilt = max(0, min(180, self._hand_tilt))
            self.servos.set_angle(SERVO_CAM_PAN, self._hand_pan)
            self.servos.set_angle(SERVO_CAM_TILT, self._hand_tilt)
            return False
        self._camera.cv_thread.on_hand_found = on_hand
        try:
            while self._active and self._hand_shake_count == 0:
                time.sleep(0.1)
        finally:
            self._camera.cv_thread.on_hand_found = None
            self._camera.set_cv_mode('none')
            self.motors.stop()
            self.servos.set_angle(SERVO_STEERING, 90)
            self.servos.set_angle(SERVO_CAM_PAN, 90)
            self.servos.set_angle(SERVO_CAM_TILT, 90)

    def shutdown(self):
        self.stop()
        self._running = False
        self._flag.set()
        logger.info('[Auto] shutdown')
