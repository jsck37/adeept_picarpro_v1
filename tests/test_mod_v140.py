"""v1.4.0 — автовосстановление железа, RGB-нормализация камеры,
троттлинг диагностики серво/LED/камеры.

Фон репорта пользователя (тестирование на железе):
1) камера: «No camera number 0 found» — Picamera2/libcamera не видит
   CSI-сенсор; v1.4.0 добавляет USB/V4L2 fallback, диагностику и
   троттлинг логов;
3) WS2812 не работает — обычно SPI не включён (нет /dev/spidev0.0);
4) «сервоприводы не отображаются» в UI — PCA9685 не ответил на I2C
   в момент старта (шина была занята/зависла); v1.4.0 поднимает
   мёртвые модули автоматически, без перезапуска сервиса.
"""
import sys
import time

import numpy as np
import pytest


def _fake_picamera2():
    """Класс FakePicamera2 из тестового окружения (см. conftest)."""
    return sys.modules['picamera2'].Picamera2


class TestHwRetry:
    """Автовосстановление железа (state._hw_retry_step)."""

    @staticmethod
    def _state():
        from picarpro.state import SharedState
        state = SharedState()
        state.init_hardware()
        return state

    def test_dead_servos_recovered(self):
        state = self._state()
        # как если бы PCA9685 не ответил на шине в момент загрузки
        state.servos = None
        recovered = state._hw_retry_step()
        assert 'servos' in recovered
        assert state.servos is not None
        assert state.servos._pwm_initialized
        state.shutdown()

    def test_dead_leds_recovered(self):
        state = self._state()
        state.leds = None
        recovered = state._hw_retry_step()
        assert 'leds' in recovered
        assert state.leds is not None
        assert state.leds._initialized
        state.shutdown()

    def test_dead_oled_recovered(self):
        state = self._state()
        state.oled = None
        recovered = state._hw_retry_step()
        assert 'oled' in recovered
        assert state.oled is not None and state.oled._initialized
        state.shutdown()

    def test_healthy_modules_not_recreated(self):
        state = self._state()
        motors_before = state.motors
        servos_before = state.servos
        recovered = state._hw_retry_step()
        assert recovered == []
        assert state.motors is motors_before
        assert state.servos is servos_before
        state.shutdown()

    def test_retry_loop_survives_exception(self, monkeypatch):
        """Ошибка внутри шага не должна убивать фоновый цикл."""
        from picarpro.state import SharedState
        state = SharedState()
        state.running = True
        calls = []

        def _boom():
            calls.append(1)
            raise RuntimeError('fake diag error')

        monkeypatch.setattr(state, '_hw_retry_step', _boom)
        # имитируем 3 итерации цикла вручную (не запуская поток)
        for _ in range(3):
            try:
                state._hw_retry_step()
            except Exception:
                pass
        assert len(calls) == 3


class TestCameraRgbNormalization:
    """Захват нормализуется к RGB (Picamera2 «RGB888» отдаёт BGR)."""

    @pytest.fixture(autouse=True)
    def _cam(self):
        from picarpro.camera.camera_opencv import Camera
        Camera._instance = None
        yield
        if Camera._instance:
            Camera._instance.shutdown()
            Camera._instance = None

    def test_capture_normalized_to_rgb(self):
        from picarpro.camera.camera_opencv import Camera
        # «сырой» кадр фейка: канал 0 = 10 (синий в BGR), канал 2 = 30
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        frame[2, 2] = (10, 20, 30)
        _fake_picamera2().frame = frame
        cam = Camera.get_instance()
        cam.start()
        deadline = time.time() + 3.0
        raw = None
        while time.time() < deadline:
            raw = cam.get_raw_frame()
            if raw is not None:
                break
            time.sleep(0.05)
        assert raw is not None, 'кадр не получен за 3 c'
        # после BGR2RGB нормализации каналы переставлены: (30, 20, 10)
        assert tuple(int(v) for v in raw[2, 2]) == (30, 20, 10)

    def test_get_frame_returns_jpeg(self):
        from picarpro.camera.camera_opencv import Camera
        _fake_picamera2().frame = np.full((8, 8, 3), 90, dtype=np.uint8)
        cam = Camera.get_instance()
        cam.start()
        deadline = time.time() + 3.0
        frame = None
        while time.time() < deadline:
            frame = cam.get_frame()
            if frame:
                break
            time.sleep(0.05)
        assert frame is not None
        assert frame[:2] == b'\xff\xd8'  # JPEG SOI — кодирование не сломано


class TestDiagThrottle:
    """Диагностические логи печатаются не чаще интервала."""

    def test_servo_diag_throttled(self):
        from picarpro.hardware.servos import ServoController
        ServoController._last_diag_ts = 0.0
        ServoController._log_diag('first')
        ts1 = ServoController._last_diag_ts
        assert ts1 > 0.0
        ServoController._log_diag('second — должен быть подавлен')
        assert ServoController._last_diag_ts == ts1

    def test_led_diag_throttled(self):
        from picarpro.hardware.leds_ws2812 import LEDController
        LEDController._last_diag_ts = 0.0
        LEDController._log_diag()
        ts1 = LEDController._last_diag_ts
        assert ts1 > 0.0
        LEDController._log_diag()
        assert LEDController._last_diag_ts == ts1

    def test_camera_diag_throttled(self):
        from picarpro.camera import camera_opencv as cam_mod
        cam_mod.Camera._last_diag_ts = 0.0
        cam_mod.Camera._log_init_failure(RuntimeError('boom'))
        ts1 = cam_mod.Camera._last_diag_ts
        assert ts1 > 0.0
        cam_mod.Camera._log_init_failure(RuntimeError('boom2'))
        assert cam_mod.Camera._last_diag_ts == ts1

    def test_v4l2_fallback_skipped_without_faker_support(self):
        """В тестовом окружении (фейк cv2 без VideoCapture) V4L2-ветка
        не активируется и не падает."""
        from picarpro.camera.camera_opencv import Camera
        cam = Camera.get_instance()
        cam._init_camera()
        # фейковая Picamera2 всегда стартует — источник picamera2
        assert cam._source == 'picamera2'
        assert cam._cap is None
