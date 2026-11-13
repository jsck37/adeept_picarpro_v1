import threading
from picarpro.logger import logger
from config import (
    MOTOR_A_EN, MOTOR_A_IN1, MOTOR_A_IN2,
    MOTOR_B_EN, MOTOR_B_IN1, MOTOR_B_IN2,
    MOTOR_PWM_FREQ, DEFAULT_SPEED,
    TURN_RADIUS_MIN, TURN_RADIUS_MAX,
    MOTOR_MAX_SPEED_DEFAULT,
    MOTOR_INVERT_RIGHT, MOTOR_INVERT_LEFT,
)
from picarpro.hardware.pwm import make_pwm

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None


class MotorController:
    """Два борта L298N: A = правый, B = левый (семантика оригинальной
    прошивки Adeept: motor_right = Motor A, motor_left = Motor B).

    Все режимы движения (move / move_differential / spin) сходятся в одну
    точку ``_apply`` — раньше логика выставления пинов и скважности была
    продублирована в каждом методе.
    """

    def __init__(self):
        self._speed = DEFAULT_SPEED
        self._max_speed = MOTOR_MAX_SPEED_DEFAULT
        self._moving = False
        self._lock = threading.Lock()
        self._pwm_a = None
        self._pwm_b = None
        self.pwm_backend = None
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
            self._pwm_a, backend = make_pwm(GPIO, MOTOR_A_EN, MOTOR_PWM_FREQ)
            self._pwm_b, _ = make_pwm(GPIO, MOTOR_B_EN, MOTOR_PWM_FREQ)
            self._pwm_a.start(0)
            self._pwm_b.start(0)
            self.pwm_backend = backend
            self._initialized = True
            logger.info(f'[Motors] OK — PWM @ {MOTOR_PWM_FREQ} Hz ({backend})')
        except Exception as e:
            logger.error(f'[Motors] init failed: {e}')

    # ---- низкий уровень ------------------------------------------------
    @staticmethod
    def _set_side(in1, in2, pwm, power, invert=False):
        """power: знаковая доля мощности -1..1 (знак = направление).

        IN1 = прямой ход, IN2 = назад (пины сверены с оригиналом Adeept,
        v1.6.0). invert — для сборок с перепутанными проводами мотора.
        """
        forward = (power >= 0) != bool(invert)
        GPIO.output(in1, GPIO.HIGH if forward else GPIO.LOW)
        GPIO.output(in2, GPIO.LOW if forward else GPIO.HIGH)
        pwm.ChangeDutyCycle(max(0.0, min(100.0, abs(power) * 100.0)))

    def _apply(self, right, left):
        """Единая точка записи в железо. right/left — знаковые доли -1..1."""
        with self._lock:
            self._set_side(MOTOR_A_IN1, MOTOR_A_IN2, self._pwm_a, right,
                           MOTOR_INVERT_RIGHT)
            self._set_side(MOTOR_B_IN1, MOTOR_B_IN2, self._pwm_b, left,
                           MOTOR_INVERT_LEFT)
            self._moving = bool(right or left)

    def _clamp_speed(self, speed):
        return max(0, min(self._max_speed, min(100, speed)))

    # ---- публичный API --------------------------------------------------
    def move_differential(self, speed, direction, left_frac, right_frac):
        """Движение с заданными долями мощности бортов (0..1).

        Единая точка дифференциального привода — используется и из move(),
        и из геймпада DS4.
        """
        if not self._initialized:
            return
        if direction not in ('forward', 'backward'):
            self.stop()
            return
        speed = self._clamp_speed(speed)
        self._speed = speed
        sign = 1.0 if direction == 'forward' else -1.0
        s = speed / 100.0
        left = max(0.0, min(1.0, left_frac)) * s * sign
        right = max(0.0, min(1.0, right_frac)) * s * sign
        self._apply(right, left)

    def move(self, speed=None, direction='forward', turn='no', radius=0.5):
        if not self._initialized:
            return
        speed = self._speed if speed is None else speed
        radius = max(TURN_RADIUS_MIN, min(TURN_RADIUS_MAX, radius))
        inner = max(0.3, 1.0 - radius)
        if turn == 'no':
            left = right = 1.0
        elif turn == 'left':
            left, right = inner, 1.0
        else:
            left, right = 1.0, inner
        self.move_differential(speed, direction, left, right)

    def spin(self, speed=None, turn='left'):
        """Разворот на месте: борта крутятся в противоположные стороны.

        turn='left' — против часовой (правый борт вперёд, левый назад).
        Раньше самый крутой поворот оставлял внутреннему борту >=30 %
        мощности, и робот описывал дугу, а не разворачивался.
        """
        if not self._initialized:
            return
        speed = self._speed if speed is None else speed
        speed = self._clamp_speed(speed)
        self._speed = speed
        s = speed / 100.0
        if turn == 'left':
            self._apply(s, -s)
        else:
            self._apply(-s, s)

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
            self._moving = False

    @property
    def is_moving(self):
        return self._moving

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, v):
        self._speed = max(0, min(100, v))

    @property
    def max_speed(self):
        return self._max_speed

    @max_speed.setter
    def max_speed(self, v):
        """Потолок скорости (режим экономии при низком заряде батареи)."""
        self._max_speed = max(0, min(100, int(v)))

    def shutdown(self):
        self.stop()
        for pwm in (self._pwm_a, self._pwm_b):
            if pwm:
                try:
                    pwm.stop()
                except Exception:
                    pass
        logger.info('[Motors] shutdown')
