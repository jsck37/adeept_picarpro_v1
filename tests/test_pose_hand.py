"""v1.6.10: YOLO-pose для режима Hand — выбор самой передней руки, разбор
выхода Ultralytics (1x56xN) и слежение камерой (только панорама/наклон)."""
import time
import types

import numpy as np
import pytest

from tests.test_v150 import wait_for


def _person(height, wrists=None, elbows=None, x=100.0):
    """Человек с рамкой высотой height; wrists/elbows: {'left'|'right': (x, y, conf)}."""
    kps = [(0.0, 0.0, 0.0)] * 17
    kps = list(kps)
    wrists = wrists or {}
    elbows = elbows or {}
    for side, idx_w, idx_e in (('left', 9, 7), ('right', 10, 8)):
        if side in wrists:
            kps[idx_w] = wrists[side]
        if side in elbows:
            kps[idx_e] = elbows[side]
    return {'conf': 0.9, 'box': [x, 0.0, height * 0.4, float(height)], 'kps': kps}


class TestPickFrontHand:
    def test_nearest_person_wins(self):
        from picarpro.camera.pose_detector import pick_front_hand
        near = _person(400, wrists={'right': (300.0, 200.0, 0.9)})
        far = _person(150, wrists={'left': (500.0, 80.0, 0.99)})
        hand = pick_front_hand([far, near])
        assert hand['person'] == 1 and hand['x'] == 300.0     # ближний (выше рамка)

    def test_low_confidence_wrist_ignored(self):
        from picarpro.camera.pose_detector import pick_front_hand
        p = _person(300, wrists={'left': (100.0, 100.0, 0.1)})
        assert pick_front_hand([p]) is None

    def test_no_people(self):
        from picarpro.camera.pose_detector import pick_front_hand
        assert pick_front_hand([]) is None

    def test_raised_hand_preferred_within_person(self):
        from picarpro.camera.pose_detector import pick_front_hand
        p = _person(300,
                    wrists={'left': (100.0, 300.0, 0.95), 'right': (400.0, 100.0, 0.7)},
                    elbows={'left': (110.0, 250.0, 0.9), 'right': (390.0, 200.0, 0.9)})
        hand = pick_front_hand([p])
        assert hand['side'] == 'right' and hand['raised'] is True   # запястье выше локтя

    def test_confidence_breaks_tie_when_not_raised(self):
        from picarpro.camera.pose_detector import pick_front_hand
        p = _person(300, wrists={'left': (100.0, 300.0, 0.6), 'right': (400.0, 300.0, 0.9)})
        assert pick_front_hand([p])['side'] == 'right'

    def test_prefer_raised_can_be_disabled(self):
        from picarpro.camera.pose_detector import pick_front_hand
        p = _person(300,
                    wrists={'left': (100.0, 300.0, 0.95), 'right': (400.0, 100.0, 0.7)},
                    elbows={'left': (110.0, 250.0, 0.9), 'right': (390.0, 200.0, 0.9)})
        assert pick_front_hand([p], prefer_raised=False)['side'] == 'left'


def _nms_stub(boxes, scores, conf, nms):
    """Жадный NMS по IoU (замена cv2.dnn.NMSBoxes для тестов с фейковым cv2)."""
    order = sorted(range(len(boxes)), key=lambda i: -scores[i])
    keep = []
    for i in order:
        x, y, w, h = boxes[i]
        ok = True
        for j in keep:
            X, Y, W, H = boxes[j]
            ix = max(0, min(x + w, X + W) - max(x, X))
            iy = max(0, min(y + h, Y + H) - max(y, Y))
            inter = ix * iy
            if inter / (w * h + W * H - inter + 1e-9) > nms:
                ok = False
                break
        if ok:
            keep.append(i)
    return keep


