"""PiCar-Pro camera: Picamera2 + OpenCV overlays.

Two responsibilities:
  1. Continuously capture frames from the Pi camera (Picamera2) and
     expose them as JPEG bytes for the MJPEG video feed.
  2. Optionally run a CV overlay thread (line-follow / hand-track) and
     replace the live frame with the overlay frame.

Robustness:
  * The capture loop never exits on a transient error. It logs the
    error, sleeps briefly, and tries again. If Picamera2 itself dies
    (camera cable loose, etc.), we attempt to fully re-init the camera
    up to 5 times with exponential back-off, then keep retrying every
    5s forever.
  * The CV overlay thread is decoupled from the capture loop — if a CV
    frame takes too long, the camera just shows the raw frame for that
    tick instead of stalling.
"""

import threading, time, math
import cv2
import numpy as np

from Server.logger import logger

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

from Server.camera.base_camera import BaseCamera
from config import (
    CAMERA_RESOLUTION, CAMERA_FPS, CAMERA_JPEG_QUALITY,
    CAMERA_FLIP_HORIZONTAL, CAMERA_FLIP_VERTICAL,
    CV_LINE_POS_1, CV_LINE_POS_2, CV_LINE_THRESHOLD,
)

CV_NONE = "none"
CV_LINE = "findlineCV"
CV_HAND = "trackHand"

# Default HSV hand-color range (used when the user hasn't picked one).
# Skin-tone-ish: H 0..25 (red/orange side of the wheel), S 30..255, V 50..255.
DEFAULT_HAND_HSV_LOW  = np.array([0,   30,  50],  dtype=np.uint8)
DEFAULT_HAND_HSV_HIGH = np.array([25,  255, 255], dtype=np.uint8)


