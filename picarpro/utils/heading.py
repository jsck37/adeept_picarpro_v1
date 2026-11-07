"""Интегратор курса по гироскопу MPU6050 (угол поворота вокруг вертикали).

Нужен для разворотов «на N градусов» вместо «на N секунд»: время разворота
зависит от заряда батареи, покрытия и нагрузки, угол — нет.

Знак угла не используется (зависит от установки платы) — важна величина.
"""
import time

from config import GYRO_BIAS_SAMPLES


class HeadingTracker:
    def __init__(self, mpu):
        self._mpu = mpu
        self._bias = 0.0
        self._yaw = 0.0
        self._last_ts = None
        self._last_gz = 0.0

    @property
    def available(self):
        return bool(self._mpu is not None and self._mpu.initialized)

    def reset_angle(self):
        """Обнулить накопленный угол (перед новым разворотом)."""
        self._yaw = 0.0
        self._last_ts = None

    def begin(self):
        """Включить быстрый опрос гироскопа и обнулить угол."""
        self.reset_angle()
        if self._mpu is not None:
            self._mpu.request_fast('heading')

    def end(self):
        if self._mpu is not None:
            self._mpu.release_fast('heading')

    def calibrate(self, wait, samples=GYRO_BIAS_SAMPLES, timeout=2.0):
        """Смещение нуля гироскопа (робот должен стоять). wait(t) -> True,
        если прервано. Возвращает True при успехе."""
        if not self.available:
            return False
        self._mpu.request_fast('heading')
        values, seen = [], None
        deadline = time.monotonic() + timeout
        while len(values) < samples and time.monotonic() < deadline:
            data = self._mpu.get_data()
            if data and data.get('ts') != seen:
                seen = data.get('ts')
                values.append(data['gyro']['z'])
            if wait(0.03):
                return False
        if len(values) < max(3, samples // 2):
            return False
        values.sort()
        self._bias = values[len(values) // 2]       # медиана — устойчива к шуму
        return True

    def update(self):
        """Прочитать новые сэмплы и накопить угол. Возвращает |yaw|, град."""
        data = self._mpu.get_data() if self.available else None
        if data:
            ts = data.get('ts')
            if ts is not None and ts != self._last_ts:
                gz = data['gyro']['z'] - self._bias
                if self._last_ts is not None:
                    dt = min(0.5, max(0.0, ts - self._last_ts))
                    self._yaw += 0.5 * (gz + self._last_gz) * dt   # трапеции
                self._last_ts = ts
                self._last_gz = gz
        return abs(self._yaw)
