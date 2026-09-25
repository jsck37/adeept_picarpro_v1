"""Фильтры сглаживания — порт из v1 (PID.py, Kalman_Filter.py) с исправлением
оригинальных багов:

* PID: добавлено ограничение интегральной составляющей (anti-windup),
  которого не было в оригинале (PID.py:44 — ``Ci += error*dt`` без ограничений).
* Kalman: убрано ветвление «при малых изменениях вернуть старое значение»
  (Kalman_Filter.py:26-29 игнорировало вход) и повторное присваивание P (стр. 38).
"""
import time


class PID:
    """ПИД-регулятор с ограничением выхода и anti-windup."""

    def __init__(self, kp=1.0, ki=0.0, kd=0.0,
                 out_min=-1.0, out_max=1.0, i_max=None):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.out_min = out_min
        self.out_max = out_max
        self.i_max = i_max if i_max is not None else (out_max - out_min) / 2.0
        self._integral = 0.0
        self._prev_error = None
        self._last_time = None

    def reset(self):
        self._integral = 0.0
        self._prev_error = None
        self._last_time = None

    def update(self, error, dt=None):
        """Обновление по ошибке. dt можно передать явно или замерить автоматически."""
        now = time.monotonic()
        if dt is None:
            if self._last_time is None:
                dt = 0.0
            else:
                dt = max(0.0, now - self._last_time)
        self._last_time = now

        self._integral += error * dt
        limit = self.i_max
        if self._integral > limit:
            self._integral = limit
        elif self._integral < -limit:
            self._integral = -limit

        derivative = 0.0
        if self._prev_error is not None and dt > 0:
            derivative = (error - self._prev_error) / dt
        self._prev_error = error

        out = self.kp * error + self.ki * self._integral + self.kd * derivative
        return max(self.out_min, min(self.out_max, out))


class KalmanFilter1D:
    """Одномерный фильтр Калмана для сглаживания зашумлённых показаний
    (дистанция ультразвука, тангаж IMU)."""

    def __init__(self, process_variance=1e-3, measurement_variance=1e-1,
                 initial_value=None):
        self.q = process_variance
        self.r = measurement_variance
        self.x = initial_value
        self.p = 1.0

    def reset(self, value=None):
        self.x = value
        self.p = 1.0

    def update(self, measurement):
        if self.x is None:
            self.x = float(measurement)
            return self.x
        # predict
        self.p += self.q
        # update
        k = self.p / (self.p + self.r)
        self.x += k * (float(measurement) - self.x)
        self.p *= (1.0 - k)
        return self.x

    @property
    def value(self):
        return self.x if self.x is not None else 0.0
