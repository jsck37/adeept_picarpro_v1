import threading
from Server.logger import logger
from config import (
    MOTOR_A_EN, MOTOR_A_IN1, MOTOR_A_IN2,
    MOTOR_B_EN, MOTOR_B_IN1, MOTOR_B_IN2,
    MOTOR_PWM_FREQ, DEFAULT_SPEED,
    TURN_RADIUS_MIN, TURN_RADIUS_MAX,
)

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None


class MotorController:
    def __init__(self):
        self._speed = DEFAULT_SPEED
        self._lock = threading.Lock()
        self._pwm_a = None
        self._pwm_b = None
        self._initialized = False
        if not _HAS_GPIO:
            logger.warning('[Motors] RPi.GPIO not available')
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in (MOTOR_A_EN, MOTOR_A_IN1, MOTOR_A_IN2,
                        MOTOR_B_EN, MOTOR_B_IN1, MOTOR_B_IN2):
                GPIO.setup(pin, GPIO.OUT)
            self._pwm_a = GPIO.PWM(MOTOR_A_EN, MOTOR_PWM_FREQ)
            self._pwm_b = GPIO.PWM(MOTOR_B_EN, MOTOR_PWM_FREQ)
            self._pwm_a.start(0)
            self._pwm_b.start(0)
            self._initialized = True
            logger.info(f'[Motors] OK — PWM @ {MOTOR_PWM_FREQ} Hz')
        except Exception as e:
            logger.error(f'[Motors] init failed: {e}')

    def _set_side(self, in1, in2, pwm, forward, power):
        if not _HAS_GPIO or not self._initialized:
            return
        if forward:
            GPIO.output(in1, GPIO.HIGH)
            GPIO.output(in2, GPIO.LOW)
        else:
            GPIO.output(in1, GPIO.LOW)
            GPIO.output(in2, GPIO.HIGH)
        pwm.ChangeDutyCycle(max(0.0, min(100.0, power * 100.0)))

    def move(self, speed=None, direction='forward', turn='no', radius=0.5):
        if not self._initialized:
            return
        speed = self._speed if speed is None else speed
        speed = max(0, min(100, speed))
        self._speed = speed
        radius = max(TURN_RADIUS_MIN, min(TURN_RADIUS_MAX, radius))
        s = speed / 100.0
        if turn == 'no':
            left = right = s
        elif turn == 'left':
            left = max(s * 0.3, s * (1 - radius))
            right = s
        else:
            left = s
            right = max(s * 0.3, s * (1 - radius))
        forward = (direction == 'forward')
        backward = (direction == 'backward')
        if not (forward or backward):
            self.stop()
            return
        with self._lock:
            self._set_side(MOTOR_A_IN1, MOTOR_A_IN2, self._pwm_a, forward, right)
            self._set_side(MOTOR_B_IN1, MOTOR_B_IN2, self._pwm_b, forward, left)

    def stop(self):
        if not self._initialized:
            return
        with self._lock:
            self._pwm_a.ChangeDutyCycle(0)
            self._pwm_b.ChangeDutyCycle(0)
            GPIO.output(MOTOR_A_IN1, GPIO.LOW)
            GPIO.output(MOTOR_A_IN2, GPIO.LOW)
            GPIO.output(MOTOR_B_IN1, GPIO.LOW)
            GPIO.output(MOTOR_B_IN2, GPIO.LOW)

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, v):
        self._speed = max(0, min(100, v))

    def shutdown(self):
        self.stop()
        for pwm in (self._pwm_a, self._pwm_b):
            if pwm:
                try:
                    pwm.stop()
                except Exception:
                    pass
        logger.info('[Motors] shutdown')
