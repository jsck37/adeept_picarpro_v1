"""Camera — PiCamera2 + OpenCV, rgb_raw only, 30fps."""

import threading, time, math, cv2, numpy as np
from Server.logger import logger

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

from Server.camera.base_camera import BaseCamera
from Server.config import (
    CAMERA_RESOLUTION, CAMERA_FPS, CAMERA_JPEG_QUALITY,
    CAMERA_FLIP_HORIZONTAL, CAMERA_FLIP_VERTICAL,
    CV_LINE_POS_1, CV_LINE_POS_2, CV_LINE_THRESHOLD,
)
from Server.utils.kalman import KalmanFilter

CV_NONE = "none"
CV_LINE = "findlineCV"
CV_HAND = "trackHand"


class CVThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._flag = threading.Event()
        self._running = True
        self._frame = None
        self._processing = False
        self.cv_mode = CV_NONE
        self.line_pos_1 = CV_LINE_POS_1
        self.line_pos_2 = CV_LINE_POS_2
        self.kf_x = KalmanFilter()
        self.kf_y = KalmanFilter()
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.line_pos = [0, 0]
        self.line_angle = 0
        self.frame_size = list(CAMERA_RESOLUTION)
        self.line_threshold = CV_LINE_THRESHOLD
        self.on_line_found = None
        # Hand tracking state
        self.hand_pos = [0, 0]       # (cx, cy) of hand centroid
        self.hand_detected = False
        self.hand_area = 0
        self.on_hand_found = None    # callback(pos, area)

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
                    logger.error(f"[CV] Error: {e}")

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
            if self.cv_mode == CV_LINE:
                self._find_line(frame)
            elif self.cv_mode == CV_HAND:
                self._find_hand(frame)
        finally:
            self._processing = False

    def _find_line(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Gaussian blur to reduce noise before thresholding
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        _, binary = cv2.threshold(gray, self.line_threshold, 255, cv2.THRESH_BINARY_INV)
        # Morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        indices1 = np.where(binary[self.line_pos_1] > 0)[0]
        indices2 = np.where(binary[self.line_pos_2] > 0)[0]
        pos1 = int(np.mean(indices1)) if len(indices1) > 0 else 0
        pos2 = int(np.mean(indices2)) if len(indices2) > 0 else 0
        self.line_pos = [pos1, pos2]
        self.line_angle = math.degrees(math.atan2(pos2 - pos1, self.line_pos_1 - self.line_pos_2)) if pos1 > 0 and pos2 > 0 else 0
        if self.on_line_found:
            self.on_line_found(self.line_pos, self.line_angle)

    def _find_hand(self, frame):
        """Detect a hand using skin-colour segmentation in HSV.

        Uses multiple HSV ranges for robust skin detection across different
        lighting conditions and skin tones.  Finds the largest skin-coloured
        contour, computes its centroid and area, and calls the callback
        with the hand position and area.

        The callback (on_hand_found) also receives a shake_detected flag
        that is True when rapid position changes are observed — this is
        used by the autonomous controller to auto-stop the mode.
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Two HSV ranges for skin detection (works for a variety of skin tones)
        lower1 = np.array([0, 40, 60])
        upper1 = np.array([25, 255, 255])
        lower2 = np.array([170, 40, 60])
        upper2 = np.array([180, 255, 255])

        mask1 = cv2.inRange(hsv, lower1, upper1)
        mask2 = cv2.inRange(hsv, lower2, upper2)
        mask = cv2.bitwise_or(mask1, mask2)

        # Morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        # Smooth edges
        mask = cv2.GaussianBlur(mask, (5, 5), 0)
        _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

        self.hand_detected = False
        if contours:
            c = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(c)
            if area > 1500:  # minimum hand size
                M = cv2.moments(c)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    self.hand_pos = [cx, cy]
                    self.hand_area = int(area)
                    self.hand_detected = True

                    # Shake detection: check if on_hand_found callback reports shake
                    shake = False
                    if self.on_hand_found:
                        shake = self.on_hand_found(self.hand_pos, self.hand_area)
                    if shake:
                        self.hand_detected = False
                        return
                    return

        # No hand found — still call callback so tracker knows hand is lost
        if self.on_hand_found:
            self.on_hand_found([0, 0], 0)


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
        logger.info(f"[Camera] {CAMERA_RESOLUTION} @ {CAMERA_FPS}fps q={CAMERA_JPEG_QUALITY}%")

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
                logger.error(f"[Camera] Error: {e}")
                time.sleep(0.1)

    def _draw_overlays(self, frame):
        mode = self.cv_thread.cv_mode
        if mode == CV_LINE:
            p1, p2 = self.cv_thread.line_pos
            h, w = frame.shape[:2]
            cv2.line(frame, (0, self.cv_thread.line_pos_1), (w, self.cv_thread.line_pos_1), (0, 255, 0), 1)
            cv2.line(frame, (0, self.cv_thread.line_pos_2), (w, self.cv_thread.line_pos_2), (0, 255, 0), 1)
            if p1 > 0:
                cv2.circle(frame, (p1, self.cv_thread.line_pos_1), 5, (0, 0, 255), -1)
            if p2 > 0:
                cv2.circle(frame, (p2, self.cv_thread.line_pos_2), 5, (0, 0, 255), -1)
        elif mode == CV_HAND and self.cv_thread.hand_detected:
            x, y = self.cv_thread.hand_pos
            cv2.circle(frame, (x, y), 15, (0, 255, 0), 2)
            cv2.circle(frame, (x, y), 5, (0, 255, 0), -1)
            h, w = frame.shape[:2]
            # Crosshair at frame centre (target)
            cv2.line(frame, (w // 2 - 20, h // 2), (w // 2 + 20, h // 2), (255, 255, 0), 1)
            cv2.line(frame, (w // 2, h // 2 - 20), (w // 2, h // 2 + 20), (255, 255, 0), 1)
            cv2.putText(frame, f"HAND ({x},{y})", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        if mode != CV_NONE:
            cv2.putText(frame, mode, (10, frame.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        return frame

    def set_cv_mode(self, mode):
        self.cv_thread.cv_mode = mode

    def shutdown(self):
        self.cv_thread.stop()
        BaseCamera.shutdown()
        if self._picam:
            try:
                self._picam.stop()
            except Exception:
                pass
            self._picam = None
