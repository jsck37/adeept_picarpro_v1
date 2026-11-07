"""Приёмник кадров аппаратного MJPEG-энкодера picamera2."""
import io


class FrameSink(io.BufferedIOBase):
    """picamera2 вызывает write() один раз на каждый готовый JPEG-кадр."""

    def __init__(self, on_frame):
        super().__init__()
        self._on_frame = on_frame

    def write(self, buf):
        try:
            self._on_frame(bytes(buf))
        except Exception:
            pass
        return len(buf)
