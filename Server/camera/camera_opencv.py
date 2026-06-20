import threading, time, math
from Server.logger import logger
from config import (
    CAMERA_RESOLUTION, CAMERA_FPS, CAMERA_JPEG_QUALITY,
    CAMERA_FLIP_HORIZONTAL, CAMERA_FLIP_VERTICAL,
    CV_LINE_POS_1, CV_LINE_POS_2,
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

CV_NONE = 'none'
CV_LINE = 'findlineCV'
CV_HAND = 'trackHand'

DEFAULT_HAND_LOW = np.array([0, 30, 50], dtype=np.uint8) if _HAS_CV2 else None
DEFAULT_HAND_HIGH = np.array([25, 255, 255], dtype=np.uint8) if _HAS_CV2 else None


class CVThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._flag = threading.Event()
        self._running = True
        self._frame = None
        self._frame_lock = threading.Lock()
        self._processing = False
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
        self.overlay_frame = None
        self._overlay_lock = threading.Lock()

    def run(self):
        while self._running:
            self._flag.wait(timeout=0.5)
            if not self._running:
                break
            self._flag.clear()
            with self._frame_lock:
                frame = self._frame
            if frame is None:
                continue
            try:
                self._process(frame)
            except Exception as e:
                logger.error(f'[CV] error: {e}')

    def submit_frame(self, frame):
        if self._processing:
            return
        with self._frame_lock:
            self._frame = frame.copy()
        self._flag.set()

    def get_overlay(self):
        with self._overlay_lock:
            return self.overlay_frame.copy() if self.overlay_frame is not None else None

    def stop(self):
        self._running = False
        self._flag.set()

    def set_hand_color(self, h_low, s_low, v_low, h_high, s_high, v_high):
        if not _HAS_CV2:
            return
        with self._hand_lock:
            self._hand_low = np.array([h_low, s_low, v_low], dtype=np.uint8)
            self._hand_high = np.array([h_high, s_high, v_high], dtype=np.uint8)

    def get_hand_color(self):
        with self._hand_lock:
            if self._hand_low is not None:
                return (list(self._hand_low), list(self._hand_high))
            return None

    def _process(self, frame):
        if not _HAS_CV2:
            return
        if self.cv_mode == CV_LINE:
            self._find_line(frame)
        elif self.cv_mode == CV_HAND:
            self._find_hand(frame)

    def _find_line(self, frame):
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        h, w = binary.shape[:2]
        idx1 = np.where(binary[CV_LINE_POS_1] > 0)[0]
        idx2 = np.where(binary[CV_LINE_POS_2] > 0)[0]
        pos1 = int(np.mean(idx1)) if len(idx1) > 0 else 0
        pos2 = int(np.mean(idx2)) if len(idx2) > 0 else 0
        self.line_pos = [pos1, pos2]
        self.line_angle = (
            math.degrees(math.atan2(pos2 - pos1, CV_LINE_POS_1 - CV_LINE_POS_2))
            if pos1 > 0 and pos2 > 0 else 0)
        disp = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        cv2.line(disp, (0, CV_LINE_POS_1), (w, CV_LINE_POS_1), (0, 255, 0), 1)
        cv2.line(disp, (0, CV_LINE_POS_2), (w, CV_LINE_POS_2), (0, 255, 0), 1)
        if pos1 > 0 and pos2 > 0:
            cv2.line(disp, (pos2, CV_LINE_POS_2), (pos1, CV_LINE_POS_1), (0, 0, 255), 2)
            mx = (pos1 + pos2) // 2
            my = (CV_LINE_POS_1 + CV_LINE_POS_2) // 2
            cv2.circle(disp, (mx, my), 6, (0, 0, 255), -1)
        if pos1 > 0:
            cv2.circle(disp, (pos1, CV_LINE_POS_1), 5, (0, 255, 0), -1)
        if pos2 > 0:
            cv2.circle(disp, (pos2, CV_LINE_POS_2), 5, (0, 255, 0), -1)
        status = 'TRACKING' if (pos1 > 0 or pos2 > 0) else 'SEARCHING'
        cv2.putText(disp, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if status == 'TRACKING' else (0, 0, 255), 2)
        with self._overlay_lock:
            self.overlay_frame = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        if self.on_line_found:
            self.on_line_found(self.line_pos, self.line_angle)

    def _find_hand(self, frame):
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        with self._hand_lock:
            low = self._hand_low.copy()
            high = self._hand_high.copy()
        if low[0] <= high[0]:
            mask = cv2.inRange(hsv, low, high)
        else:
            m1 = cv2.inRange(hsv, np.array([0, low[1], low[2]]),
                                  np.array([high[0], high[1], high[2]]))
            m2 = cv2.inRange(hsv, np.array([low[0], low[1], low[2]]),
                                  np.array([179, high[1], high[2]]))
            mask = cv2.bitwise_or(m1, m2)
        k_large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        k_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_large, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_small, iterations=2)
        mask = cv2.GaussianBlur(mask, (5, 5), 0)
        _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        self.hand_detected = False
        h, w = bgr.shape[:2]
        cx_c, cy_c = w // 2, h // 2
        disp = bgr.copy()
        if contours:
            c = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(c)
            if area > 800:
                M = cv2.moments(c)
                if M['m00'] > 0:
                    cx = int(M['m10'] / M['m00'])
                    cy = int(M['m01'] / M['m00'])
                    self.hand_pos = [cx, cy]
                    self.hand_area = int(area)
                    self.hand_detected = True
                    cv2.drawContours(disp, [c], -1, (0, 255, 0), 2)
                    cv2.line(disp, (cx - 25, cy), (cx + 25, cy), (0, 0, 255), 2)
                    cv2.line(disp, (cx, cy - 25), (cx, cy + 25), (0, 0, 255), 2)
                    cv2.circle(disp, (cx, cy), 18, (0, 0, 255), 2)
                    cv2.circle(disp, (cx, cy), 3, (0, 0, 255), -1)
                    cv2.putText(disp, f'LOCK ({cx},{cy})', (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                else:
                    self._draw_search(disp, cx_c, cy_c)
            else:
                self._draw_search(disp, cx_c, cy_c)
        else:
            self._draw_search(disp, cx_c, cy_c)
        with self._overlay_lock:
            self.overlay_frame = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        if self.on_hand_found:
            if self.hand_detected:
                shake = self.on_hand_found(self.hand_pos, self.hand_area)
                if shake:
                    self.hand_detected = False
                    return
            else:
                self.on_hand_found([0, 0], 0)

    @staticmethod
    def _draw_search(disp, cx, cy):
        cv2.line(disp, (cx - 20, cy), (cx + 20, cy), (0, 255, 0), 1)
        cv2.line(disp, (cx, cy - 20), (cx, cy + 20), (0, 255, 0), 1)
        cv2.circle(disp, (cx, cy), 15, (0, 255, 0), 1)
        cv2.putText(disp, 'SEARCHING...', (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)


class Camera:
    _instance_lock = threading.Lock()
    _instance = None

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
        self._picam_lock = threading.Lock()
        self._reconnect_count = 0
        self._frame = None
        self._frame_lock = threading.Lock()
        self._frame_ready = threading.Event()
        self._running = True
        self._thread = None

    def _init_camera(self):
        with self._picam_lock:
            if self._picam is not None:
                return
            if not _HAS_PICAM:
                logger.error('[Camera] picamera2 not installed')
                return
            try:
                self._picam = Picamera2()
                cfg = self._picam.create_preview_configuration(
                    main={'size': CAMERA_RESOLUTION, 'format': 'RGB888'})
                self._picam.configure(cfg)
                if CAMERA_FLIP_HORIZONTAL:
                    self._picam.set_control('flip_h', True)
                if CAMERA_FLIP_VERTICAL:
                    self._picam.set_control('flip_v', True)
                try:
                    self._picam.set_controls({
                        'FrameRate': CAMERA_FPS,
                        'AeEnable': True, 'AwbEnable': True,
                        'NoiseReductionMode': 1,
                    })
                except Exception:
                    pass
                self._picam.start()
                self._reconnect_count = 0
                logger.info(f'[Camera] OK — {CAMERA_RESOLUTION} @ {CAMERA_FPS}fps')
            except Exception as e:
                logger.error(f'[Camera] init failed: {e}')
                self._picam = None

    def _restart_camera(self):
        with self._picam_lock:
            self._reconnect_count += 1
            backoff = min(5.0, 0.5 * (2 ** min(self._reconnect_count, 4)))
            if self._picam:
                try: self._picam.stop()
                except Exception: pass
                try: self._picam.close()
                except Exception: pass
                self._picam = None
        time.sleep(backoff)
        self._init_camera()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self):
        frame_interval = 1.0 / CAMERA_FPS if CAMERA_FPS > 0 else 0
        consecutive_errors = 0
        MAX_ERR = 10
        while self._running:
            try:
                with self._picam_lock:
                    picam = self._picam
                if picam is None:
                    self._init_camera()
                    if self._picam is None:
                        time.sleep(1.0)
                        continue
                    picam = self._picam
                raw = picam.capture_array()
                if raw is None or len(raw.shape) != 3:
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_ERR:
                        self._restart_camera()
                        consecutive_errors = 0
                    time.sleep(0.01)
                    continue
                consecutive_errors = 0
                if self.cv_thread.cv_mode != CV_NONE and _HAS_CV2:
                    self.cv_thread.submit_frame(raw)
                    overlay = self.cv_thread.get_overlay()
                    frame = overlay if overlay is not None else raw
                else:
                    frame = raw
                if _HAS_CV2:
                    ok, jpg = cv2.imencode('.jpg', frame,
                                            [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_QUALITY])
                    if ok:
                        with self._frame_lock:
                            self._frame = jpg.tobytes()
                        self._frame_ready.set()
                        self._frame_ready.clear()
                if frame_interval > 0:
                    time.sleep(max(0.0, frame_interval - 0.005))
            except Exception as e:
                consecutive_errors += 1
                logger.error(f'[Camera] frame error: {e}')
                if consecutive_errors >= MAX_ERR:
                    self._restart_camera()
                    consecutive_errors = 0
                else:
                    time.sleep(0.05)

    def get_frame(self):
        if self._thread is None or not self._thread.is_alive():
            self.start()
        self._frame_ready.wait(timeout=0.5)
        with self._frame_lock:
            return self._frame

    def set_cv_mode(self, mode):
        self.cv_thread.cv_mode = mode
        if mode == CV_NONE:
            with self.cv_thread._overlay_lock:
                self.cv_thread.overlay_frame = None

    def set_hand_color(self, h_low, s_low, v_low, h_high, s_high, v_high):
        self.cv_thread.set_hand_color(h_low, s_low, v_low, h_high, s_high, v_high)

    def get_hand_color(self):
        return self.cv_thread.get_hand_color()

    def shutdown(self):
        self._running = False
        try:
            self.cv_thread.stop()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        with self._picam_lock:
            if self._picam:
                try: self._picam.stop()
                except Exception: pass
                try: self._picam.close()
                except Exception: pass
                self._picam = None
