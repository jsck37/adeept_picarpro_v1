"""Camera — PiCamera2 + OpenCV, rgb_raw only, 30fps."""

import threading, time, math, cv2, numpy as np

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

from Server.camera.base_camera import BaseCamera
from Server.config import (
    CAMERA_RESOLUTION, CAMERA_FPS, CAMERA_JPEG_QUALITY,
    CAMERA_FLIP_HORIZONTAL, CAMERA_FLIP_VERTICAL,
    CV_COLOR_LOWER_H, CV_COLOR_LOWER_S, CV_COLOR_LOWER_V,
    CV_COLOR_UPPER_H, CV_COLOR_UPPER_S, CV_COLOR_UPPER_V,
    CV_LINE_POS_1, CV_LINE_POS_2, CV_LINE_THRESHOLD,
    CV_WATCHDOG_THRESHOLD, CV_WATCHDOG_BLUR_SIZE,
)
from Server.utils.kalman import KalmanFilter

CV_NONE = "none"
CV_COLOR = "findColor"
CV_LINE = "findlineCV"
CV_WATCH = "watchDog"


class CVThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._flag = threading.Event()
        self._running = True
        self._frame = None
        self._processing = False
        self.cv_mode = CV_NONE
        self.color_lower = np.array([CV_COLOR_LOWER_H, CV_COLOR_LOWER_S, CV_COLOR_LOWER_V])
        self.color_upper = np.array([CV_COLOR_UPPER_H, CV_COLOR_UPPER_S, CV_COLOR_UPPER_V])
        self.line_pos_1 = CV_LINE_POS_1
        self.line_pos_2 = CV_LINE_POS_2
        self.watchdog_threshold = CV_WATCHDOG_THRESHOLD
        self.kf_x = KalmanFilter()
        self.kf_y = KalmanFilter()
        self._bg = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=self.watchdog_threshold, detectShadows=True)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.color_pos = [0, 0]
        self.line_pos = [0, 0]
        self.line_angle = 0
        self.motion_detected = False
        self.frame_size = list(CAMERA_RESOLUTION)
        self.line_threshold = CV_LINE_THRESHOLD
        self.watchdog_blur_size = CV_WATCHDOG_BLUR_SIZE
        self.on_color_found = None
        self.on_line_found = None
        self.on_motion_detected = None

    def run(self):
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            self._flag.clear()
            if self._frame is not None:
                try:
                    self._process()
                except Exception as e:
                    print(f"[CV] Error: {e}")

    def submit_frame(self, frame):
        if self._processing:
            return
        self._frame = frame
        self._flag.set()

    def stop(self):
        self._running = False
        self._flag.set()

    def _process(self):
        frame = self._frame
        if frame is None:
            return
        self._processing = True
        try:
            h, w = frame.shape[:2]
            self.frame_size = [w, h]
            if self.cv_mode == CV_COLOR:
                self._find_color(frame)
            elif self.cv_mode == CV_LINE:
                self._find_line(frame)
            elif self.cv_mode == CV_WATCH:
                self._watchdog(frame)
        finally:
            self._processing = False

    def _find_color(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.color_lower, self.color_upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel, iterations=2)
        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        if contours:
            c = max(contours, key=cv2.contourArea)
            ((x, y), radius) = cv2.minEnclosingCircle(c)
            M = cv2.moments(c)
            if M["m00"] > 0 and radius > 5:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                self.kf_x.filter(cx / self.frame_size[0])
                self.kf_y.filter(cy / self.frame_size[1])
                self.color_pos = [int(self.kf_x.get() * self.frame_size[0]),
                                  int(self.kf_y.get() * self.frame_size[1])]
                if self.on_color_found:
                    self.on_color_found(self.color_pos, radius)

    def _find_line(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, self.line_threshold, 255, cv2.THRESH_BINARY_INV)
        indices1 = np.where(binary[self.line_pos_1] > 0)[0]
        indices2 = np.where(binary[self.line_pos_2] > 0)[0]
        pos1 = int(np.mean(indices1)) if len(indices1) > 0 else 0
        pos2 = int(np.mean(indices2)) if len(indices2) > 0 else 0
        self.line_pos = [pos1, pos2]
        self.line_angle = math.degrees(math.atan2(pos2 - pos1, self.line_pos_1 - self.line_pos_2)) if pos1 > 0 and pos2 > 0 else 0
        if self.on_line_found:
            self.on_line_found(self.line_pos, self.line_angle)

    def _watchdog(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, self.watchdog_blur_size, 0)
        fg = self._bg.apply(gray)
        _, thresh = cv2.threshold(fg, 25, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=1)
        contours = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        self.motion_detected = any(cv2.contourArea(c) > 500 for c in contours)
        if self.on_motion_detected:
            self.on_motion_detected(self.motion_detected, contours)

    def set_color_range(self, lh, ls, lv, uh, us, uv):
        self.color_lower = np.array([lh, ls, lv])
        self.color_upper = np.array([uh, us, uv])


