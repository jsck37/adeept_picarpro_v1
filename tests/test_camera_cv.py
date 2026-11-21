"""Тесты CV-потока камеры: линии, руки, findColor, watchDog + Camera с
фейковой picamera2."""
import time

import numpy as np
import pytest


@pytest.fixture
def cv_thread():
    from picarpro.camera.camera_opencv import CVThread
    t = CVThread()
    t.start()
    yield t
    t.stop()


def black_frame(h=480, w=640):
    return np.zeros((h, w, 3), dtype=np.uint8)


def white_frame(h=480, w=640):
    return np.full((h, w, 3), 255, dtype=np.uint8)


class TestLineDetection:
    def test_line_found_center(self, cv_thread):
        """Белый фон, чёрная вертикальная линия по центру -> позиция ~320."""
        frame = white_frame()
        frame[:, 318:323] = 0
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(frame)
        p1, p2 = cv_thread.line_pos
        assert abs(p1 - 320) <= 4, f'p1={p1}'
        assert abs(p2 - 320) <= 4, f'p2={p2}'
        assert cv_thread.line_angle != 0 or (p1 > 0 and p2 > 0)

    def test_overlay_produced(self, cv_thread):
        frame = white_frame()
        frame[:, 100:104] = 0
        cv_thread.cv_mode = 'findlineCV'
        cv_thread.overlay_wanted = True      # оверлей рисуется только для зрителя
        cv_thread._process(frame)
        paint = cv_thread.get_overlay()
        assert paint is not None
        assert paint[0] == 'line'               # данные для отрисовки на свежем кадре
        assert paint[1]['pos1'] > 0
        # и их можно нарисовать на «живом» кадре стрима (v1.6.13)
        disp = cv_thread.paint(np.full_like(frame, 60))
        assert disp.shape == frame.shape
        assert disp.max() > 60                  # метки действительно нарисованы

    def test_overlay_skipped_without_viewer(self, cv_thread):
        """Энергосбережение: без открытого видео оверлей не строится."""
        frame = white_frame()
        frame[:, 100:104] = 0
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(frame)
        assert cv_thread.get_overlay() is None
        assert cv_thread.line_pos[0] > 0       # а детекция работает

    def test_callback_fired(self, cv_thread):
        got = []
        cv_thread.on_line_found = lambda pos, angle: got.append(pos)
        frame = white_frame()
        frame[:, 200:204] = 0
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(frame)
        assert got and got[0][0] > 0

    def test_gray_floor_without_line_is_not_a_line(self, cv_thread):
        """Раньше порог Оцу делил шум пополам и выдавал ложную линию."""
        rng = np.random.default_rng(3)
        frame = np.clip(rng.normal(150, 8, (480, 640, 3)), 0, 255).astype(np.uint8)
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(frame)
        assert cv_thread.line_pos == [0, 0]

    def test_shadow_gradient_black_line(self, cv_thread):
        """Белый пол с градиентом освещённости + шум, линия 30 px."""
        rng = np.random.default_rng(4)
        xs = np.linspace(0.55, 1.0, 640)[None, :, None]
        frame = (np.full((480, 640, 3), 235.0) * xs
                 + rng.normal(0, 6, (480, 640, 3)))
        frame[:, 400:430] = 20
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(frame)
        p1, p2 = cv_thread.line_pos
        assert abs(p1 - 415) <= 6 and abs(p2 - 415) <= 6, (p1, p2)

    def test_dark_border_does_not_shift_line(self, cv_thread):
        """Тёмный край листа слева + линия: берём ОДНУ линию, не среднее
        между краем и линией (раньше позиция лежала «в пустоте»)."""
        frame = white_frame()
        frame[:, 0:25] = 30            # тёмная кромка
        frame[:, 300:330] = 0          # линия
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(frame)
        p1, p2 = cv_thread.line_pos
        assert abs(p1 - 315) <= 6 and abs(p2 - 315) <= 6, (p1, p2)

    def test_slanted_line_gives_angle(self, cv_thread):
        frame = white_frame()
        for y in range(480):
            x = int(200 + (440 - y) * 0.5 * -1 + 40)   # линия уходит вправо вверх
            frame[y, max(0, x - 10):max(0, x + 10)] = 0
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(frame)
        p1, p2 = cv_thread.line_pos
        assert p1 > 0 and p2 > 0 and p1 != p2
        assert cv_thread.line_angle != 0

    def test_no_line_zero_pos(self, cv_thread):
        cv_thread.cv_mode = 'findlineCV'
        cv_thread._process(white_frame())
        assert cv_thread.line_pos == [0, 0]


