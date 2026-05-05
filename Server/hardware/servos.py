"""Servo control — PCA9685 with single instance, smooth movement."""

import threading
import time

from Server.config import (
    PCA9685_SERVO_ADDR, PCA9685_SERVO_FREQ, I2C_BUS,
    SERVO_COUNT, SERVO_MIN_PULSE, SERVO_MAX_PULSE, SERVO_INIT_ANGLE,
)


class ServoController:
    """PCA9685 servo controller.

    Channels:
    - 0: Steering
    - 1: Camera pan
    - 2: Camera tilt
    """

    def __init__(self):
        self._pca = None
        self._servos = [None] * SERVO_COUNT
        self._angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._init_angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._lock = threading.Lock()
        self._pwm_initialized = False

        self._servo_threads = [None] * SERVO_COUNT
        self._servo_flags = [threading.Event() for _ in range(SERVO_COUNT)]
        for flag in self._servo_flags:
            flag.clear()

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
                        self._pca.channels[i],
                        min_pulse=SERVO_MIN_PULSE,
                        max_pulse=SERVO_MAX_PULSE,
                        actuation_range=180,
                    )
                    self._servos[i].angle = SERVO_INIT_ANGLE
                    time.sleep(0.05)
                except Exception as e:
                    print(f"[Servos] Warning: servo {i} init failed: {e}")

            self._pwm_initialized = True
            print(f"[Servos] PCA9685 at 0x{PCA9685_SERVO_ADDR:02X}, "
                  f"{SERVO_COUNT} servos @ {PCA9685_SERVO_FREQ}Hz")

        except Exception as e:
            print(f"[Servos] Failed to initialize PCA9685: {e}")

    def set_angle(self, servo_id, angle):
        if not self._pwm_initialized or servo_id >= SERVO_COUNT:
            return
        angle = max(0, min(180, angle))
        with self._lock:
            try:
                self._servos[servo_id].angle = angle
                self._angles[servo_id] = angle
            except Exception as e:
                print(f"[Servos] Error setting servo {servo_id}: {e}")

    def move_angle(self, servo_id, offset):
        """Move servo by offset from its init position."""
        if servo_id >= SERVO_COUNT:
            return
        target = self._init_angles[servo_id] + offset
        self.set_angle(servo_id, target)

    def single_servo(self, servo_id, direction=1, speed=3):
        if not self._pwm_initialized or servo_id >= SERVO_COUNT:
            return
        self._stop_servo_thread(servo_id)
        flag = self._servo_flags[servo_id]
        flag.set()

        def _wiggle():
            current = self._angles[servo_id]
            while flag.is_set():
                current += direction * speed
                if current >= 180 or current <= 0:
                    current = max(0, min(180, current))
                    flag.clear()
                    break
                self.set_angle(servo_id, current)
                time.sleep(0.05)

        t = threading.Thread(target=_wiggle, daemon=True)
        self._servo_threads[servo_id] = t
        t.start()

    def smooth_move(self, servo_id, target_angle, steps=10, step_delay=0.02):
        if not self._pwm_initialized or servo_id >= SERVO_COUNT:
            return
        self._stop_servo_thread(servo_id)
        target_angle = max(0, min(180, target_angle))
        if abs(self._angles[servo_id] - target_angle) < 1:
            return

        def _smooth():
            start = self._angles[servo_id]
            delta = (target_angle - start) / steps
            for i in range(1, steps + 1):
                if not self._servo_flags[servo_id].is_set():
                    break
                self.set_angle(servo_id, start + delta * i)
                time.sleep(step_delay)

        self._servo_flags[servo_id].set()
        t = threading.Thread(target=_smooth, daemon=True)
        self._servo_threads[servo_id] = t
        t.start()

    def move_init(self):
        for i in range(SERVO_COUNT):
            self.smooth_move(i, self._init_angles[i], steps=15, step_delay=0.02)
        print("[Servos] All servos at init positions")

    def set_init_angle(self, servo_id, angle):
        if 0 <= servo_id < SERVO_COUNT:
            self._init_angles[servo_id] = max(0, min(180, angle))

    def get_angle(self, servo_id):
        if 0 <= servo_id < SERVO_COUNT:
            return self._angles[servo_id]
        return 0

    def _stop_servo_thread(self, servo_id):
        self._servo_flags[servo_id].clear()
        if self._servo_threads[servo_id] is not None:
            time.sleep(0.06)

    def stop_all(self):
        for i in range(SERVO_COUNT):
            self._servo_flags[i].clear()

    def shutdown(self):
        self.stop_all()
        if self._pwm_initialized:
            for i in range(SERVO_COUNT):
                try:
                    self._servos[i].angle = SERVO_INIT_ANGLE
                except Exception:
                    pass
            try:
                self._pca.deinit()
            except Exception:
                pass
        print("[Servos] Shutdown")
