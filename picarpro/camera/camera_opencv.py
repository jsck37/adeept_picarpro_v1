import glob
import math
import os
import shutil
import subprocess
import threading
import time

from picarpro.logger import logger
from picarpro.camera.yolo_detector import YoloDetector
from picarpro.camera import vision
from config import (
    CAMERA_RESOLUTION, CAMERA_FPS, CAMERA_JPEG_QUALITY,
    CAMERA_IDLE_INTERVAL_S, CAMERA_SLEEP_AFTER_S, CV_PROC_SCALE,
    CAMERA_STREAM_FPS, CAMERA_HW_MJPEG,
    CAMERA_FLIP_HORIZONTAL, CAMERA_FLIP_VERTICAL,
    CV_LINE_POS_1, CV_LINE_POS_2,
    FINDCOLOR_AREA_MIN, WATCHDOG_THRESHOLD, WATCHDOG_MIN_AREA,
    WATCHDOG_COOLDOWN_S,
    CAMERA_IDLE_BACKOFF_CPU_PCT, CAMERA_IDLE_BACKOFF_MAX,
    CAMERA_SLEEP_SKIP_CPU_PCT,
)

try:
    from picamera2 import Picamera2
    _HAS_PICAM = True
except ImportError:
    _HAS_PICAM = False
    Picamera2 = None

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False
    cv2 = None
    np = None

# Источники камеры: 'picamera2' (CSI через libcamera) или 'v4l2' (USB/сторонняя
# через OpenCV V4L2). Picamera2 формат "RGB888" отдаёт массив в порядке BGR —
# как и OpenCV VideoCapture. Оба источника нормализуются к настоящему RGB сразу
# после захвата (см. _capture_loop); оверлеи CV хранятся в BGR — в том виде,
# в котором их ждёт cv2.imencode (без двойной конвертации на каждый кадр).
HW_MJPEG_STARTUP_TIMEOUT_S = 3.0   # нет кадров от аппаратного энкодера — откат на cv2
CAMERA_DIAG_INTERVAL_S = 60.0   # диагностика неудач инициализации — не чаще

CV_NONE = 'none'
CV_LINE = 'findlineCV'
CV_HAND = 'trackHand'
CV_FINDCOLOR = 'findColor'
CV_WATCHDOG = 'watchDog'
CV_YOLO = 'yoloDetect'      # нейросетевое детектирование объектов (ONNX, cv2.dnn)

HAND_AREA_MIN = 800          # px² на полном кадре
LINE_BAND_PAD = 6            # запас строк вокруг опорных линий CV_LINE_POS_*

DEFAULT_HAND_LOW = np.array([0, 30, 50], dtype=np.uint8) if _HAS_CV2 else None
DEFAULT_HAND_HIGH = np.array([25, 255, 255], dtype=np.uint8) if _HAS_CV2 else None


def _system_load_pct():
    """Загрузка системы 0-100 (max из CPU и RAM) или None, если не удалось.

    v1.6.4: основа адаптивного энергосбережения — «мало простоя процессора
    и памяти» означает, что робот занят (стрим/CV/навигация), и в этом
    состоянии энергосбережение камеры должно вести себя иначе.
    """
    try:
        from picarpro.utils.system_info import SystemInfo
        cpu = SystemInfo.get_cpu_usage() or 0.0
        ram = SystemInfo.get_ram_info() or {}
        mem_pct = ram.get('percent') or 0.0
        return max(float(cpu), float(mem_pct))
    except Exception:
        return None


