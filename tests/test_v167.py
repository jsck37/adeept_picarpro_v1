"""v1.6.7 (+ правки v1.6.10): удаление pigpio и hugo-режимов; режимы
obstacleGrab / mazeU / yoloDetect / yoloSemantic удалены в v1.6.10, остался
только «YOLO Mask» (yoloSeg)."""
import time
import types

import pytest


@pytest.fixture
def state(clean_state):
    return clean_state


class TestModeRegistry:
    def test_removed_modes_gone_new_mode_present(self):
        from picarpro.functions.autonomous import ALL_MODES, MODES_WITH_ULTRA
        for removed in ('automatic', 'keepDistance', 'obstacleGrab', 'mazeU'):
            assert removed not in ALL_MODES
        assert 'mazeExit' in ALL_MODES and 'radarScan' in ALL_MODES
        assert set(MODES_WITH_ULTRA) == {'radarScan', 'mazeExit'}

    def test_ultrasonic_modes_need_only_ultrasonic(self, state):
        for mode in ('radarScan', 'mazeExit'):
            ok, _ = state.autonomous.start(mode)
            assert ok is True             # камера не обязательна
            state.autonomous.stop()

    def test_camera_modes_unchanged(self, state):
        ok, msg = state.autonomous.start('trackLineCV')
        assert ok is False and 'Camera' in msg


class TestYoloCvModes:
    def test_cv_mode_command_accepts_only_yolo_mask(self, state):
        from picarpro.commands import process
        state.init_camera()
        r = process(state, {'cmd': 'cv_mode', 'params': {'mode': 'yoloSeg'}})
        assert r['ok'] is True
        assert state.camera.cv_thread.cv_mode == 'yoloSeg'
        for removed in ('yoloDetect', 'yoloSemantic'):
            r = process(state, {'cmd': 'cv_mode', 'params': {'mode': removed}})
            assert r['ok'] is False
            assert state.camera.cv_thread.cv_mode == 'yoloSeg'   # режим не сменился

    def test_removed_cv_commands(self, state):
        from picarpro.commands import process
        for cmd in ('aruco_nav', 'color_scan'):
            r = process(state, {'cmd': cmd, 'params': {'action': 'status'}})
            assert r['ok'] is False


def _stub_cv2():
    return types.SimpleNamespace(
        dnn=types.SimpleNamespace(NMSBoxes=staticmethod(
            lambda boxes, scores, st, nt: [[0]])),
        resize=lambda s, d, interpolation=None: s,
        INTER_NEAREST=0, FONT_HERSHEY_SIMPLEX=0,
        rectangle=lambda *a, **k: None,
        putText=lambda *a, **k: None,
    )


def _objs():
    import numpy as np
    mask = np.zeros((480, 640), dtype=bool)
    mask[100:200, 100:300] = True
    return [{'label': 'person', 'conf': 0.9, 'box': [100, 100, 200, 100],
             'cx': 200, 'cy': 150, 'area': 20000,
             'mask': mask, 'mask_area': int(mask.sum()),
             'mask_pct': round(100.0 * int(mask.sum()) / (480 * 640), 1)}]


class TestDetectorDrawModes:
    def test_draw_without_masks_paints_nothing(self, monkeypatch):
        import numpy as np
        import picarpro.camera.yolo_detector as yd
        monkeypatch.setattr(yd, 'cv2', _stub_cv2())
        disp = np.zeros((480, 640, 3), np.uint8)
        yd.YoloDetector.draw(None, disp, _objs(), masks=False)
        assert not disp.any()             # рамки/текст — стаб, маски выключены

    def test_draw_masks_paints(self, monkeypatch):
        import numpy as np
        import picarpro.camera.yolo_detector as yd
        monkeypatch.setattr(yd, 'cv2', _stub_cv2())
        det = yd.YoloDetector()
        disp = np.zeros((480, 640, 3), np.uint8)
        det.draw(disp, _objs(), masks=True)
        assert disp[150, 200].any()       # маска залита

    def test_no_semantic_mode_left(self):
        import picarpro.camera.yolo_detector as yd
        import picarpro.camera.camera_opencv as cm
        assert not hasattr(yd.YoloDetector, 'draw_semantic')
        assert not hasattr(cm, 'CV_YOLO')
        assert not hasattr(cm, 'CV_YOLO_SEM')
        assert cm.CV_YOLO_SEG == 'yoloSeg'

    def test_parse_fills_masks_and_status(self, monkeypatch):
        import numpy as np
        import picarpro.camera.yolo_detector as yd
        monkeypatch.setattr(yd, 'cv2', _stub_cv2())
        det = yd.YoloDetector()
        out = np.zeros((1, 25200, 117), dtype=np.float32)
        out[0, 1000, 0:4] = (320, 320, 100, 100)
        out[0, 1000, 4] = 0.9
        out[0, 1000, 5] = 0.8
        out[0, 1000, 85] = 3.0
        proto = np.zeros((1, 32, 160, 160), dtype=np.float32)
        proto[0, 0] = 3.0
        objs = det._parse_output(out, 640, 480, proto=proto)
        assert objs and 'mask_area' in objs[0]
        det.task = 'seg'
        det.objects = objs
        st = det.get_status()
        assert st['task'] == 'seg'
        assert 'semantics' not in st        # карта классов удалена вместе с YOLO Sem


class TestPigpioRemoved:
    def test_make_pwm_is_rpigpio(self, fake_gpio):
        from picarpro.hardware.pwm import make_pwm
        pwm, backend = make_pwm(fake_gpio, 12, 100)
        assert backend == 'rpigpio'

    def test_no_pigpio_symbols(self):
        import picarpro.hardware.pwm as pwm
        assert not hasattr(pwm, 'PigpioPWM')
        assert not hasattr(pwm, '_get_pi')
        import config as cfg
        assert not hasattr(cfg, 'MOTOR_PWM_BACKEND')

    def test_setup_has_no_pigpio(self):
        src = open('setup.py').read()
        assert 'pigpio' not in src
