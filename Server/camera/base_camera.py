"""Base camera — background capture thread with FPS control."""

import threading, time
from Server.logger import logger

class BaseCamera:
    thread = None
    frame = None
    last_access = 0
    event = threading.Event()
    _running = False
    _target_fps = 30
    _frame_interval = 1.0 / 30

    def __init__(self, target_fps=30):
        self._target_fps = target_fps
        self._frame_interval = 1.0 / target_fps if target_fps > 0 else 0
        if BaseCamera.thread is None:
            BaseCamera.last_access = time.time()
            BaseCamera._running = True
            BaseCamera.thread = threading.Thread(target=self._capture_thread, daemon=True)
            BaseCamera.thread.start()
            while not self.event.wait(1):
                if not BaseCamera._running:
                    raise RuntimeError("Camera thread failed to start")
            self.event.clear()

    def _capture_thread(self):
        frames_gen = self.frames()
        last_time = time.time()
        try:
            for frame in frames_gen:
                BaseCamera.frame = frame
                self.event.set()
                self.event.clear()
                if self._target_fps > 0:
                    sleep = self._frame_interval - (time.time() - last_time)
                    if sleep > 0:
                        time.sleep(sleep)
                last_time = time.time()
                if time.time() - BaseCamera.last_access > 60:
                    frames_gen.close()
                    break
        except Exception as e:
            logger.error(f"[Camera] Thread error: {e}")
        finally:
            BaseCamera.thread = None
            BaseCamera._running = False

    @staticmethod
    def get_frame():
        BaseCamera.last_access = time.time()
        BaseCamera.event.wait()
        return BaseCamera.frame

    @staticmethod
    def shutdown():
        BaseCamera._running = False
        BaseCamera.event.set()

    def frames(self):
        raise NotImplementedError
