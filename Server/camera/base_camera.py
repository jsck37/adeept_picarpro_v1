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
    _camera_instance = None

    def __init__(self, target_fps=30):
        self._target_fps = target_fps
        self._frame_interval = 1.0 / target_fps if target_fps > 0 else 0
        BaseCamera._camera_instance = self
        self._start_thread()

    def _start_thread(self):
        if BaseCamera.thread is not None and BaseCamera.thread.is_alive():
            return
        BaseCamera.last_access = time.time()
        BaseCamera._running = True
        BaseCamera.event.clear()
        BaseCamera.thread = threading.Thread(target=self._capture_thread, daemon=True)
        BaseCamera.thread.start()
        deadline = time.time() + 5
        while not self.event.wait(0.5):
            if not BaseCamera._running:
                raise RuntimeError("Camera thread failed to start")
            if time.time() > deadline:
                logger.warning("[Camera] First frame timeout, but continuing...")
                break
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
                if time.time() - BaseCamera.last_access > 120:
                    logger.info("[Camera] 120s inactivity — stopping capture thread")
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
        if BaseCamera.thread is None or not BaseCamera.thread.is_alive():
            logger.info("[Camera] Thread not running — restarting...")
            if BaseCamera._camera_instance is not None:
                try:
                    BaseCamera._camera_instance._start_thread()
                except Exception as e:
                    logger.error(f"[Camera] Thread restart failed: {e}")
                    return None
            else:
                return None
        BaseCamera.event.wait(timeout=2)
        return BaseCamera.frame

    @staticmethod
    def shutdown():
        BaseCamera._running = False
        BaseCamera.event.set()
        BaseCamera._camera_instance = None

    def frames(self):
        raise NotImplementedError
