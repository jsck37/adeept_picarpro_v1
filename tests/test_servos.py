"""Тесты ServoController (PCA9685 через фейки adafruit/busio)."""
import json
import pytest


@pytest.fixture
def servos(tmp_path, monkeypatch):
    import picarpro.hardware.servos as mod
    monkeypatch.setattr(mod, 'SERVO_CAL_FILE', tmp_path / 'servo_cal.json')
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
        # каналы 5/6 -> CRANE_GRIP_HIGH=190 / CRANE_ARM_OPEN=80
        assert servos.get_angle(5) == 190
        assert servos.get_angle(6) == 80
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
        servos.set_angle(1, 90)
        servo_obj = FakeAdafruitServo.instances[1]
        assert servo_obj.channel.duty_cycle > 0


class TestLimits:
    def test_get_limits(self, servos):
        limits = servos.get_limits()
        assert 0 in limits and 6 in limits
        assert limits[0]['min'] == 30

    def test_set_limits_persists(self, servos, tmp_path):
        servos.set_limits(2, 40, 160)
        data = json.loads((tmp_path / 'servo_cal.json').read_text())
        assert data['limits']['2'] == {'min': 40, 'max': 160}

    def test_set_limits_applied(self, servos):
        servos.set_limits(3, 50, 130)
        servos.set_angle(3, 10)
        assert servos.get_angle(3) == 50
        servos.set_angle(3, 180)
        assert servos.get_angle(3) == 130

    def test_cal_loaded_on_init(self, servos, tmp_path):
        cal = {'limits': {'1': {'min': 20, 'max': 160}}}
        (tmp_path / 'servo_cal.json').write_text(json.dumps(cal))
        from picarpro.hardware.servos import ServoController
        s2 = ServoController()
        assert s2.get_limits(1) == {'min': 20, 'max': 160}
        s2.shutdown()


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