class TestDecode:
    @pytest.fixture(autouse=True)
    def _nms(self, monkeypatch):
        import picarpro.camera.pose_detector as pd
        monkeypatch.setattr(pd.cv2, 'dnn', types.SimpleNamespace(NMSBoxes=_nms_stub),
                            raising=False)

    def _row(self, cx, cy, w, h, conf, wrists):
        row = np.zeros(56, dtype=np.float32)
        row[0], row[1], row[2], row[3] = cx, cy, w, h
        row[4] = conf
        for idx, (x, y, c) in wrists.items():
            row[5 + idx * 3: 5 + idx * 3 + 3] = (x, y, c)
        return row

    def test_decodes_letterboxed_coordinates(self):
        from picarpro.camera.pose_detector import PoseDetector
        # кадр 640x480 -> вход 320: r=0.5, поля dy=(320-240)/2=40
        row = self._row(160, 160, 100, 200, 0.9, {10: (170.0, 100.0, 0.8)})
        out = np.zeros((1, 56, 3), dtype=np.float32)
        out[0, :, 0] = row
        persons = PoseDetector._decode(out, 0.5, 0, 40, 640, 480)
        assert len(persons) == 1
        x, y, c = persons[0]['kps'][10]
        assert (round(x), round(y)) == (340, 120)       # (170/0.5, (100-40)/0.5)
        assert c == pytest.approx(0.8)
        # рамка: левый верх x=(160-50)/0.5=220, высота 200/0.5=400
        assert persons[0]['box'][0] == pytest.approx(220.0)
        assert persons[0]['box'][3] == pytest.approx(400.0)

    def test_low_confidence_filtered_and_nms(self):
        from picarpro.camera.pose_detector import PoseDetector
        a = self._row(160, 160, 100, 200, 0.9, {})
        dup = self._row(162, 161, 100, 200, 0.8, {})        # тот же человек
        weak = self._row(60, 60, 40, 80, 0.1, {})
        out = np.stack([a, dup, weak], axis=1)[None]        # (1, 56, 3)
        persons = PoseDetector._decode(out, 1.0, 0, 0, 640, 480)
        assert len(persons) == 1 and persons[0]['conf'] == pytest.approx(0.9)

    def test_accepts_transposed_output(self):
        from picarpro.camera.pose_detector import PoseDetector
        out = np.zeros((1, 3, 56), dtype=np.float32)        # (1, N, 56)
        out[0, 0] = self._row(100, 100, 50, 100, 0.9, {})
        assert len(PoseDetector._decode(out, 1.0, 0, 0, 640, 480)) == 1

    def test_empty(self):
        from picarpro.camera.pose_detector import PoseDetector
        assert PoseDetector._decode(np.zeros((1, 56, 5), np.float32), 1.0, 0, 0, 640, 480) == []


class TestModelLoading:
    def test_missing_model_not_loaded(self, monkeypatch, tmp_path):
        import picarpro.camera.pose_detector as pd
        monkeypatch.setattr(pd, 'POSE_MODEL_PATH', str(tmp_path / 'no.onnx'))
        monkeypatch.setattr(pd, 'POSE_MODEL_BUNDLED', str(tmp_path / 'no2.onnx'))
        d = pd.PoseDetector()
        assert d.load() is False and d.loaded is False
        assert d.infer(np.zeros((480, 640, 3), np.uint8)) == []

    def test_frame_skip(self):
        import picarpro.camera.pose_detector as pd
        d = pd.PoseDetector()
        n = sum(1 for _ in range(10) if d.should_infer())
        assert n == 10 // max(1, pd.POSE_FRAME_SKIP)

    def test_bundled_model_is_in_repo(self):
        import os
        import picarpro.camera.pose_detector as pd
        assert os.path.isfile(pd.POSE_MODEL_BUNDLED), 'models/yolo11n-pose.onnx'

    def test_load_falls_back_when_installed_wrong_input(self, monkeypatch,
                                                        tmp_path):
        """v1.6.12: модель в /home/pi/models с другим входом отбраковывается
        probe-прогоном, берётся комплектная (см. YOLO-детектор)."""
        import picarpro.camera.pose_detector as pd

        class FakeNet:
            def __init__(self, expects):
                self.expects = expects

            def setInput(self, blob):
                self.shape = tuple(blob.shape)

            def forward(self):
                if self.shape[2] != self.expects:
                    raise ValueError('Blob dimensions do not match')
                return np.zeros((1, 56, 2100), np.float32)

            def setPreferableBackend(self, *a):
                pass

            def setPreferableTarget(self, *a):
                pass

        a = tmp_path / 'a.onnx'
        b = tmp_path / 'b.onnx'
        a.write_bytes(b'x')
        b.write_bytes(b'x')
        nets = {str(a): FakeNet(640), str(b): FakeNet(320)}
        monkeypatch.setattr(pd, 'cv2', types.SimpleNamespace(
            dnn=types.SimpleNamespace(
                readNetFromONNX=lambda p: nets[p],
                DNN_BACKEND_OPENCV=0, DNN_TARGET_CPU=1)))
        monkeypatch.setattr(pd, 'POSE_MODEL_PATH', str(a))
        monkeypatch.setattr(pd, 'POSE_MODEL_BUNDLED', str(b))
        d = pd.PoseDetector()
        assert d.load() is True
        assert d.loaded and d.model_path == str(b)