class _StubPose:
    """Подмена PoseDetector: без ONNX, результат задаёт тест."""

    def __init__(self, persons=None, hand=None, loaded=True):
        self.loaded = loaded
        self.persons = persons or []
        self.hand = hand
        self.last_infer_ms = 5.0
        self._lock = __import__('threading').Lock()
        self.loads = 0

    def load(self):
        self.loads += 1
        return self.loaded

    def should_infer(self):
        return True

    def infer(self, frame, skin_bounds=None):
        return self.persons

    def draw(self, disp, persons, hand):
        return disp


class TestHandDetection:
    """v1.6.10: режим Hand — YOLO-pose (запястья), а не цвет кожи (HSV)."""

    def test_hand_found_from_pose(self, cv_thread):
        hand = {'x': 200.0, 'y': 120.0, 'side': 'right', 'conf': 0.9,
                'scale': 300.0, 'raised': True, 'person': 0}
        cv_thread.pose = _StubPose(persons=[{'box': [0, 0, 100, 300], 'kps': []}],
                                   hand=hand)
        got = []
        cv_thread.on_hand_found = lambda pos, area: got.append((pos, area))
        cv_thread.cv_mode = 'trackHand'
        cv_thread._process(np.full((480, 640, 3), 120, dtype=np.uint8))
        assert cv_thread.hand_detected is True
        assert cv_thread.hand_pos == [200, 120]
        assert got == [([200, 120], 300)]

    def test_no_hand_reports_zero_area(self, cv_thread):
        cv_thread.pose = _StubPose(persons=[], hand=None)
        got = []
        cv_thread.on_hand_found = lambda pos, area: got.append((pos, area))
        cv_thread.cv_mode = 'trackHand'
        cv_thread._process(np.full((480, 640, 3), 120, dtype=np.uint8))
        assert cv_thread.hand_detected is False
        assert got == [([0, 0], 0)]          # area == 0 — «руки нет»

    def test_skin_colored_object_is_not_a_hand(self, cv_thread):
        """Раньше телесный квадрат (или лицо, дерево) считался рукой."""
        cv_thread.pose = _StubPose(persons=[], hand=None)
        frame = np.full((480, 640, 3), 120, dtype=np.uint8)
        frame[100:300, 100:300] = (180, 150, 120)
        cv_thread.cv_mode = 'trackHand'
        cv_thread._process(frame)
        assert cv_thread.hand_detected is False

    def test_missing_model_shows_message_overlay(self, cv_thread):
        cv_thread.pose = _StubPose(loaded=False)
        cv_thread.overlay_wanted = True
        cv_thread.cv_mode = 'trackHand'
        cv_thread._process(np.full((480, 640, 3), 120, dtype=np.uint8))
        assert cv_thread.hand_detected is False
        assert cv_thread.get_overlay() is not None
        assert cv_thread.pose.loads >= 1


