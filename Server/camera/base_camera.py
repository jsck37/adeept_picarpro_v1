"""Base camera class — instance-scoped, no class-level mutable state.

Design goals:
  * Each Camera instance owns its own background thread + frame buffer.
    The previous BaseCamera stored everything on the class itself, which
    meant two Camera instances would step on each other's toes and
    restart each other's threads.
  * The capture thread is resilient: it catches per-frame errors and
    keeps going instead of dying and waiting for an explicit restart.
  * ``get_frame()`` returns the most recent frame without blocking for
    more than ~0.5s; if no frame is available, it returns None and the
    caller can decide what to do (e.g. send a placeholder).
  * Inactivity timeout removed — the camera thread runs forever while
    the server is up. This avoids the "camera thread stopped, please
    restart" message users kept seeing.
"""

import threading
import time

from Server.logger import logger


class BaseCamera:
    def __init__(self, target_fps=30):
        self._target_fps = target_fps
        self._frame_interval = 1.0 / target_fps if target_fps > 0 else 0
        self._frame = None
        self._frame_lock = threading.Lock()
        self._frame_ready = threading.Event()
        self._running = True
        self._thread = None
        self._last_access = time.time()
        self._start_thread()

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------
    def _start_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._last_access = time.time()
        self._frame_ready.clear()
        self._thread = threading.Thread(target=self._capture_thread, daemon=True)
        self._thread.start()

    def _capture_thread(self):
        """Run ``self.frames()`` and pump each frame into the buffer.

        The frames() generator is expected to yield JPEG-encoded bytes
        forever. If it raises, we log + sleep + retry (calling frames()
        again to get a fresh generator).
        """
        while self._running:
            try:
                frames_gen = self.frames()
                last_yield = time.monotonic()
                for frame in frames_gen:
                    if not self._running:
                        break
                    if frame is None:
                        continue
                    with self._frame_lock:
                        self._frame = frame
                    self._frame_ready.set()
                    self._frame_ready.clear()

                    # Pace to target_fps.
                    if self._frame_interval > 0:
                        elapsed = time.monotonic() - last_yield
                        sleep = self._frame_interval - elapsed
                        if sleep > 0:
                            time.sleep(sleep)
                    last_yield = time.monotonic()
            except Exception as e:
                logger.error(f"[Camera] Capture thread error: {e}")
                time.sleep(1.0)   # back off before restarting the generator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_frame(self):
        """Return the latest frame (bytes) or None if nothing is ready.

        Updates ``last_access`` so the camera knows someone is still
        watching. Never blocks for more than 0.5s.
        """
        self._last_access = time.time()
        if self._thread is None or not self._thread.is_alive():
            logger.info("[Camera] Thread not running — restarting...")
            try:
                self._start_thread()
            except Exception as e:
                logger.error(f"[Camera] Thread restart failed: {e}")
                return None
        # Wait for a fresh frame (or timeout quickly).
        self._frame_ready.wait(timeout=0.5)
        with self._frame_lock:
            return self._frame

    def shutdown(self):
        self._running = False
        self._frame_ready.set()
        if self._thread and self._thread.is_alive():
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
        self._thread = None

    # ------------------------------------------------------------------
    # To be overridden by subclasses
    # ------------------------------------------------------------------
    def frames(self):
        raise NotImplementedError
