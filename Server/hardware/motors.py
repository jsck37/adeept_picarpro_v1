"""Motor control — L298N GPIO via gpiozero."""

from Server.config import (
    MOTOR_A_EN, MOTOR_A_IN1, MOTOR_A_IN2,
    MOTOR_B_EN, MOTOR_B_IN1, MOTOR_B_IN2,
    DEFAULT_SPEED, TURN_RADIUS_MIN, TURN_RADIUS_MAX,
)


class MotorController:

    def __init__(self):
        self._speed = DEFAULT_SPEED
        self._direction = 0
        self._turn = "no"
        self._radius = 0.5
        self._initialized = False
        self._init_motors()

    def _init_motors(self):
        try:
            from gpiozero import Motor
            self._motor_a = Motor(
                forward=MOTOR_A_IN1, backward=MOTOR_A_IN2,
                enable=MOTOR_A_EN, pwm=True
            )
            self._motor_b = Motor(
                forward=MOTOR_B_IN1, backward=MOTOR_B_IN2,
                enable=MOTOR_B_EN, pwm=True
            )
            self._initialized = True
            print("[Motors] GPIO motors initialized")
        except Exception as e:
            print(f"[Motors] Init failed: {e}")

    def move(self, speed=None, direction='forward', turn='no', radius=0.5):
        if not self._initialized:
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

        self._move(speed_norm, direction, turn, self._radius)

    def _move(self, speed, direction, turn, radius):
        left_speed = speed
        right_speed = speed
        if turn == 'left':
            left_speed = speed * (1 - radius)
        elif turn == 'right':
            right_speed = speed * (1 - radius)

        if direction == 'forward':
            self._motor_a.forward(right_speed)
            self._motor_b.forward(left_speed)
        elif direction == 'backward':
            self._motor_a.backward(right_speed)
            self._motor_b.backward(left_speed)

    def stop(self):
        self._direction = 0
        if self._initialized:
            self._motor_a.stop()
            self._motor_b.stop()

    def video_tracking_move(self, offset, max_speed=50):
        if not self._initialized:
            return
        frame_width = 640
        normalized = max(-1.0, min(1.0, offset / (frame_width / 2)))
        speed = max(0.1, max_speed / 100.0 * (1 - abs(normalized) * 0.5))
        left_speed = max(0, min(1, speed * (1 + normalized * 0.5)))
        right_speed = max(0, min(1, speed * (1 - normalized * 0.5)))
        self._motor_a.forward(right_speed)
        self._motor_b.forward(left_speed)

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, value):
        self._speed = max(0, min(100, value))

    def shutdown(self):
        self.stop()
