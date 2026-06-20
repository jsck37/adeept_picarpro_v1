import json, os, threading, time
from Server.logger import logger
from config import (
    PCA9685_SERVO_ADDR, PCA9685_SERVO_FREQ, I2C_BUS,
    SERVO_COUNT, SERVO_MIN_PULSE, SERVO_MAX_PULSE, SERVO_INIT_ANGLE,
    SERVO_INIT_ANGLES, SERVO_LIMITS,
    CRANE_ARM_OPEN, CRANE_GRIP_HIGH,
)

try:
    import busio
    from adafruit_pca9685 import PCA9685
    from adafruit_motor import servo as adafruit_servo
    _HAS_PCA = True
except ImportError:
    _HAS_PCA = False

SERVO_CAL_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'servo_cal.json')


def _load_cal():
    try:
        if os.path.isfile(SERVO_CAL_FILE):
            with open(SERVO_CAL_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cal(data):
    try:
        with open(SERVO_CAL_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


class ServoController:
    def __init__(self):
        self._pca = None
        self._servos = [None] * SERVO_COUNT
        self._angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._init_angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._lock = threading.Lock()
        self._pwm_initialized = False
        self._limits = {i: dict(SERVO_LIMITS.get(i, {"min": 0, "max": 180}))
                        for i in range(SERVO_COUNT)}
        cal = _load_cal()
        for k, v in (cal.get('limits') or {}).items():
            i = int(k)
            if 0 <= i < SERVO_COUNT:
                self._limits[i] = {'min': int(v.get('min', 0)),
                                   'max': int(v.get('max', 180))}
        if not _HAS_PCA:
            logger.warning('[Servos] adafruit_pca9685 not available')
            return
        self._init_pca9685()

    def _init_pca9685(self):
        try:
            self._i2c = busio.I2C(3, 2)
            self._pca = PCA9685(self._i2c, address=PCA9685_SERVO_ADDR)
            self._pca.frequency = PCA9685_SERVO_FREQ
            time.sleep(0.05)
            for i in range(SERVO_COUNT):
                try:
                    lim = self._limits[i]
                    actuation_range = max(int(lim['max']), 180)
                    self._servos[i] = adafruit_servo.Servo(
                        self._pca.channels[i],
                        min_pulse=SERVO_MIN_PULSE, max_pulse=SERVO_MAX_PULSE,
                        actuation_range=actuation_range)
                    init_angle = SERVO_INIT_ANGLES.get(i)
                    if init_angle is None:
                        init_angle = CRANE_ARM_OPEN if i == 6 else (
                            CRANE_GRIP_HIGH if i == 5 else SERVO_INIT_ANGLE)
                    init_angle = self._clamp(i, init_angle)
                    self._servos[i].angle = init_angle
                    self._angles[i] = init_angle
                    self._init_angles[i] = init_angle
                    time.sleep(0.03)
                except Exception as e:
                    logger.warning(f'[Servos] S{i} init failed: {e}')
            self._pwm_initialized = True
            logger.info(f'[Servos] PCA9685 OK ({sum(s is not None for s in self._servos)}/{SERVO_COUNT} channels)')
        except Exception as e:
            logger.error(f'[Servos] init failed: {e}')

    def _clamp(self, sid, angle):
        lim = self._limits.get(sid, {'min': 0, 'max': 180})
        return max(lim['min'], min(lim['max'], angle))

    def set_angle(self, sid, angle):
        if (not self._pwm_initialized or sid < 0
                or sid >= SERVO_COUNT or self._servos[sid] is None):
            return False
        angle = self._clamp(sid, angle)
        with self._lock:
            try:
                self._servos[sid].angle = angle
                self._angles[sid] = angle
                return True
            except Exception as e:
                logger.warning(f'[Servos] S{sid} set_angle({angle}) failed: {e}')
                return False

    def move_angle(self, sid, offset):
        if 0 <= sid < SERVO_COUNT:
            self.set_angle(sid, self._init_angles[sid] + offset)

    def smooth_move(self, sid, target, steps=10, delay=0.02):
        if not self._pwm_initialized or sid >= SERVO_COUNT:
            return
        target = self._clamp(sid, target)
        if abs(self._angles[sid] - target) < 1:
            return
        start = self._angles[sid]
        delta = (target - start) / steps
        for i in range(1, steps + 1):
            if not self.set_angle(sid, start + delta * i):
                break
            time.sleep(delay)

    def move_init(self):
        for i in range(SERVO_COUNT):
            if self._servos[i]:
                self.smooth_move(i, self._init_angles[i], steps=15, delay=0.02)

    def set_init_angle(self, sid, angle):
        if 0 <= sid < SERVO_COUNT:
            self._init_angles[sid] = self._clamp(sid, angle)

    def get_angle(self, sid):
        return self._angles[sid] if 0 <= sid < SERVO_COUNT else 0

    def get_limits(self, sid=None):
        if sid is not None:
            return self._limits.get(sid, {'min': 0, 'max': 180})
        return dict(self._limits)

    def set_limits(self, sid, min_angle, max_angle):
        if 0 <= sid < SERVO_COUNT:
            self._limits[sid] = {'min': int(min_angle), 'max': int(max_angle)}
            cal = _load_cal()
            cal['limits'] = {str(k): v for k, v in self._limits.items()}
            _save_cal(cal)
            return True
        return False

    def stop_all(self):
        pass

    def shutdown(self):
        self.stop_all()
        if self._pwm_initialized and self._pca:
            try:
                self._pca.deinit()
            except Exception:
                pass
        logger.info('[Servos] shutdown')