class _FakeCam:
    def __init__(self):
        self.cv_thread = types.SimpleNamespace(
            on_hand_found=None, on_color_found=None, on_motion=None, on_line_found=None)
        self.modes = []

    def set_cv_mode(self, mode):
        self.modes.append(mode)


class TestHandTracking:
    """Слежение камерой за рукой: колёса стоят, углы — в пределах лимитов."""

    def _start(self, state):
        ctrl = state.autonomous
        cam = _FakeCam()
        ctrl.set_camera(cam)
        assert ctrl.start('trackHand')[0]
        assert wait_for(lambda: cam.cv_thread.on_hand_found is not None, 2.0)
        return ctrl, cam, cam.cv_thread.on_hand_found

    def _feed(self, cb, pos, n=8, area=300):
        for _ in range(n):
            cb(pos, area)
            time.sleep(0.03)

    def test_hand_right_and_up_moves_camera_right_and_up(self, clean_state):
        ctrl, cam, cb = self._start(clean_state)
        self._feed(cb, [560, 60])                       # правее центра и выше
        pan = clean_state.servos.get_angle(1)
        tilt = clean_state.servos.get_angle(2)
        assert pan < 90, f'pan={pan}: вправо = pan уменьшается'
        assert tilt > 90, f'tilt={tilt}: вверх = tilt растёт (как стрелки в UI)'
        ctrl.stop()

    def test_hand_left_and_down(self, clean_state):
        ctrl, cam, cb = self._start(clean_state)
        self._feed(cb, [80, 430])
        assert clean_state.servos.get_angle(1) > 90
        assert clean_state.servos.get_angle(2) < 90
        ctrl.stop()

    def test_wheels_never_move(self, clean_state):
        """Следим ТОЛЬКО камерой."""
        ctrl, cam, cb = self._start(clean_state)
        self._feed(cb, [560, 60], n=12)
        self._feed(cb, [80, 430], n=12)
        assert not clean_state.motors.is_moving
        assert clean_state.servos.get_angle(0) == 90     # руль прямо
        ctrl.stop()

    def test_tilt_never_leaves_limits(self, clean_state):
        from config import SERVO_LIMITS
        lo, hi = SERVO_LIMITS[2]['min'], SERVO_LIMITS[2]['max']
        ctrl, cam, cb = self._start(clean_state)
        self._feed(cb, [320, 5], n=40)                   # рука у верхнего края
        assert clean_state.servos.get_angle(2) == hi
        self._feed(cb, [320, 475], n=80)                 # рука у нижнего края
        assert clean_state.servos.get_angle(2) == lo
        ctrl.stop()

    def test_centered_hand_holds_still(self, clean_state):
        ctrl, cam, cb = self._start(clean_state)
        self._feed(cb, [322, 242], n=6)                  # в пределах мёртвой зоны
        assert clean_state.servos.get_angle(1) == 90
        assert clean_state.servos.get_angle(2) == 90
        ctrl.stop()

    def test_lost_hand_keeps_camera_and_mode(self, clean_state):
        ctrl, cam, cb = self._start(clean_state)
        self._feed(cb, [560, 240])
        pan = clean_state.servos.get_angle(1)
        for _ in range(6):
            cb([0, 0], 0)                                # руки нет
            time.sleep(0.03)
        assert clean_state.servos.get_angle(1) == pan    # камера на месте
        assert ctrl.is_active()
        ctrl.stop()

    def test_camera_recentred_on_stop(self, clean_state):
        ctrl, cam, cb = self._start(clean_state)
        self._feed(cb, [560, 60])
        ctrl.stop()
        assert wait_for(lambda: clean_state.servos.get_angle(1) == 90, 2.0)
        assert clean_state.servos.get_angle(2) == 90