class Camera(BaseCamera):
    def __init__(self):
        self.cv_thread = CVThread()
        self.cv_thread.start()
        self._picam = None
        super().__init__(target_fps=CAMERA_FPS)

    def _init_camera(self):
        if self._picam is not None:
            return
        if Picamera2 is None:
            raise RuntimeError("picamera2 not installed")
        self._picam = Picamera2()
        cfg = self._picam.create_preview_configuration(
            main={"size": CAMERA_RESOLUTION, "format": "RGB888"}
        )
        self._picam.configure(cfg)
        if CAMERA_FLIP_HORIZONTAL:
            self._picam.set_control("flip_h", True)
        if CAMERA_FLIP_VERTICAL:
            self._picam.set_control("flip_v", True)
        self._picam.start()
        print(f"[Camera] {CAMERA_RESOLUTION} @ {CAMERA_FPS}fps q={CAMERA_JPEG_QUALITY}%")

    def frames(self):
        self._init_camera()
        while True:
            try:
                if self._picam is None:
                    time.sleep(0.3)
                    self._init_camera()
                    if self._picam is None:
                        continue
                raw = self._picam.capture_array()
                if raw is None or len(raw.shape) != 3:
                    continue
                # rgb_raw: use as-is (no conversion)
                frame = raw
                if self.cv_thread.cv_mode != CV_NONE:
                    self.cv_thread.submit_frame(frame.copy())
                frame = self._draw_overlays(frame)
                ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_QUALITY])
                if ok:
                    yield jpg.tobytes()
            except Exception as e:
                print(f"[Camera] Error: {e}")
                time.sleep(0.1)

    def _draw_overlays(self, frame):
        mode = self.cv_thread.cv_mode
        if mode == CV_COLOR:
            x, y = self.cv_thread.color_pos
            if x or y:
                cv2.circle(frame, (x, y), 10, (0, 255, 0), 2)
                cv2.putText(frame, f"({x},{y})", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        elif mode == CV_LINE:
            p1, p2 = self.cv_thread.line_pos
            h, w = frame.shape[:2]
            cv2.line(frame, (0, self.cv_thread.line_pos_1), (w, self.cv_thread.line_pos_1), (0, 255, 0), 1)
            cv2.line(frame, (0, self.cv_thread.line_pos_2), (w, self.cv_thread.line_pos_2), (0, 255, 0), 1)
            if p1 > 0:
                cv2.circle(frame, (p1, self.cv_thread.line_pos_1), 5, (0, 0, 255), -1)
            if p2 > 0:
                cv2.circle(frame, (p2, self.cv_thread.line_pos_2), 5, (0, 0, 255), -1)
        elif mode == CV_WATCH and self.cv_thread.motion_detected:
            cv2.putText(frame, "MOTION", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        if mode != CV_NONE:
            cv2.putText(frame, mode, (10, frame.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        return frame

    def set_cv_mode(self, mode):
        self.cv_thread.cv_mode = mode

    def set_color_range(self, lh, ls, lv, uh, us, uv):
        self.cv_thread.set_color_range(lh, ls, lv, uh, us, uv)

    def shutdown(self):
        self.cv_thread.stop()
        BaseCamera.shutdown()
        if self._picam:
            try:
                self._picam.stop()
            except Exception:
                pass
            self._picam = None
