"""Стабилизация камеры — порт фичи steadyCamera из v2 (WebServer.py:124-133).

В v2 стабилизация держала тангаж камеры по гироскопу/акселерометру.
Здесь: фоновый поток читает pitch у MPU6050, пропускает через фильтр
Калмана (исправленный порт Kalman_Filter.py) и компенсирует наклон
каналом SERVO_CAM_TILT вокруг базового угла.
"""
import threading
import time

from picarpro.logger import logger
from picarpro.utils.filters import KalmanFilter1D
from config import (
    SERVO_CAM_TILT, STEADY_COMPENSATION_GAIN, STEADY_UPDATE_INTERVAL,
    STEADY_CAMERA_ENABLED,
)


class SteadyCameraController:
    def __init__(self, servos, mpu6050):
        self._servos = servos
        self._mpu = mpu6050
        self._active = False
        self._running = True
        self._lock = threading.Lock()
        self._base_tilt = 90
        self._kalman = KalmanFilter1D(
            process_variance=1e-4, measurement_variance=5e-2)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if not STEADY_CAMERA_ENABLED:
            logger.info('[Steady] disabled by config')

    @property
    def active(self):
        with self._lock:
            return self._active

    def set_base_tilt(self, angle):
        """Текущий угол наклона становится базой для компенсации."""
        with self._lock:
            self._base_tilt = max(0, min(180, int(angle)))

    def start(self):
        if not STEADY_CAMERA_ENABLED:
            return False, 'Steady camera disabled'
        if not (self._mpu and self._mpu.initialized):
            return False, 'MPU6050 not available'
        with self._lock:
            self._base_tilt = self._servos.get_angle(SERVO_CAM_TILT) \
                if self._servos else 90
            self._kalman.reset()
            self._active = True
        logger.info('[Steady] camera stabilization ON')
        return True, 'Steady camera on'

    def stop(self):
        with self._lock:
            if not self._active:
                return
            self._active = False
        # вернуть камеру на базовый угол
        if self._servos:
            self._servos.set_angle(SERVO_CAM_TILT, self._base_tilt)
        logger.info('[Steady] camera stabilization OFF')

    def _loop(self):
        while self._running:
            if self._active and self._mpu and self._mpu.initialized \
                    and self._servos and self._servos._pwm_initialized:
                data = self._mpu.get_data()
                if data is not None:
                    pitch = self._kalman.update(data['pitch'])
                    with self._lock:
                        base = self._base_tilt
                    target = max(0, min(180,
                                 int(base + pitch * STEADY_COMPENSATION_GAIN)))
                    self._servos.set_angle(SERVO_CAM_TILT, target)
            else:
                self._kalman.reset()
            time.sleep(STEADY_UPDATE_INTERVAL)

    def shutdown(self):
        self._running = False
        self.stop()
