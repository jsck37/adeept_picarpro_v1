"""Camera + OpenCV processing module.

Configurable pixel format for color debugging, single JPEG encoding,
FPS control, frame skip when CV lags (Pi 3B+ friendly).

Color format options (CAMERA_COLOR_FORMAT in config.py):
  'rgb2bgr'  — Capture RGB888, convert to BGR for OpenCV (default)
  'bgr2rgb'  — Capture BGR888, convert to RGB for OpenCV
  'bgr_raw'  — Capture BGR888, use as-is (no conversion)
  'rgb_raw'  — Capture RGB888, use as-is (no conversion)
"""

import threading
import time
import math
import cv2
import numpy as np

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

from Server.camera.base_camera import BaseCamera
from Server.config import (
    CAMERA_RESOLUTION, CAMERA_FPS, CAMERA_JPEG_QUALITY,
    CAMERA_FLIP_HORIZONTAL, CAMERA_FLIP_VERTICAL,
    CAMERA_COLOR_FORMAT,
    CV_COLOR_LOWER_H, CV_COLOR_LOWER_S, CV_COLOR_LOWER_V,
    CV_COLOR_UPPER_H, CV_COLOR_UPPER_S, CV_COLOR_UPPER_V,
    CV_LINE_POS_1, CV_LINE_POS_2, CV_LINE_THRESHOLD,
    CV_WATCHDOG_THRESHOLD, CV_WATCHDOG_BLUR_SIZE,
)
from Server.utils.kalman import KalmanFilter


# ---------------------------------------------------------------------------
# CV mode constants
# ---------------------------------------------------------------------------
CV_MODE_NONE = "none"
CV_MODE_FIND_COLOR = "findColor"
CV_MODE_FIND_LINE = "findlineCV"
CV_MODE_WATCHDOG = "watchDog"


class CVThread(threading.Thread):
    """
    Computer vision processing thread.
    Runs asynchronously to avoid blocking the camera capture loop.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._flag = threading.Event()
        self._flag.clear()
        self._running = True
        self._frame = None
        self._result = None
        self._result_lock = threading.Lock()

        # CV mode and parameters
        self.cv_mode = CV_MODE_NONE
        self.color_lower = np.array([CV_COLOR_LOWER_H, CV_COLOR_LOWER_S, CV_COLOR_LOWER_V])
        self.color_upper = np.array([CV_COLOR_UPPER_H, CV_COLOR_UPPER_S, CV_COLOR_UPPER_V])
        self.line_pos_1 = CV_LINE_POS_1
        self.line_pos_2 = CV_LINE_POS_2
        self.watchdog_threshold = CV_WATCHDOG_THRESHOLD

        # Kalman filter for color tracking
        self.kf_x = KalmanFilter()
        self.kf_y = KalmanFilter()

        # Background subtractor for watchdog mode
        # Reduced history=200 (vs 500) — saves RAM on Pi 3B+,
        # still accurate enough for motion detection
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=self.watchdog_threshold, detectShadows=True
        )

        # Pre-allocated morphological kernel for color detection
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        # Output data
        self.color_pos = [0, 0]       # [x, y] of tracked color center
        self.line_pos = [0, 0]        # [pos1, pos2] for line following
        self.line_angle = 0           # Angle of detected line
        self.motion_detected = False  # Watchdog motion flag
        self.frame_size = list(CAMERA_RESOLUTION)

        # Line detection threshold (configurable)
        self.line_threshold = CV_LINE_THRESHOLD

        # Watchdog blur kernel size
        self.watchdog_blur_size = CV_WATCHDOG_BLUR_SIZE

        # Frame skip: if CV is still processing, skip the next submission
        self._processing = False

        # Callback for robot control
        self.on_color_found = None
        self.on_line_found = None
        self.on_motion_detected = None

    def run(self):
        """Main thread loop - processes frames when available."""
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            self._flag.clear()

            if self._frame is None:
                continue

            try:
                self._process_frame()
            except Exception as e:
                print(f"[CV] Processing error: {e}")

    def submit_frame(self, frame):
        """Submit a new frame for processing.
        Skips if the previous frame is still being processed
        (avoids memory buildup on slow Pi 3B+).
        """
        if self._processing:
            return  # Skip — CV thread is still busy
        self._frame = frame
        self._flag.set()

    def stop(self):
        """Stop the CV thread."""
        self._running = False
        self._flag.set()

    def pause(self):
        """Pause CV processing."""
        self._flag.clear()

    def resume(self):
        """Resume CV processing."""
        self._flag.set()

    def _process_frame(self):
        """Process the current frame based on active CV mode."""
        frame = self._frame
        if frame is None:
            return

        self._processing = True
        try:
            h, w = frame.shape[:2]
            self.frame_size = [w, h]

            if self.cv_mode == CV_MODE_FIND_COLOR:
                self._find_color(frame)
            elif self.cv_mode == CV_MODE_FIND_LINE:
                self._find_line(frame)
            elif self.cv_mode == CV_MODE_WATCHDOG:
                self._watchdog(frame)
        finally:
            self._processing = False

    def _find_color(self, frame):
        """Find the largest contour of the target color using HSV filtering.
        Optimized: uses morphological OPEN (erode+dilate in one pass)
        instead of separate erode + dilate calls — faster on Pi 3B+.
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.color_lower, self.color_upper)

        # MORPH_OPEN = erode then dilate in single call (faster than separate)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._morph_kernel, iterations=2)

        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

        if contours:
            c = max(contours, key=cv2.contourArea)
            ((x, y), radius) = cv2.minEnclosingCircle(c)
            M = cv2.moments(c)

            if M["m00"] > 0 and radius > 5:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])

                # Kalman filter for smoother tracking
                self.kf_x.filter(cx / self.frame_size[0])
                self.kf_y.filter(cy / self.frame_size[1])
                self.color_pos = [int(self.kf_x.get() * self.frame_size[0]),
                                  int(self.kf_y.get() * self.frame_size[1])]

                if self.on_color_found:
                    self.on_color_found(self.color_pos, radius)

    def _find_line(self, frame):
        """Detect lines for line following using binary thresholding.
        Threshold is now configurable via CV_LINE_THRESHOLD in config.py.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]

        # Binary threshold for dark lines on light surface
        _, binary = cv2.threshold(gray, self.line_threshold, 255, cv2.THRESH_BINARY_INV)

        # Scan two horizontal lines
        scan1 = binary[self.line_pos_1, :]
        scan2 = binary[self.line_pos_2, :]

        pos1 = self._find_line_center(scan1, w)
        pos2 = self._find_line_center(scan2, w)

        self.line_pos = [pos1, pos2]

        if pos1 > 0 and pos2 > 0:
            self.line_angle = math.degrees(math.atan2(pos2 - pos1, self.line_pos_1 - self.line_pos_2))
        else:
            self.line_angle = 0

        if self.on_line_found:
            self.on_line_found(self.line_pos, self.line_angle)

    @staticmethod
    def _find_line_center(scan_line, width):
        """Find the center of a line in a single scan row."""
        indices = np.where(scan_line > 0)[0]
        if len(indices) > 0:
            return int(np.mean(indices))
        return 0

    def _watchdog(self, frame):
        """Detect motion using background subtraction.
        Optimized for Pi 3B+: smaller blur kernel (7x7 vs 21x21),
        fewer dilate iterations, minimum contour area filter.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, self.watchdog_blur_size, 0)

        fg_mask = self._bg_subtractor.apply(gray)
        _, thresh = cv2.threshold(fg_mask, 25, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=1)

        contours = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

        # Filter tiny noise contours (minimum 500px area)
        significant = [c for c in contours if cv2.contourArea(c) > 500]
        self.motion_detected = len(significant) > 0

        if self.on_motion_detected:
            self.on_motion_detected(self.motion_detected, significant)

    def set_color_range(self, lower_h, lower_s, lower_v, upper_h, upper_s, upper_v):
        """Update the HSV color tracking range."""
        self.color_lower = np.array([lower_h, lower_s, lower_v])
        self.color_upper = np.array([upper_h, upper_s, upper_v])


