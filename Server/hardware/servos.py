"""
Servo control module for PiCar Pro.
CRITICAL OPTIMIZATION: Single PCA9685 instance, reused for all servo operations.
v1 created a new I2C bus + PCA9685 object on EVERY set_angle() call!

Optimizations:
- Single PCA9685 instance (initialized once, reused forever)
- Proper threading with smooth movement
- No self-modifying code (v1 replace_num wrote to RPIservo.py source)
- Clean servo angle calibration stored in config
"""

import threading
import time

from Server.config import (
    PCA9685_SERVO_ADDR, PCA9685_SERVO_FREQ, I2C_BUS,
    SERVO_COUNT, SERVO_MIN_PULSE, SERVO_MAX_PULSE, SERVO_INIT_ANGLE,
)


class ServoController:
    """
    Servo controller with single PCA9685 instance.
    
    Hardware configuration (user-specific):
    - Channel 0: Front wheel STEERING (left-right rotation)
    - Channel 1: Camera PAN
    - Channel 2: Camera TILT
    - Channels 3-7: DISABLED (crane not connected)
    
    Safe initialization: initializes servos one by one with delays
    to prevent I2C bus overload that could cause Pi reboots.
    """

    def __init__(self):
        self._pca = None
        self._servos = [None] * SERVO_COUNT
        self._angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._init_angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._lock = threading.Lock()
        self._pwm_initialized = False

        # Servo movement threads
        self._servo_threads = [None] * SERVO_COUNT
        self._servo_flags = [threading.Event() for _ in range(SERVO_COUNT)]
        for flag in self._servo_flags:
            flag.clear()

        self._init_pca9685()

    def _init_pca9685(self):
        """Initialize PCA9685 ONCE (not per call like v1!).
        
        Safe init: one servo at a time with 50ms delay to prevent
        I2C bus overload on Pi 3B+ that could cause reboots.
        """
        try:
            import busio
            from adafruit_pca9685 import PCA9685
            from adafruit_motor import servo as adafruit_servo

            # Create I2C bus and PCA9685 once
            self._i2c = busio.I2C(3, 2)  # SCL=GPIO3, SDA=GPIO2
            self._pca = PCA9685(self._i2c, address=PCA9685_SERVO_ADDR)
            self._pca.frequency = PCA9685_SERVO_FREQ
            time.sleep(0.1)  # Settle after frequency change

            # Create only the servos we need (SERVO_COUNT from config)
            for i in range(SERVO_COUNT):
                try:
                    self._servos[i] = adafruit_servo.Servo(
                        self._pca.channels[i],
                        min_pulse=SERVO_MIN_PULSE,
                        max_pulse=SERVO_MAX_PULSE,
                        actuation_range=180,
                    )
                    # Set initial position with safe ramp
                    self._servos[i].angle = SERVO_INIT_ANGLE
                    time.sleep(0.05)  # 50ms between servo inits to prevent I2C overload
                except Exception as e:
                    print(f"[Servos] Warning: failed to init servo {i}: {e}")

            self._pwm_initialized = True
            print(f"[Servos] PCA9685 initialized at 0x{PCA9685_SERVO_ADDR:02X}, "
                  f"{SERVO_COUNT} servos at {PCA9685_SERVO_FREQ}Hz (crane disabled)")

        except Exception as e:
            print(f"[Servos] Failed to initialize PCA9685: {e}")
            self._pwm_initialized = False

    def set_angle(self, servo_id, angle):
        """
        Set servo angle directly.
        
        Args:
            servo_id: 0-7 servo channel
            angle: 0-180 degrees
        """
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
        """
        Move servo by offset from its init position.
        
        Args:
            servo_id: 0-7 servo channel
            offset: degrees offset from init position (-90 to +90)
        """
        target = self._init_angles[servo_id] + offset
        self.set_angle(servo_id, target)

    def single_servo(self, servo_id, direction=1, speed=3):
        """
        Continuously move a servo in one direction (for scanning/wiggling).
        
        Args:
            servo_id: 0-7 servo channel
            direction: 1 for increase, -1 for decrease
            speed: movement speed (1-10)
        """
        if not self._pwm_initialized or servo_id >= SERVO_COUNT:
            return

        # Stop existing thread for this servo
        self._stop_servo_thread(servo_id)

        flag = self._servo_flags[servo_id]
        flag.set()

        def _wiggle():
            current = self._angles[servo_id]
            while flag.is_set():
                current += direction * speed
                if current >= 180:
                    current = 180
                    flag.clear()
                    break
                elif current <= 0:
                    current = 0
                    flag.clear()
                    break
                self.set_angle(servo_id, current)
                time.sleep(0.05)

        t = threading.Thread(target=_wiggle, daemon=True)
        self._servo_threads[servo_id] = t
        t.start()

    def smooth_move(self, servo_id, target_angle, steps=10, step_delay=0.02):
        """
        Smoothly move a servo to a target angle.
        
        Args:
            servo_id: 0-7 servo channel
            target_angle: 0-180 target
            steps: number of interpolation steps
            step_delay: delay between steps in seconds
        """
        if not self._pwm_initialized or servo_id >= SERVO_COUNT:
            return

        self._stop_servo_thread(servo_id)

        current = self._angles[servo_id]
        target_angle = max(0, min(180, target_angle))

        if abs(current - target_angle) < 1:
            return

        def _smooth():
            start = self._angles[servo_id]
            delta = (target_angle - start) / steps
            for i in range(1, steps + 1):
                if not self._servo_flags[servo_id].is_set():
                    break
                angle = start + delta * i
                self.set_angle(servo_id, angle)
                time.sleep(step_delay)

        self._servo_flags[servo_id].set()
        t = threading.Thread(target=_smooth, daemon=True)
        self._servo_threads[servo_id] = t
        t.start()

    def move_init(self):
        """Move all servos to their init positions smoothly."""
        for i in range(SERVO_COUNT):
            self.smooth_move(i, self._init_angles[i], steps=15, step_delay=0.02)
        print("[Servos] All servos moved to init positions")

    def set_init_angle(self, servo_id, angle):
        """Set and save the init angle for a servo."""
        if 0 <= servo_id < SERVO_COUNT:
            self._init_angles[servo_id] = max(0, min(180, angle))

    def get_angle(self, servo_id):
        """Get current angle of a servo."""
        if 0 <= servo_id < SERVO_COUNT:
            return self._angles[servo_id]
        return 0

    def _stop_servo_thread(self, servo_id):
        """Stop the movement thread for a specific servo."""
        self._servo_flags[servo_id].clear()
        if self._servo_threads[servo_id] is not None:
            # Give thread time to stop
            time.sleep(0.06)

    def stop_all(self):
        """Stop all servo movement threads."""
        for i in range(SERVO_COUNT):
            self._servo_flags[i].clear()

    def shutdown(self):
        """Clean shutdown - stop all threads, move to init, release PCA9685."""
        self.stop_all()
        # Move servos to safe position
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
        print("[Servos] Shutdown complete")