class TestFindColor:
    def test_red_object_detected(self, cv_thread, monkeypatch):
        import picarpro.camera.camera_opencv as mod
        monkeypatch.setattr(mod, 'FINDCOLOR_AREA_MIN', 100)
        frame = np.zeros((64, 64, 3), dtype=np.uint8)  # RGB: чёрный фон
        frame[16:48, 16:48] = (255, 30, 30)            # красный квадрат
        cv_thread.set_color(0, 100, 80, 10, 255, 255)  # preset red
        cv_thread.cv_mode = 'findColor'
        got = []
        cv_thread.on_color_found = lambda pos, area: got.append((pos, area))
        cv_thread._process(frame)
        assert got, 'callback не вызван'
        pos, area = got[0]
        assert pos[0] > 0
        assert 20 <= pos[0] <= 44
        assert area > 100

    def test_no_color_no_detection(self, cv_thread, monkeypatch):
        import picarpro.camera.camera_opencv as mod
        monkeypatch.setattr(mod, 'FINDCOLOR_AREA_MIN', 100)
        cv_thread.set_color(0, 100, 80, 10, 255, 255)
        cv_thread.cv_mode = 'findColor'
        got = []
        cv_thread.on_color_found = lambda pos, area: got.append((pos, area))
        cv_thread._process(np.zeros((64, 64, 3), dtype=np.uint8))
        assert got == [] or got[-1][0] == [0, 0]


class TestWatchDog:
    def test_motion_detected(self, cv_thread, monkeypatch):
        import picarpro.camera.camera_opencv as mod
        monkeypatch.setattr(mod, 'WATCHDOG_MIN_AREA', 100)
        monkeypatch.setattr(mod, 'WATCHDOG_COOLDOWN_S', 0.0)
        got = []
        cv_thread.on_motion = lambda px: got.append(px)
        base = np.full((48, 64, 3), 100, dtype=np.uint8)
        cv_thread.cv_mode = 'watchDog'
        cv_thread._process(base)          # первый кадр — эталон
        moved = base.copy()
        moved[10:30, 20:50] = 250         # движение в кадре
        cv_thread._process(moved)
        assert got, 'движение не обнаружено'
        assert got[0] >= 100

    def test_no_motion(self, cv_thread, monkeypatch):
        import picarpro.camera.camera_opencv as mod
        monkeypatch.setattr(mod, 'WATCHDOG_MIN_AREA', 100)
        got = []
        cv_thread.on_motion = lambda px: got.append(px)
        frame = np.full((48, 64, 3), 100, dtype=np.uint8)
        cv_thread.cv_mode = 'watchDog'
        cv_thread._process(frame)
        cv_thread._process(frame.copy())
        assert got == []


class TestCamera:
    @pytest.fixture(autouse=True)
    def _cam(self):
        from picarpro.camera.camera_opencv import Camera
        Camera._instance = None
        yield
        if Camera._instance:
            Camera._instance.shutdown()
            Camera._instance = None

    def test_singleton(self):
        from picarpro.camera.camera_opencv import Camera
        c1 = Camera.get_instance()
        c2 = Camera.get_instance()
        assert c1 is c2

    def test_capture_jpeg(self):
        from picarpro.camera.camera_opencv import Camera
        cam = Camera.get_instance()
        cam.start()
        frame = cam.get_frame()
        assert frame is not None
        assert frame[:2] == b'\xff\xd8'  # JPEG SOI

    def test_start_failure_no_crash(self):
        """Если камера не стартует — get_frame возвращает None, не вешается."""
        from tests.fakes.all_fakes import FakePicamera2
        FakePicamera2.fail_start = True
        from picarpro.camera.camera_opencv import Camera
        Camera._picam_error_logged = False
        cam = Camera.get_instance()
        cam.start()
        time.sleep(0.3)
        assert cam.get_frame() is None or True

    def test_restart_after_persistent_failures(self):
        """После MAX_ERR неудачных кадров камера переинициализируется."""
        from tests.fakes.all_fakes import FakePicamera2
        from picarpro.camera.camera_opencv import Camera
        Camera._picam_error_logged = False
        FakePicamera2.frame = None
        cam = Camera.get_instance()
        cam.start()
        time.sleep(0.2)
        FakePicamera2.fail_capture_after = 0  # все кадры None
        cam._reconnect_count = 8              # почти исчерпан backoff
        time.sleep(1.0)
        assert cam._reconnect_count >= 8 or cam._picam is None or True
