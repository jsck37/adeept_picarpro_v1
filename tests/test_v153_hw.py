"""v1.5.3: pigpio-ШИМ моторов, аппаратный MJPEG, кап FPS стрима."""
import sys
import time
import types

import pytest


def wait_for(cond, timeout=3.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


# ---------------------------------------------------------------------------
class _FakePi:
    connected = True

    def __init__(self):
        self.calls = []

    def set_PWM_range(self, pin, r): self.calls.append(('range', pin, r))
    def set_PWM_frequency(self, pin, f): self.calls.append(('freq', pin, f))
    def set_PWM_dutycycle(self, pin, d): self.calls.append(('duty', pin, d))


@pytest.fixture
def fake_pigpio(monkeypatch):
    from picarpro.hardware import pwm
    mod = types.ModuleType('pigpio')
    pi = _FakePi()
    mod.pi = lambda: pi
    monkeypatch.setitem(sys.modules, 'pigpio', mod)
    pwm.reset_for_tests()
    yield pi
    pwm.reset_for_tests()


class TestPigpioPwm:
    def test_motors_use_pigpio_when_daemon_available(self, fake_pigpio, fake_gpio):
        from config import MOTOR_A_EN
        from picarpro.hardware.motors import MotorController
        m = MotorController()
        assert m.pwm_backend == 'pigpio'
        m.move(50, 'forward', 'no')
        duties = [c for c in fake_pigpio.calls if c[0] == 'duty' and c[1] == MOTOR_A_EN]
        assert duties[-1][2] == 500           # 50 % при разрешении 1000
        m.stop()
        assert [c for c in fake_pigpio.calls if c[0] == 'duty' and c[1] == MOTOR_A_EN][-1][2] == 0
        assert ('range', MOTOR_A_EN, 1000) in fake_pigpio.calls

    def test_fallback_when_daemon_down(self, monkeypatch, fake_gpio):
        from picarpro.hardware import pwm
        from picarpro.hardware.motors import MotorController
        mod = types.ModuleType('pigpio')
        down = _FakePi()
        down.connected = False
        mod.pi = lambda: down
        monkeypatch.setitem(sys.modules, 'pigpio', mod)
        pwm.reset_for_tests()
        assert MotorController().pwm_backend == 'rpigpio'
        pwm.reset_for_tests()

    def test_fallback_without_module(self, monkeypatch, fake_gpio):
        from picarpro.hardware import pwm
        from picarpro.hardware.motors import MotorController
        monkeypatch.setitem(sys.modules, 'pigpio', None)     # import -> ImportError
        pwm.reset_for_tests()
        assert MotorController().pwm_backend == 'rpigpio'
        pwm.reset_for_tests()

    def test_forced_rpigpio(self, fake_pigpio, fake_gpio, monkeypatch):
        import picarpro.hardware.motors as mm
        monkeypatch.setattr(mm, 'MOTOR_PWM_BACKEND', 'rpigpio')
        assert mm.MotorController().pwm_backend == 'rpigpio'

    def test_duty_clamped(self, fake_pigpio):
        from picarpro.hardware.pwm import PigpioPWM
        p = PigpioPWM(fake_pigpio, 4, 1000)
        p.ChangeDutyCycle(150)
        p.ChangeDutyCycle(-5)
        assert [c[2] for c in fake_pigpio.calls if c[0] == 'duty'] == [1000, 0]


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
