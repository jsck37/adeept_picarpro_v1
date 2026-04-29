"""
Motor control module for PiCar Pro v1.
Compatible with v1 hardware (direct GPIO via gpiozero).
Supports v2 hardware (PCA9685) via HARDWARE_VERSION config.

Optimizations over v1:
- No global state - clean MotorController class
- Proper initialization and shutdown
- Speed clamping to valid range
- Correct video_Tracking_Move (v1 had a bug: move.move() AttributeError)
"""

import time
from Server.config import (
    HARDWARE_VERSION,
    MOTOR_A_EN, MOTOR_A_IN1, MOTOR_A_IN2,
    MOTOR_B_EN, MOTOR_B_IN1, MOTOR_B_IN2,
    PCA9685_MOTOR_ADDR, I2C_BUS,
    DEFAULT_SPEED, TURN_RADIUS_MIN, TURN_RADIUS_MAX,
)


class MotorController:
    """
    DC motor controller.
    
    v1 hardware: 2 motors via gpiozero (differential drive)
    v2 hardware: 4 motors via PCA9685
    """

    def __init__(self):
        self._speed = DEFAULT_SPEED
        self._direction = 0  # -1=backward, 0=stop, 1=forward
        self._turn = "no"
        self._radius = 0.5

        if HARDWARE_VERSION == 1:
            self._init_gpio_motors()
        else:
            self._init_pca9685_motors()

    def _init_gpio_motors(self):
        """Initialize v1 GPIO-based motor control."""
        try:
            from gpiozero import Motor, OutputDevice

            self._motor_a = Motor(
                forward=MOTOR_A_IN1, backward=MOTOR_A_IN2,
                enable=MOTOR_A_EN, pwm=True
            )
            self._motor_b = Motor(
                forward=MOTOR_B_IN1, backward=MOTOR_B_IN2,
                enable=MOTOR_B_EN, pwm=True
            )
            self._motor_type = "gpio"
            print("[Motors] Initialized GPIO motors (v1 hardware)")
        except Exception as e:
            print(f"[Motors] Failed to initialize GPIO motors: {e}")
            self._motor_type = None

    def _init_pca9685_motors(self):
        """Initialize v2 PCA9685-based motor control."""
        try:
            import busio
            from adafruit_pca9685 import PCA9685
            from adafruit_motor import motor as adafruit_motor

            i2c = busio.I2C(3, 2)  # SCL=GPIO3, SDA=GPIO2
            pca = PCA9685(i2c, address=PCA9685_MOTOR_ADDR)
            pca.frequency = 1000

            from Server.config import (
                MOTOR_M1_IN1, MOTOR_M1_IN2,
                MOTOR_M2_IN1, MOTOR_M2_IN2,
                MOTOR_M3_IN1, MOTOR_M3_IN2,
                MOTOR_M4_IN1, MOTOR_M4_IN2,
            )

            self._m1 = adafruit_motor.DCMotor(pca.channels[MOTOR_M1_IN1], pca.channels[MOTOR_M1_IN2])
            self._m2 = adafruit_motor.DCMotor(pca.channels[MOTOR_M2_IN1], pca.channels[MOTOR_M2_IN2])
            self._m3 = adafruit_motor.DCMotor(pca.channels[MOTOR_M3_IN1], pca.channels[MOTOR_M3_IN2])
            self._m4 = adafruit_motor.DCMotor(pca.channels[MOTOR_M4_IN1], pca.channels[MOTOR_M4_IN2])

            for m in [self._m1, self._m2, self._m3, self._m4]:
                m.decay_mode = adafruit_motor.SLOW_DECAY

            self._pca = pca
            self._motor_type = "pca9685"
            print("[Motors] Initialized PCA9685 motors (v2 hardware)")
        except Exception as e:
            print(f"[Motors] Failed to initialize PCA9685 motors: {e}")
            self._motor_type = None

    def move(self, speed=None, direction='forward', turn='no', radius=0.5):
        """
        Move the robot.
        
        Args:
            speed: 0-100 motor speed
            direction: 'forward' or 'backward'
            turn: 'left', 'right', or 'no'
            radius: turning radius (0.0-1.0, higher = wider turn)
        """
        if self._motor_type is None:
            return

        speed = speed if speed is not None else self._speed
        speed = max(0, min(100, speed))
        self._speed = speed
        self._turn = turn
        self._radius = max(TURN_RADIUS_MIN, min(TURN_RADIUS_MAX, radius))

        speed_norm = speed / 100.0

        if direction == 'forward':
            self._direction = 1
        elif direction == 'backward':
            self._direction = -1
        else:
            self._direction = 0
            self.stop()
            return

        if self._motor_type == "gpio":
            self._move_gpio(speed_norm, direction, turn, self._radius)
        elif self._motor_type == "pca9685":
            self._move_pca9685(speed_norm, direction, turn, self._radius)

    def _move_gpio(self, speed, direction, turn, radius):
        """Move using GPIO motors (v1).
        
        Motor mapping: motorA = RIGHT, motorB = LEFT
        Differential steering: turning left slows LEFT motor,
        turning right slows RIGHT motor.
        """
        left_speed = speed
        right_speed = speed

        # Differential steering
        if turn == 'left':
            left_speed = speed * (1 - radius)
        elif turn == 'right':
            right_speed = speed * (1 - radius)

        if direction == 'forward':
            self._motor_a.forward(right_speed)   # motorA = RIGHT
            self._motor_b.forward(left_speed)    # motorB = LEFT
        elif direction == 'backward':
            self._motor_a.backward(right_speed)  # motorA = RIGHT
            self._motor_b.backward(left_speed)   # motorB = LEFT

    def _move_pca9685(self, speed, direction, turn, radius):
        """Move using PCA9685 motors (v2)."""
        left_speed = speed
        right_speed = speed

        if turn == 'left':
            left_speed = speed * (1 - radius)
        elif turn == 'right':
            right_speed = speed * (1 - radius)

        # M1=left, M2=right (4 motors, 2 per side)
        if direction == 'forward':
            self._m1.throttle = left_speed
            self._m2.throttle = right_speed
            self._m3.throttle = left_speed
            self._m4.throttle = right_speed
        elif direction == 'backward':
            self._m1.throttle = -left_speed
            self._m2.throttle = -right_speed
            self._m3.throttle = -left_speed
            self._m4.throttle = -right_speed

    def stop(self):
        """Stop all motors."""
        self._direction = 0

        if self._motor_type == "gpio":
            self._motor_a.stop()
            self._motor_b.stop()
        elif self._motor_type == "pca9685":
            for m in [self._m1, self._m2, self._m3, self._m4]:
                m.throttle = 0

    def video_tracking_move(self, offset, max_speed=50):
        """
        Move based on CV tracking offset.
        
        Args:
            offset: pixel offset from center (negative=left, positive=right)
            max_speed: maximum motor speed
        """
        if self._motor_type is None:
            return

        # Normalize offset to [-1, 1]
        frame_width = 640  # default
        normalized = max(-1.0, min(1.0, offset / (frame_width / 2)))

        # Differential speed based on offset
        speed = max(0.1, max_speed / 100.0 * (1 - abs(normalized) * 0.5))
        left_speed = speed * (1 + normalized * 0.5)
        right_speed = speed * (1 - normalized * 0.5)

        left_speed = max(0, min(1, left_speed))
        right_speed = max(0, min(1, right_speed))

        if self._motor_type == "gpio":
            self._motor_a.forward(right_speed)   # motorA = RIGHT
            self._motor_b.forward(left_speed)    # motorB = LEFT
        elif self._motor_type == "pca9685":
            self._m1.throttle = left_speed
            self._m2.throttle = right_speed
            self._m3.throttle = left_speed
            self._m4.throttle = right_speed

    @property
    def speed(self):
        """Current speed setting."""
        return self._speed

    @speed.setter
    def speed(self, value):
        """Set speed (0-100)."""
        self._speed = max(0, min(100, value))

    def shutdown(self):
        """Stop motors and release resources."""
        self.stop()
        if self._motor_type == "pca9685" and hasattr(self, '_pca'):
            try:
                self._pca.deinit()
            except Exception:
                pass
