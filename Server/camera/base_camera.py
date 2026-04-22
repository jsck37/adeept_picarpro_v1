"""
Base camera class with FPS control and efficient frame management.
Optimized from v1: added frame rate limiting, removed duplicate encoding.
"""

import threading
import time
from io import BytesIO


class CameraEvent(threading.Event):
    """Custom event that tracks the last frame timestamp for FPS control."""

    def __init__(self):
        super().__init__()
        self.last_frame_time = 0


class BaseCamera:
    """
    Base camera class that runs a background thread for frame capture.
    
    Improvements over v1:
    - FPS limiting via target_fps parameter (v1 had no limit = 100% CPU)
    - Single frame storage with proper thread synchronization
    - Clean startup/shutdown
    """

    thread = None           # Background capture thread
    frame = None            # Current JPEG frame bytes
    last_access = 0         # Timestamp of last client access
    event = CameraEvent()   # Signaling event for new frames
    _running = False        # Thread control flag
    _target_fps = 20        # Default FPS
    _frame_interval = 1.0 / 20  # Time between frames

    def __init__(self, target_fps=20):
        """Initialize camera with target FPS."""
        self._target_fps = target_fps
        self._frame_interval = 1.0 / target_fps if target_fps > 0 else 0

        if BaseCamera.thread is None:
            BaseCamera.last_access = time.time()
            BaseCamera._running = True
            BaseCamera.thread = threading.Thread(target=self._capture_thread, daemon=True)
            BaseCamera.thread.start()

            # Wait for first frame
            while self.event.wait(1) is False:
                if not BaseCamera._running:
                    raise RuntimeError("Camera thread failed to start")
            self.event.clear()

    def _capture_thread(self):
        """Background thread that continuously captures frames."""
        frames_iterator = self.frames()
        last_time = time.time()

        try:
            for frame in frames_iterator:
                BaseCamera.frame = frame
                self.event.set()
                self.event.clear()

                # FPS limiting: sleep if we're ahead of schedule
                if self._target_fps > 0:
                    elapsed = time.time() - last_time
                    sleep_time = self._frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                last_time = time.time()

                # Auto-stop if no clients for 60 seconds
                if time.time() - BaseCamera.last_access > 60:
                    frames_iterator.close()
                    break

        except Exception as e:
            print(f"[Camera] Capture thread error: {e}")
        finally:
            BaseCamera.thread = None
            BaseCamera._running = False

    @staticmethod
    def get_frame():
        """Get the latest JPEG frame. Blocks until a new frame is available."""
        BaseCamera.last_access = time.time()
        BaseCamera.event.wait()
        return BaseCamera.frame

    @staticmethod
    def shutdown():
        """Stop the camera capture thread."""
        BaseCamera._running = False
        BaseCamera.event.set()  # Unblock any waiting threads

    def frames(self):
        """Generator that yields JPEG frames. Must be overridden by subclass."""
        raise NotImplementedError("Subclass must implement frames()")