class Camera(BaseCamera):
    """PiCamera2 + OpenCV with JPEG streaming.

    Configurable pixel format for color debugging.
    Default: capture RGB888, convert to BGR for OpenCV.
    """

    # Color format options for live switching
    COLOR_FORMATS = ['rgb2bgr', 'bgr2rgb', 'bgr_raw', 'rgb_raw']

    def __init__(self):
        self.cv_thread = CVThread()
        self.cv_thread.start()
        self._picam = None
        self._overlay_data = {}
        self._color_format = CAMERA_COLOR_FORMAT
        self._capture_format = 'RGB888'  # picamera2 capture format
        super().__init__(target_fps=CAMERA_FPS)

    def set_color_format(self, fmt):
        """Switch color format at runtime for debugging.
        Re-initializes the camera with the new format."""
        if fmt not in self.COLOR_FORMATS:
            print(f"[Camera] Unknown format: {fmt}, using rgb2bgr")
            fmt = 'rgb2bgr'
        self._color_format = fmt

        # Determine picamera2 capture format
        if fmt in ('rgb2bgr', 'rgb_raw'):
            self._capture_format = 'RGB888'
        else:
            self._capture_format = 'BGR888'

        # Re-initialize camera with new format
        if self._picam is not None:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                pass
            self._picam = None

        print(f"[Camera] Color format set to: {fmt} (capture: {self._capture_format})")

    def _init_camera(self):
        """Initialize the PiCamera2 instance (only once per format!).
        The capture format depends on the current color_format setting.
        """
        if self._picam is not None:
            return

        if Picamera2 is None:
            raise RuntimeError("picamera2 not installed")

        self._picam = Picamera2()
        config = self._picam.create_preview_configuration(
            main={"size": CAMERA_RESOLUTION, "format": self._capture_format}
        )
        self._picam.configure(config)

        if CAMERA_FLIP_HORIZONTAL:
            self._picam.set_control("flip_h", True)
        if CAMERA_FLIP_VERTICAL:
            self._picam.set_control("flip_v", True)

        self._picam.start()
        print(f"[Camera] Initialized at {CAMERA_RESOLUTION} @ {CAMERA_FPS}fps "
              f"quality={CAMERA_JPEG_QUALITY}% (capture:{self._capture_format}, fmt:{self._color_format})")

    def frames(self):
        """Generator that yields JPEG-encoded frames.
        Color conversion depends on CAMERA_COLOR_FORMAT setting.
        """
        self._init_camera()

        while True:
            try:
                frame_raw = self._picam.capture_array()

                if frame_raw is None or len(frame_raw.shape) != 3:
                    continue

                # Apply color conversion based on format setting
                frame = self._convert_frame(frame_raw)

                # Submit to CV thread if a mode is active (CV works in BGR)
                # No .copy() needed — submit_frame skips if still processing
                if self.cv_thread.cv_mode != CV_MODE_NONE:
                    self.cv_thread.submit_frame(frame.copy())

                # Draw overlays
                frame = self._draw_overlays(frame)

                # JPEG encoding (OpenCV imencode expects BGR input)
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_QUALITY]
                success, jpeg_data = cv2.imencode('.jpg', frame, encode_params)
                if success:
                    yield jpeg_data.tobytes()
                else:
                    continue

            except Exception as e:
                print(f"[Camera] Frame capture error: {e}")
                time.sleep(0.1)

    def _convert_frame(self, frame_raw):
        """Convert raw captured frame based on current color format setting.

        OpenCV imencode() expects BGR input. All formats ultimately
        produce BGR output so JPEG encoding works correctly.

        Format logic:
          rgb2bgr  — capture RGB888 → convert to BGR (default, correct for most Pi)
          bgr2rgb  — capture BGR888 → swap R↔B channels (if rgb2bgr gives wrong colors)
          bgr_raw  — capture BGR888 → use as-is (BGR is already correct for imencode)
          rgb_raw  — capture RGB888 → use as-is (will show swapped colors in browser
                     to help diagnose which channel is which)
        """
        fmt = self._color_format

        if fmt == 'rgb2bgr':
            # Standard: capture RGB888, convert to BGR for OpenCV
            return cv2.cvtColor(frame_raw, cv2.COLOR_RGB2BGR)
        elif fmt == 'bgr2rgb':
            # Alternative: capture BGR888, swap channels
            return cv2.cvtColor(frame_raw, cv2.COLOR_BGR2RGB)
        elif fmt == 'bgr_raw':
            # Raw BGR888 — already in BGR, correct for OpenCV
            return frame_raw
        elif fmt == 'rgb_raw':
            # Raw RGB888 — NO conversion, colors will be swapped in JPEG
            # This is for DIAGNOSIS: if colors look correct in this mode,
            # it means the camera is actually outputting BGR888 not RGB888
            # and you should use 'bgr_raw' format.
            return frame_raw
        else:
            return cv2.cvtColor(frame_raw, cv2.COLOR_RGB2BGR)

    def _draw_overlays(self, frame):
        """Draw CV overlay information on the frame."""
        mode = self.cv_thread.cv_mode

        if mode == CV_MODE_FIND_COLOR:
            x, y = self.cv_thread.color_pos
            if x > 0 or y > 0:
                cv2.circle(frame, (x, y), 10, (0, 255, 0), 2)
                cv2.putText(frame, f"Color: ({x},{y})", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        elif mode == CV_MODE_FIND_LINE:
            pos1, pos2 = self.cv_thread.line_pos
            h, w = frame.shape[:2]
            # Draw scan lines
            cv2.line(frame, (0, self.cv_thread.line_pos_1), (w, self.cv_thread.line_pos_1),
                     (0, 255, 0), 1)
            cv2.line(frame, (0, self.cv_thread.line_pos_2), (w, self.cv_thread.line_pos_2),
                     (0, 255, 0), 1)
            # Draw detected points
            if pos1 > 0:
                cv2.circle(frame, (pos1, self.cv_thread.line_pos_1), 5, (0, 0, 255), -1)
            if pos2 > 0:
                cv2.circle(frame, (pos2, self.cv_thread.line_pos_2), 5, (0, 0, 255), -1)

        elif mode == CV_MODE_WATCHDOG:
            if self.cv_thread.motion_detected:
                cv2.putText(frame, "MOTION DETECTED", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        # Draw mode indicator
        mode_text = f"Mode: {mode}" if mode != CV_MODE_NONE else ""
        if mode_text:
            cv2.putText(frame, mode_text, (10, frame.shape[0] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        return frame

    def set_cv_mode(self, mode):
        """Change the CV processing mode."""
        self.cv_thread.cv_mode = mode

    def set_color_range(self, lower_h, lower_s, lower_v, upper_h, upper_s, upper_v):
        """Update HSV color range for color tracking."""
        self.cv_thread.set_color_range(lower_h, lower_s, lower_v, upper_h, upper_s, upper_v)

    def shutdown(self):
        """Clean up camera resources."""
        self.cv_thread.stop()
        BaseCamera.shutdown()
        if self._picam is not None:
            try:
                self._picam.stop()
            except Exception:
                pass
            self._picam = None
