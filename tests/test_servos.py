"""Тесты ServoController (PCA9685 через фейки adafruit/busio)."""
import pytest


@pytest.fixture
def servos(tmp_path, monkeypatch):
    import picarpro.hardware.servos as mod
    from picarpro.hardware.servos import ServoController
    s = ServoController()
    yield s
    s.shutdown()


class TestInit:
    def test_pca_created_with_freq(self, servos):
        from tests.fakes.all_fakes import FakePCA9685
        assert len(FakePCA9685.instances) == 1
        assert FakePCA9685.instances[0].frequency == 50
        assert servos._pwm_initialized

    def test_all_channels_created(self, servos):
        from tests.fakes.all_fakes import FakeAdafruitServo
        assert len(FakeAdafruitServo.instances) == 7

    def test_init_angles_applied(self, servos):
        # каналы 5/6 -> CRANE_GRIP_HIGH=180 / CRANE_ARM_OPEN=100 (v1.3.0)
        assert servos.get_angle(5) == 180
        assert servos.get_angle(6) == 100
        assert servos.get_angle(0) == 90


class TestSetAngle:
    def test_set_within_limits(self, servos):
        assert servos.set_angle(0, 60) is True
        assert servos.get_angle(0) == 60

    def test_clamped_by_limits(self, servos):
        # руль: 30..150
        assert servos.set_angle(0, 10) is True
        assert servos.get_angle(0) == 30
        assert servos.set_angle(0, 200) is True
        assert servos.get_angle(0) == 150

    def test_invalid_channel(self, servos):
        assert servos.set_angle(-1, 90) is False
        assert servos.set_angle(99, 90) is False

    def test_duty_written_to_pca(self, servos):
        from tests.fakes.all_fakes import FakeAdafruitServo
        servos.set_angle(1, 60)
        servo_obj = FakeAdafruitServo.instances[1]
        assert servo_obj.channel.duty_cycle > 0


class TestLimits:
    def test_get_limits(self, servos):
        limits = servos.get_limits()
        assert 0 in limits and 6 in limits
        assert limits[0]['min'] == 30

    def test_limits_from_config_only(self, servos):
        """v1.3.0: лимиты строго из config.py, файла калибровки нет."""
        from config import SERVO_LIMITS
        assert servos.get_limits() == {
            int(k): dict(v) for k, v in SERVO_LIMITS.items()}
        assert servos.get_limits(2) == {'min': 80, 'max': 170}
        assert servos.get_limits(5) == {'min': 100, 'max': 180}
        assert servos.get_limits(6) == {'min': 100, 'max': 180}

    def test_no_runtime_editing(self, servos):
        """Редактирование лимитов из кода удалено."""
        assert not hasattr(servos, 'set_limits')

    def test_clamped_to_config_limits(self, servos):
        # tilt: 80..170, grip: 100..180, arm: 100..180
        assert servos.set_angle(2, 10) is True
        assert servos.get_angle(2) == 80
        assert servos.set_angle(2, 200) is True
        assert servos.get_angle(2) == 170
        assert servos.set_angle(5, 10) is True
        assert servos.get_angle(5) == 100
        assert servos.set_angle(6, 200) is True
        assert servos.get_angle(6) == 180

    def test_same_angle_not_rewritten(self, servos):
        """Анти-джиттер: повторная команда того же угла не пишет PWM."""
        from tests.fakes.all_fakes import FakeAdafruitServo
        obj = FakeAdafruitServo.instances[1]
        writes_after_init = obj.channel.writes
        assert servos.set_angle(1, 60) is True
        assert obj.channel.writes > writes_after_init
        assert servos.set_angle(1, 60) is True
        assert obj.channel.writes == writes_after_init + 1  # не выросла


class TestMove:
    def test_move_angle_offset(self, servos):
        servos.move_angle(1, 20)  # init 90 -> 110
        assert servos.get_angle(1) == 110

    def test_move_init_returns_home(self, servos):
        servos.set_angle(0, 150)
        servos.set_angle(1, 10)
        servos.move_init()
        assert servos.get_angle(0) == 90
        assert servos.get_angle(1) == 90

    def test_smooth_move_steps(self, servos):
        servos.smooth_move(1, 120, steps=5, delay=0.001)
        assert servos.get_angle(1) == 120

    def test_get_angle_invalid(self, servos):
        assert servos.get_angle(99) == 0


class TestShutdown:
    def test_shutdown_deinits_pca(self, servos):
        from tests.fakes.all_fakes import FakePCA9685
        servos.shutdown()
        assert FakePCA9685.instances[0].deinited
