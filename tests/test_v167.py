"""v1.6.7: режимы obstacleGrab и mazeU, три YOLO CV-режима (детект/маски/
семантика), удаление pigpio и hugo-режимов."""
import time
import types

import pytest


@pytest.fixture
def state(clean_state):
    return clean_state


class TestModeRegistry:
    def test_new_modes_registered(self):
        from picarpro.functions.autonomous import ALL_MODES
        assert 'obstacleGrab' in ALL_MODES
        assert 'mazeU' in ALL_MODES

    def test_new_modes_need_only_ultrasonic(self, state):
        ok, _ = state.autonomous.start('obstacleGrab')
        assert ok is True                 # камера не обязательна
        state.autonomous.stop()
        ok, _ = state.autonomous.start('mazeU')
        assert ok is True
        state.autonomous.stop()

    def test_camera_modes_unchanged(self, state):
        ok, msg = state.autonomous.start('trackLineCV')
        assert ok is False and 'Camera' in msg


class TestMazeU:
    def test_two_turns_and_exit(self, state, monkeypatch):
        """Стена -> поворот -> стена -> поворот -> свободно = выход."""
        import picarpro.functions.autonomous as am
        from config import MAZE_TURN_DIR
        monkeypatch.setattr(am, 'MAZE_BACKUP_S', 0.05)
        monkeypatch.setattr(am, 'MAZE_EXIT_FREE_S', 0.2)
        monkeypatch.setattr(am, 'MAZE_TIMEOUT_S', 15)
        ctrl = state.autonomous
        ctrl._camera = None
        seq = [100.0, 10.0, 100.0, 10.0, 200.0, 200.0, 200.0]
        monkeypatch.setattr(ctrl, '_safe_distance',
                            lambda: seq.pop(0) if len(seq) > 1 else seq[0])
        turns = []
        monkeypatch.setattr(am, 'arc_turn',
                            lambda m, s, deg, **k: turns.append(deg))
        ok, _ = ctrl.start('mazeU')
        assert ok is True
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline and ctrl.is_active():
            time.sleep(0.05)
        assert not ctrl.is_active(), 'mode did not finish'
        deg = 90.0 if MAZE_TURN_DIR == 'right' else -90.0
        assert turns == [deg, deg]        # U = два поворота на 90°
        ctrl.stop()


class TestObstacleGrab:
    @staticmethod
    def _finish(ctrl, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and ctrl.is_active():
            time.sleep(0.05)
        assert not ctrl.is_active(), 'mode did not finish'

    def test_blind_grab_relocate_return(self, state, monkeypatch):
        """Без камеры: подъезд по ультразвуку -> захват клешнёй -> разворот
        -> перенос -> отпускание -> возврат -> режим завершён."""
        import picarpro.functions.autonomous as am
        from config import (CRANE_ARM_OPEN, CRANE_ARM_CLOSED,
                            CRANE_GRIP_MID, SERVO_CRANE_ARM,
                            SERVO_CRANE_GRIP)
        monkeypatch.setattr(am, 'OBSTACLE_GRAB_RELOCATE_DRIVE_S', 0.2)
        monkeypatch.setattr(am, 'OBSTACLE_GRAB_RETURN_DRIVE_S', 0.2)
        ctrl = state.autonomous
        ctrl._camera = None
        seq = [40.0, 30.0, 20.0, 12.0, 12.0, 12.0, 12.0, 12.0]
        monkeypatch.setattr(ctrl, '_safe_distance',
                            lambda: seq.pop(0) if len(seq) > 1 else seq[0])
        turns = []
        monkeypatch.setattr(am, 'arc_turn',
                            lambda m, s, deg, **k: turns.append(deg))
        ok, _ = ctrl.start('obstacleGrab')
        assert ok is True
        self._finish(ctrl)
        assert turns == [am.OBSTACLE_GRAB_RELOCATE_TURN_DEG,
                         am.OBSTACLE_GRAB_RETURN_TURN_DEG]
        # клешня вернулась в транспортное положение после отпускания
        assert state.servos.get_angle(SERVO_CRANE_ARM) == CRANE_ARM_OPEN
        assert state.servos.get_angle(SERVO_CRANE_GRIP) == CRANE_GRIP_MID
        assert state.servos.get_angle(SERVO_CRANE_ARM) != CRANE_ARM_CLOSED

    def test_vision_aim_steers_toward_object(self, state, monkeypatch):
        """Есть YOLO-объект справа в кадре — руль уходит вправо (< 90)."""
        import picarpro.functions.autonomous as am
        from config import SERVO_STEERING
        monkeypatch.setattr(am, 'OBSTACLE_GRAB_RELOCATE_DRIVE_S', 0.05)
        monkeypatch.setattr(am, 'OBSTACLE_GRAB_RETURN_DRIVE_S', 0.05)
        monkeypatch.setattr(am, 'OBSTACLE_GRAB_SPEED', 0)  # едем врасползание
        ctrl = state.autonomous

        yolo = types.SimpleNamespace(
            loaded=True, _objects_lock=None,
            objects=[{'label': 'box', 'conf': 0.9,
                      'box': [400, 200, 100, 100],
                      'cx': 450, 'cy': 250, 'area': 10000}])

        class _Cam:
            def __init__(self):
                self.cv_thread = types.SimpleNamespace(yolo=yolo)
                self.modes = []

            def set_cv_mode(self, m):
                self.modes.append(m)

        cam = _Cam()
        ctrl._camera = cam
        seq = [40.0, 20.0, 12.0, 12.0, 12.0, 12.0, 12.0, 12.0]
        monkeypatch.setattr(ctrl, '_safe_distance',
                            lambda: seq.pop(0) if len(seq) > 1 else seq[0])
        monkeypatch.setattr(am, 'arc_turn', lambda m, s, deg, **k: None)
        ok, _ = ctrl.start('obstacleGrab')
        assert ok is True
        # в поисковой фазе руль доворачивает вправо (cx=450 в кадре 640:
        # err=+0.4 -> угол 90-24=66)
        deadline = time.monotonic() + 3.0
        steered = False
        while time.monotonic() < deadline:
            if state.servos.get_angle(SERVO_STEERING) < 80:
                steered = True
                break
            time.sleep(0.05)
        ctrl.stop()
        assert steered
        assert cam.modes                  # камера переводилась в CV_YOLO


class TestYoloCvModes:
    def test_cv_mode_command_accepts_new_modes(self, state):
        from picarpro.commands import process
        state.init_camera()
        for mode in ('yoloSeg', 'yoloSemantic'):
            r = process(state, {'cmd': 'cv_mode', 'params': {'mode': mode}})
            assert r['ok'] is True
            assert state.camera.cv_thread.cv_mode == mode
        r = process(state, {'cmd': 'cv_mode', 'params': {'mode': 'yoloDetect'}})
        assert r['ok'] is True
        assert state.camera.cv_thread.cv_mode == 'yoloDetect'

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

    def test_draw_semantic_paints(self, monkeypatch):
        import numpy as np
        import picarpro.camera.yolo_detector as yd
        monkeypatch.setattr(yd, 'cv2', _stub_cv2())
        det = yd.YoloDetector()
        disp = np.zeros((480, 640, 3), np.uint8)
        det.draw_semantic(disp, _objs())
        assert disp[150, 200].any()       # заливка классом (плотнее)

    def test_parse_fills_semantics_and_status(self, monkeypatch):
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
        assert det._semantics['id0'] > 0
        det.task = 'seg'
        det.objects = objs
        st = det.get_status()
        assert st['task'] == 'seg'
        assert st['semantics']['id0'] > 0


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
