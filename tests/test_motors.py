"""Тесты MotorController (L298N через фейковый RPi.GPIO)."""
import pytest


@pytest.fixture
def motors():
    from picarpro.hardware.motors import MotorController
    m = MotorController()
    yield m
    m.shutdown()


class TestInit:
    def test_initialized(self, motors, fake_gpio):
        assert motors._initialized is True
        assert fake_gpio.mode_set == fake_gpio.BCM
        # 6 пинов настроены как OUT
        assert len(fake_gpio.modes) >= 6

    def test_pwm_created(self, motors, fake_gpio):
        pwm_a = fake_gpio.pwm_for(4)   # MOTOR_A_EN
        pwm_b = fake_gpio.pwm_for(17)  # MOTOR_B_EN
        assert pwm_a is not None and pwm_b is not None
        assert pwm_a.frequency == 1000


class TestMove:
    def test_forward_sets_direction_pins(self, motors, fake_gpio):
        from config import MOTOR_A_IN1, MOTOR_A_IN2, MOTOR_B_IN1, MOTOR_B_IN2
        motors.move(50, 'forward', 'no', 0.5)
        assert fake_gpio.outputs[MOTOR_A_IN1] == fake_gpio.HIGH
        assert fake_gpio.outputs[MOTOR_A_IN2] == fake_gpio.LOW
        assert fake_gpio.outputs[MOTOR_B_IN1] == fake_gpio.HIGH
        assert fake_gpio.outputs[MOTOR_B_IN2] == fake_gpio.LOW

    def test_backward_sets_direction_pins(self, motors, fake_gpio):
        from config import MOTOR_A_IN1, MOTOR_A_IN2
        motors.move(50, 'backward', 'no', 0.5)
        assert fake_gpio.outputs[MOTOR_A_IN1] == fake_gpio.LOW
        assert fake_gpio.outputs[MOTOR_A_IN2] == fake_gpio.HIGH

    def test_forward_duty_half(self, motors, fake_gpio):
        motors.move(50, 'forward', 'no', 0.5)
        pwm_a = fake_gpio.pwm_for(4)
        assert pwm_a.duty_cycle == pytest.approx(50.0)

    def test_speed_clamped(self, motors):
        motors.move(500, 'forward', 'no', 0.5)
        assert motors._speed == 100

    def test_invalid_direction_stops(self, motors, fake_gpio):
        motors.move(50, 'forward', 'no', 0.5)
        motors.move(50, 'sideways', 'no', 0.5)
        assert fake_gpio.pwm_for(4).duty_cycle == 0

    def test_move_differential_left_zero(self, motors, fake_gpio):
        """Разворот на месте: левый борт стоит, правый едет."""
        motors.move_differential(50, 'forward', 0.0, 1.0)
        pwm_a = fake_gpio.pwm_for(4)   # A = правая сторона
        pwm_b = fake_gpio.pwm_for(17)  # B = левая сторона
        assert pwm_a.duty_cycle == pytest.approx(50.0)
        assert pwm_b.duty_cycle == 0.0

    def test_move_differential_clamps(self, motors, fake_gpio):
        motors.move_differential(50, 'forward', 5.0, -1.0)
        pwm_a = fake_gpio.pwm_for(4)   # right=-1 -> зажат в 0
        pwm_b = fake_gpio.pwm_for(17)  # left=5 -> зажат в 1.0
        assert pwm_a.duty_cycle == 0
        assert pwm_b.duty_cycle == pytest.approx(50.0)


class TestStop:
    def test_stop_zeroes_duty_and_pins(self, motors, fake_gpio):
        from config import MOTOR_A_IN1, MOTOR_A_IN2
        motors.move(60, 'forward', 'no', 0.5)
        motors.stop()
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        assert fake_gpio.pwm_for(17).duty_cycle == 0
        assert fake_gpio.outputs[MOTOR_A_IN1] == fake_gpio.LOW
        assert fake_gpio.outputs[MOTOR_A_IN2] == fake_gpio.LOW


class TestShutdown:
    def test_shutdown_stops_pwm(self, motors, fake_gpio):
        motors.move(50, 'forward', 'no', 0.5)
        motors.shutdown()
        assert fake_gpio.pwm_for(4).stopped
        assert fake_gpio.pwm_for(17).stopped


class TestSpeedProperty:
    def test_speed_setter_clamps(self, motors):
        motors.speed = 150
        assert motors.speed == 100
        motors.speed = -5
        assert motors.speed == 0


class TestPinSemantics:
    """v1.6.1: роли пинов возвращены к заводской разводке Adeept v1
    (урок 6 «How to Control DC Motor»: Motor_A_Pin1 = 26, Motor_A_Pin2 =
    21; forward = Pin1 HIGH) и исходному коду jsck37 (gpiozero Motor(
    forward=MOTOR_A_IN1=26)). В v1.6.0 пары ошибочно поменяли местами —
    на реальном роботе инвертировались W/S («w» ехал назад).
    Тест фиксирует КОНКРЕТНЫЕ GPIO, чтобы семантика не потерялась."""

    def test_forward_pins_match_adeept_original(self, motors, fake_gpio):
        motors.move(50, 'forward', 'no', 0.5)
        assert fake_gpio.outputs[26] == fake_gpio.HIGH   # right forward (Pin1)
        assert fake_gpio.outputs[21] == fake_gpio.LOW    # right backward
        assert fake_gpio.outputs[27] == fake_gpio.HIGH   # left forward (Pin1)
        assert fake_gpio.outputs[18] == fake_gpio.LOW    # left backward

    def test_backward_pins_match_adeept_original(self, motors, fake_gpio):
        motors.move(50, 'backward', 'no', 0.5)
        assert fake_gpio.outputs[26] == fake_gpio.LOW
        assert fake_gpio.outputs[21] == fake_gpio.HIGH
        assert fake_gpio.outputs[27] == fake_gpio.LOW
        assert fake_gpio.outputs[18] == fake_gpio.HIGH

    def test_invert_right_board(self, motors, fake_gpio, monkeypatch):
        """MOTOR_INVERT_RIGHT — для сборок с перепутанными проводами."""
        import picarpro.hardware.motors as mm
        monkeypatch.setattr(mm, 'MOTOR_INVERT_RIGHT', True)
        motors.move(50, 'forward', 'no', 0.5)
        # борт A инвертирован: «вперёд» для него теперь 21 HIGH / 26 LOW
        assert fake_gpio.outputs[21] == fake_gpio.HIGH
        assert fake_gpio.outputs[26] == fake_gpio.LOW
        # борт B не тронут
        assert fake_gpio.outputs[27] == fake_gpio.HIGH
        assert fake_gpio.outputs[18] == fake_gpio.LOW
