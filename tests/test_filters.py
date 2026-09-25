"""Тесты фильтров PID и Калмана (порт из v1/v2 с исправлениями)."""
import pytest

from picarpro.utils.filters import PID, KalmanFilter1D


class TestPID:
    def test_proportional_only(self):
        pid = PID(kp=2.0, out_min=-10, out_max=10)
        assert pid.update(1.0, dt=0.1) == pytest.approx(2.0)

    def test_integral_accumulates(self):
        pid = PID(kp=0.0, ki=1.0, out_min=-100, out_max=100)
        out = pid.update(1.0, dt=0.5)
        assert out == pytest.approx(0.5)

    def test_anti_windup(self):
        """Исправление бага PID.py: интеграл не должен накапливаться бесконечно."""
        pid = PID(kp=0.0, ki=10.0, out_min=-5, out_max=5, i_max=2.0)
        for _ in range(100):
            out = pid.update(100.0, dt=1.0)
        # интеграл зажат i_max=2, выхлоп ограничен out_max
        assert pid._integral <= 2.0 + 1e-9
        assert out == pytest.approx(5.0)  # ki*i_max=20, но out_max=5

    def test_output_clamped(self):
        pid = PID(kp=100.0, out_min=-1.0, out_max=1.0)
        assert pid.update(10.0, dt=0.1) == 1.0
        assert pid.update(-10.0, dt=0.1) == -1.0

    def test_derivative_kick_damping(self):
        pid = PID(kp=0.0, ki=0.0, kd=1.0, out_min=-100, out_max=100)
        pid.update(1.0, dt=1.0)
        out2 = pid.update(0.0, dt=1.0)
        assert out2 == pytest.approx(-1.0)

    def test_reset(self):
        pid = PID(kp=0.0, ki=1.0)
        pid.update(1.0, dt=1.0)
        pid.reset()
        assert pid._integral == 0.0
        assert pid._prev_error is None


class TestKalman:
    def test_first_value_adopted(self):
        kf = KalmanFilter1D()
        assert kf.update(15.0) == pytest.approx(15.0)

    def test_noise_smoothing(self):
        kf = KalmanFilter1D(process_variance=1e-3, measurement_variance=1.0)
        for v in (10.0, 20.0, 10.0, 20.0):
            kf.update(v)
        # фильтр держится ближе к среднему, чем зашумлённые входы
        assert 10.0 < kf.value < 20.0

    def test_converges_to_constant(self):
        kf = KalmanFilter1D(process_variance=1e-2, measurement_variance=0.5)
        for _ in range(50):
            kf.update(30.0)
        assert kf.value == pytest.approx(30.0, abs=0.5)

    def test_no_frozen_output(self):
        """Баг Kalman_Filter.py:26-29 — при малых изменениях фильтр возвращал
        старое значение, игнорируя вход. Здесь вход обязан влиять."""
        kf = KalmanFilter1D(process_variance=1e-2, measurement_variance=0.1)
        kf.update(10.0)
        for _ in range(20):
            kf.update(50.0)
        assert kf.value > 30.0

    def test_reset(self):
        kf = KalmanFilter1D()
        kf.update(10.0)
        kf.reset()          # без значения -> первое измерение принимается как есть
        assert kf.update(5.0) == pytest.approx(5.0)