class CVThread(threading.Thread):
    """Поток компьютерного зрения.

    Энергосбережение:
    * поток спит на Event без периодических пробуждений;
    * цвет/рука/охрана обрабатываются на уменьшенном кадре (CV_PROC_SCALE);
    * линия — только в полосе между опорными строками, а не на всём кадре;
    * картинка-оверлей рисуется, лишь когда её кто-то смотрит (overlay_wanted).
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._flag = threading.Event()
        self._running = True
        self._frame = None
        self._frame_lock = threading.Lock()
        self.cv_mode = CV_NONE
        self.line_pos = [0, 0]
        self.line_angle = 0
        self.on_line_found = None
        self.hand_pos = [0, 0]
        self.hand_detected = False
        self.hand_area = 0
        self.on_hand_found = None
        self._hand_lock = threading.Lock()
        self._hand_low = DEFAULT_HAND_LOW
        self._hand_high = DEFAULT_HAND_HIGH
        self.on_color_found = None
        self.on_motion = None
        self._prev_gray = None
        self._last_motion_ts = 0.0
        self.overlay_frame = None          # BGR (или None)
        self._overlay_lock = threading.Lock()
        self.overlay_wanted = False
        self._yolo_err_drawn = False
        self.yolo = YoloDetector()
        self.yolo._objects_lock = threading.Lock()
        self.on_yolo_found = None   # callback(objects)

    # ---- жизненный цикл ---------------------------------------------------
    def run(self):
        while self._running:
            self._flag.wait()          # stop() тоже взводит флаг
            if not self._running:
                break
            self._flag.clear()
            with self._frame_lock:
                frame = self._frame
                self._frame = None     # не обрабатываем один кадр дважды
            if frame is None:
                continue
            try:
                self._process(frame)
            except Exception as e:
                logger.error(f'[CV] error: {e}')

    def submit_frame(self, frame):
        """Передать кадр (RGB) в CV-поток без копирования: ни один режим не
        мутирует входной кадр, а источник отдаёт новый буфер на каждый захват.
        Если поток ещё занят прошлым кадром, новый заменяет ожидающий."""
        with self._frame_lock:
            self._frame = frame
        self._flag.set()

    def get_overlay(self):
        """Оверлей (BGR) или None. Копия не нужна: массив после публикации
        никто не изменяет — каждый кадр создаёт новый."""
        with self._overlay_lock:
            return self.overlay_frame

    def _set_overlay(self, bgr):
        with self._overlay_lock:
            self.overlay_frame = bgr

    def clear_overlay(self):
        self._set_overlay(None)
        self._prev_gray = None
        self._yolo_err_drawn = False

    def stop(self):
        self._running = False
        self._flag.set()

    # ---- цвет -------------------------------------------------------------
    def set_hand_color(self, h_low, s_low, v_low, h_high, s_high, v_high):
        if not _HAS_CV2:
            return
        with self._hand_lock:
            self._hand_low = np.array([h_low, s_low, v_low], dtype=np.uint8)
            self._hand_high = np.array([h_high, s_high, v_high], dtype=np.uint8)

    set_color = set_hand_color  # общий HSV-фильтр для findColor/trackHand

    def get_hand_color(self):
        with self._hand_lock:
            if self._hand_low is not None:
                return (list(self._hand_low), list(self._hand_high))
            return None

    get_color = get_hand_color

    def _color_bounds(self):
        with self._hand_lock:
            return self._hand_low.copy(), self._hand_high.copy()

    # ---- диспетчер --------------------------------------------------------
    def _process(self, frame):
        if not _HAS_CV2:
            return
        handler = {
            CV_LINE: self._find_line,
            CV_HAND: self._find_hand,
            CV_FINDCOLOR: self._find_color,
            CV_WATCHDOG: self._watch_dog,
            CV_YOLO: self._yolo_detect,
        }.get(self.cv_mode)
        if handler:
            handler(frame)

    @staticmethod
    def _to_bgr(frame):
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    @staticmethod
    def _scaled_contour(c, inv):
        return (c.astype(np.float32) * inv).astype(np.int32)

    # ---- линия ------------------------------------------------------------
    def _find_line(self, frame):
        h, w = frame.shape[:2]
        y1 = min(CV_LINE_POS_1, h - 1)
        y2 = min(CV_LINE_POS_2, h - 1)
        top = max(0, min(y1, y2) - LINE_BAND_PAD)
        bot = min(h, max(y1, y2) + LINE_BAND_PAD + 1)
        # Otsu только по полосе вокруг опорных строк: в десятки раз меньше
        # пикселей, и порог подстраивается под освещённость именно там,
        # где ищется линия
        band = cv2.cvtColor(frame[top:bot], cv2.COLOR_RGB2GRAY)
        band = cv2.GaussianBlur(band, (5, 5), 0)
        _, binary = cv2.threshold(
            band, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        idx1 = np.nonzero(binary[y1 - top] > 0)[0]
        idx2 = np.nonzero(binary[y2 - top] > 0)[0]
        pos1 = int(np.mean(idx1)) if len(idx1) > 0 else 0
        pos2 = int(np.mean(idx2)) if len(idx2) > 0 else 0
        self.line_pos = [pos1, pos2]
        self.line_angle = (
            math.degrees(math.atan2(pos2 - pos1, CV_LINE_POS_1 - CV_LINE_POS_2))
            if pos1 > 0 and pos2 > 0 else 0)
        if self.overlay_wanted:
            self._draw_line_overlay(binary, top, w, h, pos1, pos2)
        if self.on_line_found:
            self.on_line_found(self.line_pos, self.line_angle)

    def _draw_line_overlay(self, band_bin, top, w, h, pos1, pos2):
        disp = np.zeros((h, w, 3), dtype=np.uint8)
        disp[top:top + band_bin.shape[0]] = cv2.cvtColor(
            band_bin, cv2.COLOR_GRAY2BGR)
        cv2.line(disp, (0, CV_LINE_POS_1), (w, CV_LINE_POS_1), (0, 255, 0), 1)
        cv2.line(disp, (0, CV_LINE_POS_2), (w, CV_LINE_POS_2), (0, 255, 0), 1)
        if pos1 > 0 and pos2 > 0:
            cv2.line(disp, (pos2, CV_LINE_POS_2), (pos1, CV_LINE_POS_1),
                     (0, 0, 255), 2)
            mx = (pos1 + pos2) // 2
            my = (CV_LINE_POS_1 + CV_LINE_POS_2) // 2
            cv2.circle(disp, (mx, my), 6, (0, 0, 255), -1)
        if pos1 > 0:
            cv2.circle(disp, (pos1, CV_LINE_POS_1), 5, (0, 255, 0), -1)
        if pos2 > 0:
            cv2.circle(disp, (pos2, CV_LINE_POS_2), 5, (0, 255, 0), -1)
        tracking = pos1 > 0 or pos2 > 0
        cv2.putText(disp, 'TRACKING' if tracking else 'SEARCHING', (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if tracking else (0, 0, 255), 2)
        self._set_overlay(disp)

    # ---- общий поиск пятна по HSV (рука/цвет) ------------------------------
    def _blob(self, frame, min_area, morph):
        """(cx, cy, area, contour_полного_кадра) или None. Работает на
        уменьшенном кадре; координаты/площадь пересчитываются в полный."""
        s = CV_PROC_SCALE if CV_PROC_SCALE < 0.999 else 1.0
        small = vision.downscale(frame, s)
        low, high = self._color_bounds()
        mask = vision.hsv_mask(small, low, high)
        mask = morph(mask)
        blob = vision.largest_blob(mask, min_area * s * s)
        if blob is None:
            return None
        cx, cy, area, c = blob
        inv = 1.0 / s
        return (int(cx * inv), int(cy * inv), int(area * inv * inv),
                self._scaled_contour(c, inv) if inv != 1.0 else c)

    def _find_hand(self, frame):
        def morph(mask):
            k_large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_large, iterations=2)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_small, iterations=2)
            mask = cv2.GaussianBlur(mask, (5, 5), 0)
            return cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)[1]

        blob = self._blob(frame, HAND_AREA_MIN, morph)
        self.hand_detected = blob is not None
        if blob:
            cx, cy, area, c = blob
            self.hand_pos = [cx, cy]
            self.hand_area = area
        if self.overlay_wanted:
            self._draw_target_overlay(frame, blob, 'LOCK', cross=True)
        if self.on_hand_found:
            if self.hand_detected:
                if self.on_hand_found(self.hand_pos, self.hand_area):
                    self.hand_detected = False    # «встряхивание» — выходим
            else:
                self.on_hand_found([0, 0], 0)

    def _find_color(self, frame):
        """Следование за цветным объектом — порт findColor из v1/v2."""
        def morph(mask):
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
            return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

        blob = self._blob(frame, FINDCOLOR_AREA_MIN, morph)
        if self.overlay_wanted:
            self._draw_target_overlay(frame, blob, 'COLOR', cross=False)
        if self.on_color_found:
            if blob:
                self.on_color_found([blob[0], blob[1]], blob[2])
            else:
                self.on_color_found([0, 0], 0)

    def _draw_target_overlay(self, frame, blob, label, cross):
        disp = self._to_bgr(frame)
        if blob is None:
            h, w = disp.shape[:2]
            if cross:
                cx_c, cy_c = w // 2, h // 2
                cv2.line(disp, (cx_c - 20, cy_c), (cx_c + 20, cy_c), (0, 255, 0), 1)
                cv2.line(disp, (cx_c, cy_c - 20), (cx_c, cy_c + 20), (0, 255, 0), 1)
                cv2.circle(disp, (cx_c, cy_c), 15, (0, 255, 0), 1)
            cv2.putText(disp, 'SEARCHING...', (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        else:
            cx, cy, _area, c = blob
            cv2.drawContours(disp, [c], -1, (0, 255, 0), 2)
            color = (0, 0, 255)
            if cross:
                cv2.line(disp, (cx - 25, cy), (cx + 25, cy), color, 2)
                cv2.line(disp, (cx, cy - 25), (cx, cy + 25), color, 2)
                cv2.circle(disp, (cx, cy), 18, color, 2)
                cv2.circle(disp, (cx, cy), 3, color, -1)
            else:
                cv2.circle(disp, (cx, cy), 6, color, -1)
            cv2.putText(disp, f'{label} ({cx},{cy})', (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 0, 255) if cross else (0, 255, 0), 2)
        self._set_overlay(disp)

    # ---- охрана -----------------------------------------------------------
    def _watch_dog(self, frame):
        """Детектор движения — порт Camera_WatchDog из v1/v2: порог +
        минимальная площадь + кулдаун WATCHDOG_COOLDOWN_S."""
        s = CV_PROC_SCALE if CV_PROC_SCALE < 0.999 else 1.0
        small = vision.downscale(frame, s)
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        prev = self._prev_gray
        self._prev_gray = gray
        if prev is None or prev.shape != gray.shape:
            return
        diff = cv2.absdiff(gray, prev)
        _, motion_mask = cv2.threshold(diff, WATCHDOG_THRESHOLD, 255,
                                       cv2.THRESH_BINARY)
        # пересчёт в пиксели полного кадра: порог WATCHDOG_MIN_AREA задан в них
        motion_pixels = int(cv2.countNonZero(motion_mask) / (s * s))
        alarm = motion_pixels >= WATCHDOG_MIN_AREA
        if self.overlay_wanted:
            disp = self._to_bgr(frame)
            cv2.putText(disp, f'MOTION {motion_pixels}px' if alarm else 'CLEAR',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 0, 255) if alarm else (0, 255, 0), 2)
            self._set_overlay(disp)
        if alarm:
            now = time.monotonic()
            if now - self._last_motion_ts >= WATCHDOG_COOLDOWN_S:
                self._last_motion_ts = now
                if self.on_motion:
                    self.on_motion(motion_pixels)

    # ---- YOLO -------------------------------------------------------------
    def _yolo_detect(self, frame):
        """Детектирование объектов YOLO (ONNX через cv2.dnn); инференс — только
        каждый YOLO_FRAME_SKIP-й кадр, между ними показывается прошлый итог."""
        if not self.yolo.loaded:
            if not self.yolo.load():
                # «модель не найдена» рисуем ОДИН раз
                if self.overlay_wanted and not self._yolo_err_drawn:
                    bgr = self._to_bgr(frame)
                    cv2.putText(bgr, 'YOLO MODEL NOT FOUND', (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    self._set_overlay(bgr)
                    self._yolo_err_drawn = True
                return
        if not self.yolo.should_infer():
            objs = list(self.yolo.objects)
        else:
            objs = self.yolo.infer(frame)
        if self.overlay_wanted:
            bgr = self._to_bgr(frame)
            self.yolo.draw(bgr, objs)
            cv2.putText(bgr, f'YOLO-{self.yolo.task} {len(objs)} obj '
                             f'({self.yolo.last_infer_ms:.0f} ms)',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 255, 255), 2)
            self._set_overlay(bgr)
        if self.on_yolo_found and objs:
            try:
                self.on_yolo_found(objs)
            except Exception as e:
                logger.warning(f'[YOLO] callback error: {e}')


class Camera:
    _instance_lock = threading.Lock()
    _instance = None
    _picam_error_logged = False
    _last_diag_ts = 0.0   # классовое поле: троттлинг диагностики для всех инстансов

    @classmethod
    def get_instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self.cv_thread = CVThread()
        self.cv_thread.start()
        self._picam = None
        self._cap = None            # OpenCV VideoCapture (USB/V4L2)
        self._source = None         # 'picamera2' | 'v4l2' | None
        self._picam_lock = threading.Lock()
        self._reconnect_count = 0
        self._running = True
        self._thread = None
        # MJPEG-кадр: счётчик + Condition. Раньше Event оставался взведённым
        # навсегда, и /video_feed крутил цикл вхолостую, отправляя один и тот
        # же кадр с максимальной скоростью (100% CPU и трафик впустую).
        self._frame = None
        self._frame_id = 0
        self._frame_cond = threading.Condition()
        # сырой RGB-кадр (ArUco, color_scan) — по тому же принципу
        self._last_raw = None
        self._raw_id = 0
        self._raw_cond = threading.Condition()
        # Потребители кадров: JPEG кодируется только при зрителях, CV —
        # только при включённом режиме, сырые кадры — при raw_demand.
        self._demand_lock = threading.Lock()
        self._stream_clients = 0
        self._raw_demand = 0
        self._wake = threading.Event()   # разбудить усыплённую камеру
        self._asleep = False
        self._hw_ok = bool(CAMERA_HW_MJPEG)   # False после первой неудачи
        self._hw_active = False
        self._hw_encoder = None
        self._hw_started_ts = 0.0
        self._hw_frames = 0
        self._last_jpeg_ts = 0.0

    @classmethod
    def _log_init_failure(cls, err):
        """Лог неудачи инициализации с диагностикой — не чаще
        CAMERA_DIAG_INTERVAL_S (v1.4.0). Раньше ошибка «No camera number 0
        found» печаталась каждую секунду без единой подсказки."""
        now = time.monotonic()
        # 0.0 = «ещё не логировали»: monotonic() на свежезагруженной Pi < 60 с,
        # и сравнение с нулём подавляло самую первую диагностику
        if cls._last_diag_ts and now - cls._last_diag_ts < CAMERA_DIAG_INTERVAL_S:
            return
        cls._last_diag_ts = now
        logger.error(f'[Camera] init failed: {err}')
        for line in cls._collect_diagnostics():
            logger.error(f'[Camera] diag: {line}')
        logger.error(
            '[Camera] hints (по порядку): '
            '1) если config.txt правили после загрузки — сначала sudo reboot; '
            '2) переткните шлейф CSI на обоих концах до щелчка фиксатора: '
            'металлические контакты ленты — со стороны, противоположенной '
            'подвижной заслонке разъёма (и на плате Pi, и на плате камеры), '
            'синяя сторона — со стороны заслонки; '
            '3) проверка железа: rpicam-hello --list-cameras; '
            '4) если авто-детект не сработал — добавьте в /boot/firmware/'
            'config.txt строку dtoverlay=ov5647 (камера V1) или '
            'dtoverlay=imx219 (V2) и reboot; '
            '5) USB-камера подхватится автоматически как /dev/video0.')

    @staticmethod
    def _uptime_seconds():
        try:
            with open('/proc/uptime') as f:
                return float(f.read().split()[0])
        except Exception:
            return None

    @staticmethod
    def _collect_diagnostics():
        """Что видит система: libcamera, V4L2-устройства, config.txt, dmesg."""
        lines = []
        # 1) libcamera — есть ли CSI-камера на уровне стека
        for tool in ('rpicam-hello', 'libcamera-hello'):
            if shutil.which(tool):
                try:
                    r = subprocess.run(
                        [tool, '--list-cameras'], capture_output=True,
                        text=True, timeout=15)
                    out = ((r.stdout or '') + (r.stderr or '')).strip()
                    lines.append(f'{tool}: {out[:300] if out else "(пусто)"}')
                except Exception as e:
                    lines.append(f'{tool}: сбой ({e})')
                break
        # 2) V4L2-устройства
        try:
            devs = sorted(glob.glob('/dev/video*'))
            lines.append('/dev/video*: '
                         + (', '.join(devs) if devs else 'нет'))
        except Exception:
            pass
        # 3) config.txt — только файл, который реально читает прошивка
        #    (v1.4.1: на Trixie это /boot/firmware/config.txt; рядом может
        #    лежать отдельный /boot/config.txt — он НЕ читается и раньше
        #    сбивал подсказкой «нет строк камеры»)
        canonical = None
        for cfg in ('/boot/firmware/config.txt', '/boot/config.txt'):
            if os.path.exists(cfg):
                canonical = cfg
                break
        if canonical:
            try:
                with open(canonical) as f:
                    cam = [ln.strip() for ln in f
                           if 'camera' in ln.lower()
                           or 'imx219' in ln.lower() or 'imx477' in ln.lower()
                           or 'imx708' in ln.lower() or 'ov5647' in ln.lower()]
                lines.append(f'{canonical}: '
                             + ('; '.join(cam[:6]) if cam
                                else 'нет строк камеры (нужно '
                                     'camera_auto_detect=1)'))
                # v1.4.1: файл правили ПОСЛЕ загрузки — камера появится
                # только после reboot (частая причина «добавили строку,
                # а rpicam-hello всё равно No cameras available»)
                uptime = Camera._uptime_seconds()
                if uptime is not None:
                    boot_ts = time.time() - uptime
                    if os.path.getmtime(canonical) > boot_ts + 5:
                        lines.append(
                            'config.txt изменён ПОСЛЕ последней загрузки — '
                            'сначала sudo reboot, затем проверьте снова')
            except Exception:
                pass
            for other in ('/boot/firmware/config.txt', '/boot/config.txt'):
                if other == canonical or not os.path.exists(other):
                    continue
                try:
                    same = os.path.islink(other) \
                        or os.path.samefile(other, canonical)
                except Exception:
                    same = True
                if not same:
                    lines.append(
                        f'{other}: отдельный файл, прошивкой НЕ читается '
                        f'(рабочий файл — {canonical})')
        # 4) dmesg — ошибки CSI-шины (unicam) и сенсора
        try:
            r = subprocess.run(
                'dmesg | grep -iE "unicam|imx219|imx477|imx708|ov5647" '
                '| tail -4',
                shell=True, capture_output=True, text=True, timeout=10)
            out = (r.stdout or '').strip()
            if out:
                lines.append('dmesg: '
                             + ' | '.join(x.strip() for x in out.splitlines()))
            elif r.returncode == 0:
                # grep ничего не нашёл: сенсор вообще не опрашивался при
                # загрузке — не физическое подключение, так reboot
                lines.append(
                    'dmesg: нет упоминаний unicam/сенсора — сенсор не '
                    'опрашивался при загрузке (шлейф не подключён или '
                    'не до щелчка, либо reboot после правки config.txt '
                    'ещё не делали)')
        except Exception:
            pass
        return lines

    @staticmethod
    def _find_v4l2_device():
        """Первый /dev/videoN (обычно 0) — для USB-камеры."""
        for dev in sorted(glob.glob('/dev/video*')):
            try:
                return int(dev.rsplit('video', 1)[1])
            except (IndexError, ValueError):
                continue
        return None

    def _init_camera(self):
        with self._picam_lock:
            if self._picam is not None or self._cap is not None:
                return
            # --- путь 1: Picamera2 (CSI-камера через libcamera) ---------
            if _HAS_PICAM:
                try:
                    self._picam = Picamera2()
                    cfg_kwargs = {}
                    if CAMERA_FLIP_HORIZONTAL or CAMERA_FLIP_VERTICAL:
                        # переворот — через Transform в конфигурации; метода
                        # set_control('flip_h') в picamera2 нет, и прежний код
                        # ронял инициализацию камеры целиком при включённом флипе
                        try:
                            from libcamera import Transform
                            cfg_kwargs['transform'] = Transform(
                                hflip=int(bool(CAMERA_FLIP_HORIZONTAL)),
                                vflip=int(bool(CAMERA_FLIP_VERTICAL)))
                        except Exception as e:
                            logger.warning(f'[Camera] flip unavailable: {e}')
                    cfg = self._picam.create_preview_configuration(
                        main={'size': CAMERA_RESOLUTION, 'format': 'RGB888'},
                        **cfg_kwargs)
                    self._picam.configure(cfg)
                    try:
                        self._picam.set_controls({
                            'FrameRate': CAMERA_FPS,
                            'AeEnable': True, 'AwbEnable': True,
                            'NoiseReductionMode': 1,
                        })
                    except Exception:
                        pass
                    self._picam.start()
                    self._source = 'picamera2'
                    self._asleep = False
                    self._reconnect_count = 0
                    logger.info(f'[Camera] OK (picamera2/CSI) — '
                                f'{CAMERA_RESOLUTION} @ {CAMERA_FPS}fps')
                    return
                except Exception as e:
                    self._picam = None
                    self._log_init_failure(e)
            else:
                if not Camera._picam_error_logged:
                    logger.error('[Camera] picamera2 not installed')
                    Camera._picam_error_logged = True
            # --- путь 2: USB/совместимая камера через OpenCV V4L2 -------
            if _HAS_CV2 and hasattr(cv2, 'VideoCapture'):
                idx = self._find_v4l2_device()
                if idx is not None:
                    try:
                        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RESOLUTION[0])
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RESOLUTION[1])
                        cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
                        if hasattr(cv2, 'VideoWriter_fourcc'):
                            # MJPG: 640x480@15 по USB 2.0 без потерь fps
                            cap.set(cv2.CAP_PROP_FOURCC,
                                    cv2.VideoWriter_fourcc(*'MJPG'))
                        if cap.isOpened():
                            self._cap = cap
                            self._source = 'v4l2'
                            self._reconnect_count = 0
                            logger.info(f'[Camera] OK (USB/V4L2 /dev/video{idx})'
                                        f' — {CAMERA_RESOLUTION} @ {CAMERA_FPS}fps')
                            return
                        try:
                            cap.release()
                        except Exception:
                            pass
                    except Exception as e:
                        self._log_init_failure(e)
            # оба пути не удались — retry цикл попробует снова

    def _restart_camera(self):
        self._hw_active = False   # камера пересоздаётся — энкодер умирает вместе с ней
        with self._picam_lock:
            self._reconnect_count += 1
            backoff = min(5.0, 0.5 * (2 ** min(self._reconnect_count, 4)))
            if self._picam:
                for fn in (self._picam.stop, self._picam.close):
                    try:
                        fn()
                    except Exception:
                        pass
                self._picam = None
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            self._source = None
        time.sleep(backoff)
        self._init_camera()

    # ---- учёт потребителей кадров ------------------------------------------
    def _demand_changed(self):
        self.cv_thread.overlay_wanted = self._stream_clients > 0
        self._wake.set()

    def stream_open(self):
        """Открыть MJPEG-стрим (/video_feed) — держит камеру в боевом ритме."""
        with self._demand_lock:
            self._stream_clients += 1
        self._demand_changed()

    def stream_close(self):
        with self._demand_lock:
            self._stream_clients = max(0, self._stream_clients - 1)
        self._demand_changed()

    def raw_demand_open(self):
        """Регистрация потребителя сырых кадров (aruco_nav, color_scan)."""
        with self._demand_lock:
            self._raw_demand += 1
        self._demand_changed()

    def raw_demand_close(self):
        with self._demand_lock:
            self._raw_demand = max(0, self._raw_demand - 1)

    def _has_demand(self):
        with self._demand_lock:
            if self._stream_clients + self._raw_demand > 0:
                return True
        return self.cv_thread.cv_mode != CV_NONE

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    # ---- аппаратный MJPEG (picamera2) -----------------------------------------
    def _on_hw_frame(self, jpeg):
        self._hw_frames += 1
        with self._frame_cond:
            self._frame = bytes(jpeg)
            self._frame_id += 1
            self._frame_cond.notify_all()

    def _hw_start(self, picam):
        try:
            from picamera2.encoders import MJPEGEncoder
            from picamera2.outputs import FileOutput
            from picarpro.camera.hw_mjpeg import FrameSink
            self._hw_frames = 0
            self._hw_encoder = MJPEGEncoder()
            picam.start_encoder(self._hw_encoder, FileOutput(FrameSink(self._on_hw_frame)))
            self._hw_active = True
            self._hw_started_ts = time.monotonic()
            logger.info('[Camera] hardware MJPEG encoder ON')
        except Exception as e:
            self._hw_ok = False
            self._hw_active = False
            logger.warning(f'[Camera] hardware MJPEG unavailable ({e}) — using cv2.imencode')

    def _hw_stop(self):
        if not self._hw_active:
            return
        self._hw_active = False
        try:
            with self._picam_lock:
                if self._picam is not None:
                    self._picam.stop_encoder()
        except Exception:
            pass

    def _hw_sync(self, want, picam):
        """Запустить/остановить энкодер по текущему спросу; следит за тем,
        чтобы он реально выдавал кадры (иначе — постоянный откат на cv2)."""
        if want and not self._hw_active and self._hw_ok:
            self._hw_start(picam)
        elif not want and self._hw_active:
            self._hw_stop()
            logger.info('[Camera] hardware MJPEG encoder OFF')
        if (self._hw_active and self._hw_frames == 0
                and time.monotonic() - self._hw_started_ts > HW_MJPEG_STARTUP_TIMEOUT_S):
            self._hw_stop()
            self._hw_ok = False
            logger.warning('[Camera] hardware MJPEG gave no frames — falling back to cv2.imencode')

    # ---- сон камеры ---------------------------------------------------------
    def _sleep_camera(self):
        """Остановить CSI-камеру (поток кадров, ISP). Вернуть True при успехе."""
        self._hw_stop()
        with self._picam_lock:
            if self._source != 'picamera2' or self._picam is None:
                return False
            try:
                self._picam.stop()
            except Exception as e:
                logger.warning(f'[Camera] sleep failed: {e}')
                return False
        logger.info('[Camera] no consumers — sleeping (CSI stream stopped)')
        return True

    def _wake_camera(self):
        with self._picam_lock:
            if self._picam is not None:
                self._picam.start()
        logger.info('[Camera] woke up')

    # ---- захват -------------------------------------------------------------
    def _publish_jpeg(self, frame_bgr):
        ok, jpg = cv2.imencode('.jpg', frame_bgr,
                               [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_QUALITY])
        if ok:
            with self._frame_cond:
                self._frame = jpg.tobytes()
                self._frame_id += 1
                self._frame_cond.notify_all()

    def _capture_loop(self):
        frame_interval = 1.0 / CAMERA_FPS if CAMERA_FPS > 0 else 0
        consecutive_errors = 0
        MAX_ERR = 10
        last_demand = time.monotonic()
        while self._running:
            t_loop = time.monotonic()
            try:
                demand = self._has_demand()
                if demand:
                    last_demand = t_loop
                # --- адаптивное энергосбережение (v1.6.4) -------------------
                # Загрузка системы (max из CPU и RAM): чем она выше, тем реже
                # фон захватывает кадры (ядра отдаются активным задачам —
                # стриму/CV/навигации), а полный сон CSI при высокой загрузке
                # запрещён: пробуждение под нагрузкой занимало секунды и
                # выглядело как «всё лагает при малом простое».
                load = _system_load_pct()
                if load is None:
                    idle_backoff, can_sleep = 1.0, True
                else:
                    idle_backoff = 1.0 + (CAMERA_IDLE_BACKOFF_MAX - 1.0) * max(
                        0.0, min(1.0, (load - CAMERA_IDLE_BACKOFF_CPU_PCT)
                                 / max(1.0, 100.0 - CAMERA_IDLE_BACKOFF_CPU_PCT)))
                    can_sleep = load < CAMERA_SLEEP_SKIP_CPU_PCT
                # --- сон при отсутствии потребителей ---
                if (not demand and not self._asleep and CAMERA_SLEEP_AFTER_S > 0
                        and can_sleep
                        and t_loop - last_demand > CAMERA_SLEEP_AFTER_S):
                    self._asleep = self._sleep_camera()
                if self._asleep:
                    if not demand:
                        self._wake.wait(1.0)
                        self._wake.clear()
                        continue
                    self._wake_camera()
                    self._asleep = False
                with self._picam_lock:
                    source, picam, cap = self._source, self._picam, self._cap
                if picam is None and cap is None:
                    self._init_camera()
                    with self._picam_lock:
                        source, picam, cap = self._source, self._picam, self._cap
                    if picam is None and cap is None:
                        time.sleep(1.0)
                        continue
                # аппаратный MJPEG: только для «чистого» видео (без CV-оверлея)
                with self._demand_lock:
                    viewers = self._stream_clients > 0
                    raw_users = self._raw_demand > 0
                cv_active = self.cv_thread.cv_mode != CV_NONE
                self._hw_sync(bool(self._hw_ok and source == 'picamera2'
                                   and viewers and not cv_active), picam)
                if self._hw_active and not (cv_active or raw_users):
                    # кадры идут из энкодера в _on_hw_frame — Python-обработка
                    # (capture_array, конвертация, imencode) не нужна вовсе
                    self._wake.wait(0.25)
                    self._wake.clear()
                    continue
                if source == 'v4l2':
                    ok, captured = cap.read()
                    if not ok or captured is None:
                        captured = None
                else:
                    captured = picam.capture_array()
                if captured is not None and _HAS_CV2 and len(captured.shape) == 3:
                    # Нормализация к настоящему RGB: и Picamera2 "RGB888"
                    # (байты BGR), и OpenCV VideoCapture отдают BGR, а все
                    # потребители (CV, ArUco, color_scan, YOLO) ждут RGB.
                    captured = cv2.cvtColor(captured, cv2.COLOR_BGR2RGB)
                raw = captured
                if raw is None or len(raw.shape) != 3:
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_ERR:
                        self._restart_camera()
                        consecutive_errors = 0
                    time.sleep(0.01)
                    continue
                consecutive_errors = 0
                with self._raw_cond:
                    self._last_raw = raw
                    self._raw_id += 1
                    self._raw_cond.notify_all()
                if demand:
                    cv_on = self.cv_thread.cv_mode != CV_NONE and _HAS_CV2
                    if cv_on:
                        self.cv_thread.submit_frame(raw)
                    with self._demand_lock:
                        want_jpeg = self._stream_clients > 0
                    # JPEG кодируем только для зрителей: при CV-режиме без
                    # открытой вкладки видео раньше тратилось ядро впустую.
                    # CAMERA_STREAM_FPS ограничивает частоту кадров стрима.
                    gap = (0.9 / CAMERA_STREAM_FPS) if CAMERA_STREAM_FPS > 0 else 0.0
                    if (want_jpeg and _HAS_CV2 and not self._hw_active
                            and t_loop - self._last_jpeg_ts >= gap):
                        self._last_jpeg_ts = t_loop
                        overlay = self.cv_thread.get_overlay() if cv_on else None
                        self._publish_jpeg(
                            overlay if overlay is not None
                            else cv2.cvtColor(raw, cv2.COLOR_RGB2BGR))
                    # спим остаток интервала: capture_array() уже ждал кадр,
                    # прежний «полный» сон вдвое занижал реальный fps
                    sleep_s = frame_interval - (time.monotonic() - t_loop)
                    if sleep_s > 0:
                        time.sleep(sleep_s)
                else:
                    # фоновый редкий захват, чтобы _last_raw и статус не
                    # протухали; появление спроса будит цикл немедленно.
                    # При высокой загрузке системы интервал растёт до
                    # CAMERA_IDLE_BACKOFF_MAX × base (адаптивно, v1.6.4)
                    self._wake.wait(CAMERA_IDLE_INTERVAL_S * idle_backoff)
                    self._wake.clear()
            except Exception as e:
                consecutive_errors += 1
                logger.error(f'[Camera] frame error: {e}')
                if consecutive_errors >= MAX_ERR:
                    self._restart_camera()
                    consecutive_errors = 0
                else:
                    time.sleep(0.05)

    # ---- выдача кадров ------------------------------------------------------
    def _ensure_started(self):
        if self._thread is None or not self._thread.is_alive():
            self.start()

    def get_raw_frame(self):
        """Последний сырой RGB-кадр (копия) — для разовых запросов."""
        self._ensure_started()
        with self._raw_cond:
            return None if self._last_raw is None else self._last_raw.copy()

    def wait_raw_frame(self, last_id=0, timeout=1.0):
        """Дождаться СВЕЖЕГО сырого кадра: (id, кадр) или (last_id, None).

        Кадр отдаётся без копии (только для чтения): потребителям ArUco и
        color_scan раньше приходилось по 0.9 МБ копировать и обрабатывать
        один и тот же кадр по несколько раз за период камеры.
        """
        self._ensure_started()
        with self._raw_cond:
            if not self._raw_cond.wait_for(
                    lambda: self._raw_id != last_id and self._last_raw is not None,
                    timeout=timeout):
                return last_id, None
            return self._raw_id, self._last_raw

    def wait_frame(self, last_id=0, timeout=1.0):
        """Дождаться нового MJPEG-кадра: (id, bytes) или (last_id, None)."""
        self._ensure_started()
        with self._frame_cond:
            if not self._frame_cond.wait_for(
                    lambda: self._frame_id != last_id and self._frame is not None,
                    timeout=timeout):
                return last_id, None
            return self._frame_id, self._frame

    def get_frame(self):
        """Последний MJPEG-кадр. Вызов вне /video_feed (selftest/тесты)
        сам создаёт разовый спрос, иначе кадр мог бы не кодироваться."""
        self.stream_open()           # спрос — до старта потока, иначе гонка
        try:
            self._ensure_started()
            with self._frame_cond:
                if self._frame is None:
                    self._frame_cond.wait_for(
                        lambda: self._frame is not None, timeout=0.5)
                return self._frame
        finally:
            self.stream_close()

    def set_cv_mode(self, mode):
        if mode == CV_YOLO:
            self.cv_thread.yolo.load()   # ленивая загрузка ONNX при включении
        self.cv_thread.cv_mode = mode
        if mode == CV_NONE:
            self.cv_thread.clear_overlay()
        self._wake.set()

    def get_yolo_status(self):
        return self.cv_thread.yolo.get_status()

    def set_hand_color(self, h_low, s_low, v_low, h_high, s_high, v_high):
        self.cv_thread.set_hand_color(h_low, s_low, v_low, h_high, s_high, v_high)

    set_color = set_hand_color

    def get_hand_color(self):
        return self.cv_thread.get_hand_color()

    get_color = get_hand_color

    def shutdown(self):
        self._running = False
        self._wake.set()
        try:
            self.cv_thread.stop()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._hw_stop()
        with self._picam_lock:
            if self._picam:
                for fn in (self._picam.stop, self._picam.close):
                    try:
                        fn()
                    except Exception:
                        pass
                self._picam = None
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            self._source = None
