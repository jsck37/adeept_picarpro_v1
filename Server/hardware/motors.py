"""Motor control — L298N GPIO via gpiozero.

Supports:
  - Normal driving (forward/backward/turn)
  - Drift mode: over-steer front wheels + aggressive rear power
    with reduced inner wheel to induce oversteer on RWD car.
"""

import threading
from Server.config import (
    MOTOR_A_EN, MOTOR_A_IN1, MOTOR_A_IN2,
    MOTOR_B_EN, MOTOR_B_IN1, MOTOR_B_IN2,
    DEFAULT_SPEED, TURN_RADIUS_MIN, TURN_RADIUS_MAX,
    DRIFT_ENABLED, DRIFT_POWER_MULT, DRIFT_INNER_BRAKE,
)
from Server.logger import logger

class MotorController:
    def __init__(self):
        self._speed = DEFAULT_SPEED
        self._initialized = False
        self._lock = threading.Lock()
        self._drift_mode = False
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
        """Move the robot.

        Parameters
        ----------
        speed : int, optional
            Motor speed 0-100. Defaults to stored speed.
        direction : str
            'forward' or 'backward'
        turn : str
            'no', 'left', or 'right'
        radius : float
            Turn radius factor (0.0 = pivot, 1.0 = gentle curve)
        """
        if not self._initialized:
            return
        speed = speed if speed is not None else self._speed
        speed = max(0, min(100, speed))
        self._speed = speed
        radius = max(TURN_RADIUS_MIN, min(TURN_RADIUS_MAX, radius))
        s = speed / 100.0

        if self._drift_mode and DRIFT_ENABLED and turn != 'no':
            # ── Drift mode ──
            # Apply power multiplier for aggressive rear drive
            drift_s = min(1.0, s * DRIFT_POWER_MULT)
            # Inner wheel gets reduced power to break traction
            if turn == 'left':
                left = drift_s * DRIFT_INNER_BRAKE   # inner wheel brakes
                right = drift_s                       # outer wheel full power
            else:  # right
                left = drift_s
                right = drift_s * DRIFT_INNER_BRAKE
            if direction == 'forward':
                self._motor_a.forward(right)
                self._motor_b.forward(left)
            elif direction == 'backward':
                self._motor_a.backward(right)
                self._motor_b.backward(left)
            else:
                self.stop()
        else:
            # ── Normal mode ──
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

    def set_drift_mode(self, enabled):
        """Enable or disable drift mode."""
        self._drift_mode = enabled and DRIFT_ENABLED
        logger.info(f"[Motors] Drift mode: {'ON' if self._drift_mode else 'OFF'}")

    @property
    def drift_mode(self):
        return self._drift_mode

    def stop(self):
        if self._initialized:
            self._motor_a.stop()
            self._motor_b.stop()

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, v):
        self._speed = max(0, min(100, v))

    def shutdown(self):
        self.stop()
