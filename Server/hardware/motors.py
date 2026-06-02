"""Motor control — L298N GPIO via gpiozero."""

from Server.config import (
    MOTOR_A_EN, MOTOR_A_IN1, MOTOR_A_IN2,
    MOTOR_B_EN, MOTOR_B_IN1, MOTOR_B_IN2,
    DEFAULT_SPEED, TURN_RADIUS_MIN, TURN_RADIUS_MAX,
)
from Server.logger import logger

class MotorController:
    def __init__(self):
        self._speed = DEFAULT_SPEED
        self._initialized = False
        self._init_motors()

    def _init_motors(self):
        try:
            from gpiozero import Motor
            self._motor_a = Motor(forward=MOTOR_A_IN1, backward=MOTOR_A_IN2, enable=MOTOR_A_EN, pwm=True)
            self._motor_b = Motor(forward=MOTOR_B_IN1, backward=MOTOR_B_IN2, enable=MOTOR_B_EN, pwm=True)
            self._initialized = True
            logger.info("[Motors] OK")
        except Exception as e:
            logger.error(f"[Motors] Failed: {e}")

    def move(self, speed=None, direction='forward', turn='no', radius=0.5):
        if not self._initialized:
            return
        speed = speed if speed is not None else self._speed
        speed = max(0, min(100, speed))
        self._speed = speed
        radius = max(TURN_RADIUS_MIN, min(TURN_RADIUS_MAX, radius))
        s = speed / 100.0
        left = s * (1 - radius) if turn == 'left' else s
        right = s * (1 - radius) if turn == 'right' else s
        if direction == 'forward':
            self._motor_a.forward(right)
            self._motor_b.forward(left)
        elif direction == 'backward':
            self._motor_a.backward(right)
            self._motor_b.backward(left)
        else:
            self.stop()

    def stop(self):
        if self._initialized:
            self._motor_a.stop()
            self._motor_b.stop()

    def video_tracking_move(self, offset, max_speed=50):
        if not self._initialized:
            return
        n = max(-1.0, min(1.0, offset / 320))
        s = max(0.1, max_speed / 100.0 * (1 - abs(n) * 0.5))
        self._motor_a.forward(max(0, min(1, s * (1 - n * 0.5))))
        self._motor_b.forward(max(0, min(1, s * (1 + n * 0.5))))

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, v):
        self._speed = max(0, min(100, v))

    def shutdown(self):
        self.stop()
