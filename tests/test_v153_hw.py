"""v1.5.3: ШИМ моторов (с v1.6.7 — только RPi.GPIO), аппаратный MJPEG,
кап FPS стрима."""
import time

import pytest


def wait_for(cond, timeout=3.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


# ---------------------------------------------------------------------------
class TestRpigpioPwm:
    """v1.6.7: pigpio убран — ШИМ всегда через RPi.GPIO."""

    def test_backend_is_rpigpio(self, fake_gpio):
        from picarpro.hardware.motors import MotorController
        m = MotorController()
        assert m.pwm_backend == 'rpigpio'

    def test_make_pwm_uses_gpio_pwm(self, fake_gpio):
        import RPi.GPIO as GPIO_F  # фейковый модуль из тестовой среды
        from picarpro.hardware.pwm import make_pwm
        pwm, backend = make_pwm(GPIO_F, 12, 100)
        assert backend == 'rpigpio'
        pwm.start(50)
        pwm.ChangeDutyCycle(75)
        pwm.stop()


# ---------------------------------------------------------------------------
@pytest.fixture
def cam(monkeypatch):
    import picarpro.camera.camera_opencv as cm
    from tests.fakes.all_fakes import FakePicamera2
    monkeypatch.setattr(cm.Camera, '_instance', None)
    FakePicamera2.silent_encoder = False
    FakePicamera2.fail_encoder = False
    c = None

    def make(hw=True):
        nonlocal c
        monkeypatch.setattr(cm, 'CAMERA_HW_MJPEG', hw)
        c = cm.Camera()
        return c
    yield make
    if c:
        c.shutdown()
    FakePicamera2.silent_encoder = False
    FakePicamera2.fail_encoder = False


class TestHwMjpeg:
    def test_frames_from_encoder_without_software_encode(self, cam, monkeypatch):
        import cv2
        calls = []
        orig = cv2.imencode
        monkeypatch.setattr(cv2, 'imencode', lambda *a, **k: calls.append(1) or orig(*a, **k))
        c = cam(hw=True)
        c.stream_open()
        c.start()
        fid, frame = c.wait_frame(0, timeout=3.0)
        assert frame is not None and frame.startswith(b'\xff\xd8hw-frame')
        fid2, _ = c.wait_frame(fid, timeout=2.0)
        assert fid2 > fid
        assert c._hw_active and not calls          # cv2.imencode вообще не вызывался
        c.stream_close()

    def test_no_python_capture_while_hw_streaming(self, cam):
        from tests.fakes.all_fakes import FakePicamera2
        c = cam(hw=True)
        c.stream_open()
        c.start()
        assert wait_for(lambda: c._hw_active, 3.0)
        time.sleep(0.2)
        n = FakePicamera2.instance._captures
        time.sleep(0.6)
        assert FakePicamera2.instance._captures == n       # capture_array не вызывается
        c.stream_close()

    def test_cv_overlay_switches_to_software(self, cam):
        c = cam(hw=True)
        c.stream_open()
        c.start()
        assert wait_for(lambda: c._hw_active, 3.0)
        c.set_cv_mode('findColor')
        assert wait_for(lambda: not c._hw_active, 3.0)
        fid, frame = c.wait_frame(c._frame_id, timeout=3.0)
        assert frame is not None and not frame.startswith(b'\xff\xd8hw-frame')
        c.set_cv_mode('none')
        assert wait_for(lambda: c._hw_active, 3.0)          # вернулись на аппаратный
        c.stream_close()

    def test_silent_encoder_falls_back(self, cam, monkeypatch):
        import picarpro.camera.camera_opencv as cm
        from tests.fakes.all_fakes import FakePicamera2
        monkeypatch.setattr(cm, 'HW_MJPEG_STARTUP_TIMEOUT_S', 0.3)
        FakePicamera2.silent_encoder = True
        c = cam(hw=True)
        c.stream_open()
        c.start()
        assert wait_for(lambda: not c._hw_ok, 4.0)
        fid, frame = c.wait_frame(0, timeout=3.0)           # видео не пропало
        assert frame is not None and not frame.startswith(b'\xff\xd8hw-frame')
        c.stream_close()

    def test_encoder_exception_falls_back(self, cam):
        from tests.fakes.all_fakes import FakePicamera2
        FakePicamera2.fail_encoder = True
        c = cam(hw=True)
        c.stream_open()
        c.start()
        fid, frame = c.wait_frame(0, timeout=3.0)
        assert frame is not None and c._hw_ok is False

    def test_disabled_by_default_path(self, cam):
        c = cam(hw=False)
        c.stream_open()
        c.start()
        fid, frame = c.wait_frame(0, timeout=3.0)
        assert frame is not None and not c._hw_active


class TestStreamFpsCap:
    def test_cap_reduces_jpeg_rate(self, cam, monkeypatch):
        import picarpro.camera.camera_opencv as cm
        monkeypatch.setattr(cm, 'CAMERA_FPS', 30)
        monkeypatch.setattr(cm, 'CAMERA_STREAM_FPS', 5)
        c = cam(hw=False)
        c.stream_open()
        c.start()
        c.wait_frame(0, timeout=3.0)
        start_id = c._frame_id
        time.sleep(2.0)
        produced = c._frame_id - start_id
        assert 5 <= produced <= 14, produced                # ~5 к/с, а не 30
        c.stream_close()
