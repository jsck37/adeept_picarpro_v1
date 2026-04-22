"""
Camera + OpenCV processing module.
Optimized from v1:
- Single JPEG encoding per frame (v1 encoded twice!)
- FPS control via BaseCamera
- Cleaner CV mode switching
- No redundant Picamera2 instances
- Better resource management
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
    CV_COLOR_LOWER_H, CV_COLOR_LOWER_S, CV_COLOR_LOWER_V,
    CV_COLOR_UPPER_H, CV_COLOR_UPPER_S, CV_COLOR_UPPER_V,
    CV_LINE_POS_1, CV_LINE_POS_2, CV_WATCHDOG_THRESHOLD,
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
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=self.watchdog_threshold, detectShadows=True
        )

        # Output data
        self.color_pos = [0, 0]       # [x, y] of tracked color center
        self.line_pos = [0, 0]        # [pos1, pos2] for line following
        self.line_angle = 0           # Angle of detected line
        self.motion_detected = False  # Watchdog motion flag
        self.frame_size = list(CAMERA_RESOLUTION)

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
        """Submit a new frame for processing."""
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

        h, w = frame.shape[:2]
        self.frame_size = [w, h]

        if self.cv_mode == CV_MODE_FIND_COLOR:
            self._find_color(frame)
        elif self.cv_mode == CV_MODE_FIND_LINE:
            self._find_line(frame)
        elif self.cv_mode == CV_MODE_WATCHDOG:
            self._watchdog(frame)

    def _find_color(self, frame):
        """Find the largest contour of the target color using HSV filtering."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.color_lower, self.color_upper)
        mask = cv2.erode(mask, None, iterations=2)
        mask = cv2.dilate(mask, None, iterations=2)

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
        """Detect lines for line following using binary thresholding."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]

        # Binary threshold for dark lines on light surface
        _, binary = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)

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
        """Detect motion using background subtraction."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        fg_mask = self._bg_subtractor.apply(gray)
        _, thresh = cv2.threshold(fg_mask, 25, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=2)

        contours = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

        self.motion_detected = len(contours) > 0

        if self.on_motion_detected:
            self.on_motion_detected(self.motion_detected, contours)

    def set_color_range(self, lower_h, lower_s, lower_v, upper_h, upper_s, upper_v):
        """Update the HSV color tracking range."""
        self.color_lower = np.array([lower_h, lower_s, lower_v])
        self.color_upper = np.array([upper_h, upper_s, upper_v])


class Camera(BaseCamera):
    """
    PiCamera2 + OpenCV camera with efficient JPEG streaming.
    
    Key optimizations over v1:
    - Single JPEG encoding per frame (v1 did cv2.imencode TWICE)
    - FPS control (v1 had no throttle = max CPU)
    - Single Picamera2 instance (v1 created multiple competing instances)
    - Cleaner CV thread management
    """

    def __init__(self):
        self.cv_thread = CVThread()
        self.cv_thread.start()
        self._picam = None
        self._overlay_data = {}
        super().__init__(target_fps=CAMERA_FPS)

    def _init_camera(self):
        """Initialize the PiCamera2 instance (only once!)."""
        if self._picam is not None:
            return

        if Picamera2 is None:
            raise RuntimeError("picamera2 not installed")

        self._picam = Picamera2()
        config = self._picam.create_preview_configuration(
            main={"size": CAMERA_RESOLUTION, "format": "RGB888"}
        )
        self._picam.configure(config)

        if CAMERA_FLIP_HORIZONTAL:
            self._picam.set_control("flip_h", True)
        if CAMERA_FLIP_VERTICAL:
            self._picam.set_control("flip_v", True)

        self._picam.start()
        print(f"[Camera] Initialized at {CAMERA_RESOLUTION} @ {CAMERA_FPS}fps")

    def frames(self):
        """Generator that yields JPEG-encoded frames."""
        self._init_camera()

        while True:
            try:
                # Capture frame
                frame = self._picam.capture_array()

                # Convert RGB to BGR for OpenCV
                if frame is not None and len(frame.shape) == 3:
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:
                    continue

                # Submit to CV thread if a mode is active
                if self.cv_thread.cv_mode != CV_MODE_NONE:
                    self.cv_thread.submit_frame(frame_bgr.copy())

                # Draw overlays
                frame_bgr = self._draw_overlays(frame_bgr)

                # SINGLE JPEG encoding (v1 did this twice!)
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_QUALITY]
                success, jpeg_data = cv2.imencode('.jpg', frame_bgr, encode_params)
                if success:
                    yield jpeg_data.tobytes()
                else:
                    # Skip this frame rather than crash
                    continue

            except Exception as e:
                print(f"[Camera] Frame capture error: {e}")
                time.sleep(0.1)

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
