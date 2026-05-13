"""Servo control — PCA9685, 6 channels, smooth movement."""

import threading, time
from Server.config import (
    PCA9685_SERVO_ADDR, PCA9685_SERVO_FREQ, I2C_BUS,
    SERVO_COUNT, SERVO_MIN_PULSE, SERVO_MAX_PULSE, SERVO_INIT_ANGLE, CRANE_ENABLED,
)

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
        self._init_pca9685()

    def _init_pca9685(self):
        try:
            import busio
            from adafruit_pca9685 import PCA9685
            from adafruit_motor import servo as adafruit_servo
            self._i2c = busio.I2C(3, 2)
            self._pca = PCA9685(self._i2c, address=PCA9685_SERVO_ADDR)
            self._pca.frequency = PCA9685_SERVO_FREQ
            time.sleep(0.1)
            for i in range(SERVO_COUNT):
                try:
                    self._servos[i] = adafruit_servo.Servo(
                        self._pca.channels[i], min_pulse=SERVO_MIN_PULSE,
                        max_pulse=SERVO_MAX_PULSE, actuation_range=180)
                    self._servos[i].angle = SERVO_INIT_ANGLE
                    time.sleep(0.05)
                except Exception as e:
                    print(f"[Servos] S{i} init failed: {e}")
            self._pwm_initialized = True
            print(f"[Servos] PCA9685 OK, {sum(s is not None for s in self._servos)}/{SERVO_COUNT} servos")
        except Exception as e:
            print(f"[Servos] Failed: {e}")

    def set_angle(self, sid, angle):
        if not self._pwm_initialized or sid >= SERVO_COUNT or self._servos[sid] is None:
            return
        angle = max(0, min(180, angle))
        with self._lock:
            try:
                self._servos[sid].angle = angle
                self._angles[sid] = angle
            except Exception:
                pass

    def move_angle(self, sid, offset):
        if sid < SERVO_COUNT:
            self.set_angle(sid, self._init_angles[sid] + offset)

    def single_servo(self, sid, direction=1, speed=3):
        if not self._pwm_initialized or sid >= SERVO_COUNT:
            return
        self._stop_thread(sid)
        flag = self._servo_flags[sid]
        flag.set()
        def _run():
            cur = self._angles[sid]
            while flag.is_set():
                cur += direction * speed
                if cur >= 180 or cur <= 0:
                    cur = max(0, min(180, cur))
                    flag.clear()
                    break
                self.set_angle(sid, cur)
                time.sleep(0.05)
        self._servo_threads[sid] = threading.Thread(target=_run, daemon=True)
        self._servo_threads[sid].start()

    def smooth_move(self, sid, target, steps=10, delay=0.02):
        if not self._pwm_initialized or sid >= SERVO_COUNT:
            return
        self._stop_thread(sid)
        target = max(0, min(180, target))
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
            self._init_angles[sid] = max(0, min(180, angle))

    def get_angle(self, sid):
        return self._angles[sid] if 0 <= sid < SERVO_COUNT else 0

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
        print("[Servos] Shutdown")
