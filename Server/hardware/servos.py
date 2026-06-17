import json, os, threading, time
from config import (
    PCA9685_SERVO_ADDR, PCA9685_SERVO_FREQ, I2C_BUS,
    SERVO_COUNT, SERVO_MIN_PULSE, SERVO_MAX_PULSE, SERVO_INIT_ANGLE,
    SERVO_INIT_ANGLES, SERVO_LIMITS,
    CRANE_ARM_OPEN, CRANE_GRIP_HIGH,
)
from Server.logger import logger

I2C_BUS_PINS = {0: (1, 0), 1: (3, 2)}

SERVO_CAL_FILE = os.path.join(os.path.dirname(__file__), '..', 'servo_cal.json')


def _load_servo_cal():
    try:
        if os.path.isfile(SERVO_CAL_FILE):
            with open(SERVO_CAL_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_servo_cal(data):
    try:
        with open(SERVO_CAL_FILE, "w") as f:
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
        self._servo_threads = [None] * SERVO_COUNT
        self._servo_flags = [threading.Event() for _ in range(SERVO_COUNT)]
        self._limits = {}
        for i in range(SERVO_COUNT):
            if i in SERVO_LIMITS:
                self._limits[i] = dict(SERVO_LIMITS[i])
            else:
                self._limits[i] = {"min": 0, "max": 180}
        self._load_limits_from_cal()
        self._init_pca9685()

    def _load_limits_from_cal(self):
        cal = _load_servo_cal()
        limits = cal.get("limits", {})
        for k, v in limits.items():
            idx = int(k)
            if 0 <= idx < SERVO_COUNT:
                self._limits[idx] = {"min": int(v.get("min", 0)), "max": int(v.get("max", 180))}

    def _save_limits_to_cal(self):
        cal = _load_servo_cal()
        cal["limits"] = {str(k): v for k, v in self._limits.items()}
        _save_servo_cal(cal)

    def _init_pca9685(self):
        try:
            import busio
            from adafruit_pca9685 import PCA9685
            from adafruit_motor import servo as adafruit_servo
            scl, sda = I2C_BUS_PINS.get(I2C_BUS, (3, 2))
            self._i2c = busio.I2C(scl, sda)
            self._pca = PCA9685(self._i2c, address=PCA9685_SERVO_ADDR)
            self._pca.frequency = PCA9685_SERVO_FREQ
            time.sleep(0.1)
            for i in range(SERVO_COUNT):
                try:
                    lim = self._limits.get(i, {"min": 0, "max": 180})
                    actuation_range = max(lim["max"], 180)
                    self._servos[i] = adafruit_servo.Servo(
                        self._pca.channels[i], min_pulse=SERVO_MIN_PULSE,
                        max_pulse=SERVO_MAX_PULSE, actuation_range=actuation_range)
                    init_angle = SERVO_INIT_ANGLES.get(i)
                    if init_angle is None:
                        if i == 6:
                            init_angle = CRANE_ARM_OPEN
                        elif i == 5:
                            init_angle = CRANE_GRIP_HIGH
                        else:
                            init_angle = SERVO_INIT_ANGLE
                    init_angle = self._clamp(i, init_angle)
                    self._servos[i].angle = init_angle
                    self._angles[i] = init_angle
                    self._init_angles[i] = init_angle
                    time.sleep(0.05)
                except Exception as e:
                    logger.warning(f"[Servos] S{i} init failed: {e}")
            self._pwm_initialized = True
            logger.info(f"[Servos] PCA9685 OK (bus={I2C_BUS}, scl={scl}, sda={sda}), {sum(s is not None for s in self._servos)}/{SERVO_COUNT} servos")
            logger.info(f"[Servos] Init angles: {self._init_angles}")
            logger.info(f"[Servos] Limits: {self._limits}")
        except Exception as e:
            logger.error(f"[Servos] Failed: {e}")

    def _clamp(self, sid, angle):
        lim = self._limits.get(sid, {"min": 0, "max": 180})
        return max(lim["min"], min(lim["max"], angle))

    def set_angle(self, sid, angle):
        if not self._pwm_initialized or sid >= SERVO_COUNT or self._servos[sid] is None:
            return
        angle = self._clamp(sid, angle)
        with self._lock:
            try:
                self._servos[sid].angle = angle
                self._angles[sid] = angle
            except Exception:
                pass

    def move_angle(self, sid, offset):
        if sid < SERVO_COUNT:
            self.set_angle(sid, self._init_angles[sid] + offset)

    def smooth_move(self, sid, target, steps=10, delay=0.02):
        if not self._pwm_initialized or sid >= SERVO_COUNT:
            return
        self._stop_thread(sid)
        target = self._clamp(sid, target)
        if abs(self._angles[sid] - target) < 1:
            return
        def _run():
            start = self._angles[sid]
            delta = (target - start) / steps
            for i in range(1, steps + 1):
                if not self._servo_flags[sid].is_set():
                    break
                self.set_angle(sid, start + delta * i)
                time.sleep(delay)
        self._servo_flags[sid].set()
        self._servo_threads[sid] = threading.Thread(target=_run, daemon=True)
        self._servo_threads[sid].start()

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
            return self._limits.get(sid, {"min": 0, "max": 180})
        return dict(self._limits)

    def set_limits(self, sid, min_angle, max_angle):
        if 0 <= sid < SERVO_COUNT:
            self._limits[sid] = {"min": int(min_angle), "max": int(max_angle)}
            self._save_limits_to_cal()
            logger.info(f"[Servos] S{sid} limits set: {min_angle}-{max_angle}")
            return True
        return False

    def _stop_thread(self, sid):
        self._servo_flags[sid].clear()
        if self._servo_threads[sid]:
            time.sleep(0.06)

    def stop_all(self):
        for f in self._servo_flags:
            f.clear()

    def shutdown(self):
        self.stop_all()
        if self._pwm_initialized:
            for s in self._servos:
                if s:
                    try:
                        s.angle = SERVO_INIT_ANGLE
                    except Exception:
                        pass
            try:
                self._pca.deinit()
            except Exception:
                pass
        logger.info("[Servos] Shutdown")
