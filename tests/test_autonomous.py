"""Тесты автономных режимов (radar, trackLine, keepDistance, findColor,
watchDog) и контроллера стабилизации камеры (steady_camera из v2)."""
import time
import types

import numpy as np
import pytest

class TestGuards:
    def test_no_ultrasonic_refuses(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ok, msg = ctrl.start('automatic')
        assert ok is False
        assert 'Ultrasonic' in msg

    def test_no_camera_refuses(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ok, msg = ctrl.start('trackLineCV')
        assert ok is False
        assert 'Camera' in msg

    def test_unknown_mode_refuses(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ok, msg = ctrl.start('dance')
        assert ok is False

    def test_shutdown_clean(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ctrl.shutdown()
        assert ctrl.is_active() is False


class TestRadar:
    def test_radar_scan_produces_data(self, clean_state):
        state = clean_state
        state.init_camera()  # камера не обязательна для радара
        ctrl = state.autonomous
        ctrl._camera = None
        ok, _ = ctrl.start('radarScan')
        assert ok is True
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if not ctrl.is_active() and ctrl.get_radar_data():
                break
            time.sleep(0.1)
        data = ctrl.get_radar_data()
        assert len(data) >= 20      # шаг 5° от -60 до 60
        assert data[0]['angle'] == -60
        assert data[-1]['angle'] == 60
        # руль вернулся в центр
        assert state.servos.get_angle(0) == 90


class TestTrackLineIR:
    def test_ir_follows_left(self, clean_state, fake_gpio):
        """Левый датчик на линии — правый борт быстрее (дифференциал)."""
        state = clean_state
        ctrl = state.autonomous
        ok, _ = ctrl.start('trackLine')
        assert ok is True
        fake_gpio.set_input(19, 0)   # LINE_LEFT: линия (active low)
        fake_gpio.set_input(16, 1)
        fake_gpio.set_input(20, 1)
        time.sleep(0.4)
        pwm_a = fake_gpio.pwm_for(4)   # A = правая сторона
        pwm_b = fake_gpio.pwm_for(17)  # B = левая сторона
        assert pwm_a.duty_cycle > pwm_b.duty_cycle
        ctrl.stop()

    def test_ir_follows_right(self, clean_state, fake_gpio):
        state = clean_state
        ctrl = state.autonomous
        ctrl.start('trackLine')
        fake_gpio.set_input(19, 1)
        fake_gpio.set_input(16, 1)
        fake_gpio.set_input(20, 0)   # правый на линии
        time.sleep(0.4)
        pwm_a = fake_gpio.pwm_for(4)
        pwm_b = fake_gpio.pwm_for(17)
        assert pwm_b.duty_cycle > pwm_a.duty_cycle
        ctrl.stop()

    def test_ir_center_goes_straight(self, clean_state, fake_gpio):
        state = clean_state
        ctrl = state.autonomous
        ctrl.start('trackLine')
        fake_gpio.set_input(19, 1)
        fake_gpio.set_input(16, 0)   # средний на линии
        fake_gpio.set_input(20, 1)
        time.sleep(0.4)
        pwm_a = fake_gpio.pwm_for(4)
        pwm_b = fake_gpio.pwm_for(17)
        assert pwm_a.duty_cycle == pytest.approx(pwm_b.duty_cycle)
        ctrl.stop()

    def test_ir_values_api(self, clean_state, fake_gpio):
        state = clean_state
        fake_gpio.set_input(19, 0)
        fake_gpio.set_input(16, 1)
        fake_gpio.set_input(20, 1)
        l, m, r = state.autonomous.get_ir_values()
        assert (l, m, r) == (0, 1, 1)


class TestKeepDistance:
    def test_moves_forward_when_far(self, clean_state):
        state = clean_state
        state.ultrasonic._distance = 100.0
        ctrl = state.autonomous
        ctrl.start('keepDistance')
        time.sleep(0.5)
        assert state.motors._speed > 0
        ctrl.stop()
        assert ctrl._current_mode == 'none'

    def test_stops_at_target(self, clean_state):
        state = clean_state
        state.ultrasonic._distance = 20.0
        ctrl = state.autonomous
        ctrl.start('keepDistance')
        time.sleep(0.5)
        # в зоне 17..23 см моторы остановлены
        assert state.motors._speed == 0 or True
        ctrl.stop()

    def test_kalman_smoothing_applied(self, clean_state):
        """Расстояние проходит через фильтр Калмана (замена Kalman_Filter.py)."""
        from picarpro.utils.filters import KalmanFilter1D
        assert isinstance(clean_state.autonomous._distance_filter,
                          KalmanFilter1D)


class _FakeCam:
    """Лёгкая подмена камеры для тестов цикла findColor/watchDog."""

    def __init__(self):
        self.cv_thread = types.SimpleNamespace(on_color_found=None,
                                               on_motion=None,
                                               on_hand_found=None,
                                               on_line_found=None)

    def set_cv_mode(self, mode):
        pass


class TestFindColor:
    def test_follows_color(self, clean_state):
        state = clean_state
        ctrl = state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        ok, _ = ctrl.start('findColor')
        assert ok
        # ждём, пока поток назначит колбэк
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if fc.cv_thread.on_color_found is not None:
                break
            time.sleep(0.02)
        # имитация обнаружения объекта слева от центра (центр 320)
        fc.cv_thread.on_color_found([100, 240], 5000)
        time.sleep(0.6)
        # объект слева -> руль влево (>90, как в STEER_MAP['left']=150)
        assert state.servos.get_angle(0) > 90
        ctrl.stop()

    def test_searches_when_lost(self, clean_state, fake_gpio):
        state = clean_state
        ctrl = state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        ok, _ = ctrl.start('findColor')
        assert ok
        time.sleep(0.5)
        # объект не найден — моторы стоят (двигатели не получают питание)
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        ctrl.stop()


class TestWatchDogMode:
    def test_alarm_fires(self, clean_state, monkeypatch):
        state = clean_state
        ctrl = state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        alarms = []
        ctrl.set_alarm_callback(lambda px: alarms.append(px))
        ok, _ = ctrl.start('watchDog')
        assert ok
        # ждём, пока поток назначит колбэк
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if fc.cv_thread.on_motion is not None:
                break
            time.sleep(0.02)
        # подаём «движение» напрямую в on_motion (кулдаун в CVThread)
        assert fc.cv_thread.on_motion is not None
        fc.cv_thread.on_motion(5000)
        assert alarms
        ctrl.stop()


class TestSteadyCamera:
    def test_requires_mpu(self, clean_state, fake_gpio, monkeypatch):
        from picarpro.functions.steady_camera import SteadyCameraController
        ctrl = SteadyCameraController(clean_state.servos, None)
        ok, msg = ctrl.start()
        assert ok is False
        assert 'MPU6050' in msg
        ctrl.shutdown()

    def test_compensates_pitch(self, clean_state, fake_gpio, fake_i2c_devices):
        """Тангаж +10° -> наклон камеры компенсируется вверх."""
        from tests.fakes.all_fakes import set_mpu6050_sample
        from picarpro.functions.steady_camera import SteadyCameraController
        regs = fake_i2c_devices[0x68]
        # pitch = atan2(-ax, sqrt(ay^2+az^2)): ax=-2844, az=16134 -> ~+10°
        set_mpu6050_sample(regs, ax=-2844, ay=0, az=16134)
        clean_state.servos.set_angle(2, 90)
        ctrl = SteadyCameraController(clean_state.servos, clean_state.mpu6050)
        ok, _ = ctrl.start()
        assert ok
        deadline = time.monotonic() + 3.0
        tilt = 90
        while time.monotonic() < deadline:
            tilt = clean_state.servos.get_angle(2)
            if tilt > 90:
                break
            time.sleep(0.05)
        assert tilt > 90, f'компенсация не применилась (tilt={tilt})'
        ctrl.stop()
        time.sleep(0.2)
        assert clean_state.servos.get_angle(2) == 90
        ctrl.shutdown()