class CVThread(threading.Thread):
    """Background thread that runs the CV processing for the current mode.

    Frames are submitted via ``submit_frame()`` and the latest overlay
    can be fetched via ``get_overlay()``. The thread drops frames if it
    can't keep up, so it never blocks the capture loop.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._flag = threading.Event()
        self._running = True
        self._frame = None
        self._frame_lock = threading.Lock()
        self._processing = False
        self.cv_mode = CV_NONE

        self.line_pos_1 = CV_LINE_POS_1
        self.line_pos_2 = CV_LINE_POS_2
        self.line_pos = [0, 0]
        self.line_angle = 0
        self.frame_size = list(CAMERA_RESOLUTION)
        self.line_threshold = CV_LINE_THRESHOLD
        self.on_line_found = None

        self.hand_pos = [0, 0]
        self.hand_detected = False
        self.hand_area = 0
        self.on_hand_found = None

        # Hand color range — adjustable via set_hand_color().
        self._hand_lock = threading.Lock()
        self._hand_low  = DEFAULT_HAND_HSV_LOW
        self._hand_high = DEFAULT_HAND_HSV_HIGH

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
                logger.error(f"[CV] Error: {e}")

    def submit_frame(self, frame):
        # Drop the frame if we're still processing the previous one.
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

    # ---- hand color customisation ------------------------------------
    def set_hand_color(self, h_low, s_low, v_low, h_high, s_high, v_high):
        """Update the HSV range used by the hand-tracker."""
        with self._hand_lock:
            # Handle hue wrap-around: if h_low > h_high, we split into two
            # ranges internally. For simplicity here, the user is expected
            # to pick a range where h_low <= h_high.
            self._hand_low  = np.array([h_low,  s_low,  v_low],  dtype=np.uint8)
            self._hand_high = np.array([h_high, s_high, v_high], dtype=np.uint8)

    def get_hand_color(self):
        with self._hand_lock:
            return (list(self._hand_low), list(self._hand_high))

    # ---- processing --------------------------------------------------
    def _process(self, frame):
        h, w = frame.shape[:2]
        self.frame_size = [w, h]
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

        indices1 = np.where(binary[self.line_pos_1] > 0)[0]
        indices2 = np.where(binary[self.line_pos_2] > 0)[0]
        pos1 = int(np.mean(indices1)) if len(indices1) > 0 else 0
        pos2 = int(np.mean(indices2)) if len(indices2) > 0 else 0
        self.line_pos = [pos1, pos2]
        self.line_angle = (
            math.degrees(math.atan2(pos2 - pos1, self.line_pos_1 - self.line_pos_2))
            if pos1 > 0 and pos2 > 0 else 0
        )

        display = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        cv2.line(display, (0, self.line_pos_1), (w, self.line_pos_1), (0, 255, 0), 1)
        cv2.line(display, (0, self.line_pos_2), (w, self.line_pos_2), (0, 255, 0), 1)
        if pos1 > 0 and pos2 > 0:
            cv2.line(display, (pos2, self.line_pos_2), (pos1, self.line_pos_1), (0, 0, 255), 2)
            mid_x = (pos1 + pos2) // 2
            mid_y = (self.line_pos_1 + self.line_pos_2) // 2
            cv2.circle(display, (mid_x, mid_y), 6, (0, 0, 255), -1)
        if pos1 > 0:
            cv2.circle(display, (pos1, self.line_pos_1), 5, (0, 255, 0), -1)
        if pos2 > 0:
            cv2.circle(display, (pos2, self.line_pos_2), 5, (0, 255, 0), -1)
        cv2.putText(display, "LINE_FOLLOW", (10, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        status = "TRACKING" if (pos1 > 0 or pos2 > 0) else "SEARCHING"
        cv2.putText(display, status, (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if status == "TRACKING" else (0, 0, 255), 2)

        overlay_rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        with self._overlay_lock:
            self.overlay_frame = overlay_rgb

        if self.on_line_found:
            self.on_line_found(self.line_pos, self.line_angle)

    def _find_hand(self, frame):
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        with self._hand_lock:
            low  = self._hand_low.copy()
            high = self._hand_high.copy()

        # Handle hue wrap-around: if low[0] > high[0], build two masks.
        if low[0] <= high[0]:
            mask = cv2.inRange(hsv, low, high)
        else:
            mask1 = cv2.inRange(hsv, np.array([0, low[1], low[2]]),
                                       np.array([high[0], high[1], high[2]]))
            mask2 = cv2.inRange(hsv, np.array([low[0], low[1], low[2]]),
                                       np.array([179, high[1], high[2]]))
            mask = cv2.bitwise_or(mask1, mask2)

        kernel_large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_large, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_small, iterations=2)
        mask = cv2.GaussianBlur(mask, (5, 5), 0)
        _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

        self.hand_detected = False
        h, w = bgr.shape[:2]
        cx_center, cy_center = w // 2, h // 2
        display = bgr.copy()

        if contours:
            c = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(c)
            if area > 800:
                M = cv2.moments(c)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    self.hand_pos = [cx, cy]
                    self.hand_area = int(area)
                    self.hand_detected = True

                    cv2.drawContours(display, [c], -1, (0, 255, 0), 2)
                    cross_len = 25
                    cv2.line(display, (cx - cross_len, cy), (cx + cross_len, cy), (0, 0, 255), 2)
                    cv2.line(display, (cx, cy - cross_len), (cx, cy + cross_len), (0, 0, 255), 2)
                    cv2.circle(display, (cx, cy), 18, (0, 0, 255), 2)
                    cv2.circle(display, (cx, cy), 3, (0, 0, 255), -1)
                    cv2.line(display, (cx_center, cy_center), (cx, cy), (0, 255, 255), 1)
                    cv2.putText(display, f"LOCK ({cx},{cy})", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                else:
                    self._draw_searching(display, cx_center, cy_center)
            else:
                self._draw_searching(display, cx_center, cy_center)
        else:
            self._draw_searching(display, cx_center, cy_center)

        cv2.putText(display, "HAND_TRACK", (10, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        overlay_rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        with self._overlay_lock:
            self.overlay_frame = overlay_rgb

        if self.on_hand_found:
            if self.hand_detected:
                shake = self.on_hand_found(self.hand_pos, self.hand_area)
                if shake:
                    self.hand_detected = False
                    return
            else:
                self.on_hand_found([0, 0], 0)

    @staticmethod
    def _draw_searching(display, cx, cy):
        cross_len = 20
        cv2.line(display, (cx - cross_len, cy), (cx + cross_len, cy), (0, 255, 0), 1)
        cv2.line(display, (cx, cy - cross_len), (cx, cy + cross_len), (0, 255, 0), 1)
        cv2.circle(display, (cx, cy), 15, (0, 255, 0), 1)
        cv2.putText(display, "SEARCHING...", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)


class Camera(BaseCamera):
    """Picamera2-backed camera with optional CV overlays."""

    _instance_lock = threading.Lock()
    _instance = None

    @classmethod
    def get_instance(cls):
        """Singleton accessor so the MJPEG route and the autonomous
        controller share the same Camera / capture thread."""
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
        self._init_camera()
        super().__init__(target_fps=CAMERA_FPS)

    # ------------------------------------------------------------------
    # Picamera2 lifecycle
    # ------------------------------------------------------------------
    def _init_camera(self):
        with self._picam_lock:
            if self._picam is not None:
                return
            if Picamera2 is None:
                raise RuntimeError("picamera2 not installed")
            try:
                self._picam = Picamera2()
                cfg = self._picam.create_preview_configuration(
                    main={"size": CAMERA_RESOLUTION, "format": "RGB888"}
                )
                self._picam.configure(cfg)
                if CAMERA_FLIP_HORIZONTAL:
                    self._picam.set_control("flip_h", True)
                if CAMERA_FLIP_VERTICAL:
                    self._picam.set_control("flip_v", True)
                try:
                    self._picam.set_controls({
                        "FrameRate": CAMERA_FPS,
                        "AeEnable": True,
                        "AwbEnable": True,
                        "NoiseReductionMode": 1,
                    })
                except Exception as e:
                    logger.warning(f"[Camera] Control setting failed: {e}")
                self._picam.start()
                self._reconnect_count = 0
                logger.info(f"[Camera] Picamera2 started "
                            f"({CAMERA_RESOLUTION} @ {CAMERA_FPS}fps q={CAMERA_JPEG_QUALITY}%)")
            except Exception as e:
                self._picam = None
                raise RuntimeError(f"Camera init failed: {e}")

    def _restart_camera(self):
        with self._picam_lock:
            self._reconnect_count += 1
            backoff = min(5.0, 0.5 * (2 ** min(self._reconnect_count, 4)))
            logger.warning(f"[Camera] Restarting (attempt {self._reconnect_count}, "
                           f"wait {backoff:.1f}s)...")
            if self._picam:
                try:
                    self._picam.stop()
                except Exception:
                    pass
                try:
                    self._picam.close()
                except Exception:
                    pass
                self._picam = None
        time.sleep(backoff)
        try:
            self._init_camera()
            logger.info("[Camera] Restart successful")
        except Exception as e:
            logger.error(f"[Camera] Restart failed: {e}")

    # ------------------------------------------------------------------
    # Frame generator
    # ------------------------------------------------------------------
    def frames(self):
        """Yield JPEG-encoded bytes forever.

        Resilient: a single bad frame logs + continues. Many bad frames
        in a row trigger a full camera restart.
        """
        # Make sure the camera is initialised before we start looping.
        try:
            self._init_camera()
        except Exception as e:
            logger.error(f"[Camera] Initial init failed: {e}")

        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 10

        while True:
            try:
                with self._picam_lock:
                    picam = self._picam
                if picam is None:
                    self._restart_camera()
                    continue

                raw = picam.capture_array()
                if raw is None or len(raw.shape) != 3:
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        logger.error("[Camera] Too many bad frames — restarting")
                        self._restart_camera()
                        consecutive_errors = 0
                    time.sleep(0.01)
                    continue
                consecutive_errors = 0

                # CV overlay (non-blocking — uses the latest processed overlay).
                if self.cv_thread.cv_mode != CV_NONE:
                    self.cv_thread.submit_frame(raw)
                    overlay = self.cv_thread.get_overlay()
                    frame = overlay if overlay is not None else raw
                else:
                    frame = raw

                # Encode to JPEG. If encoding fails, skip this frame.
                ok, jpg = cv2.imencode('.jpg', frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_QUALITY])
                if ok:
                    yield jpg.tobytes()
                else:
                    yield b''
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"[Camera] Frame error: {e}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    self._restart_camera()
                    consecutive_errors = 0
                else:
                    time.sleep(0.05)

    # ------------------------------------------------------------------
    # CV mode + colour control
    # ------------------------------------------------------------------
    def set_cv_mode(self, mode):
        self.cv_thread.cv_mode = mode
        if mode == CV_NONE:
            with self.cv_thread._overlay_lock:
                self.cv_thread.overlay_frame = None

    def set_hand_color(self, h_low, s_low, v_low, h_high, s_high, v_high):
        self.cv_thread.set_hand_color(h_low, s_low, v_low, h_high, s_high, v_high)

    def get_hand_color(self):
        return self.cv_thread.get_hand_color()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def shutdown(self):
        try:
            self.cv_thread.stop()
        except Exception:
            pass
        BaseCamera.shutdown(self)
        with self._picam_lock:
            if self._picam:
                try:
                    self._picam.stop()
                except Exception:
                    pass
                try:
                    self._picam.close()
                except Exception:
                    pass
                self._picam = None
