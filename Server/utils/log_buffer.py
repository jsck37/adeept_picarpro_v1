import threading
import time


class LogBuffer:

    def __init__(self, max_lines=2000):
        self._max = max_lines
        self._lines = []
        self._lock = threading.Lock()
        self._subscribers = []

    def write(self, text):
        if not text:
            return
        ts = time.time()
        for line in text.splitlines():
            stripped = line.rstrip()
            if not stripped:
                continue
            with self._lock:
                self._lines.append((ts, stripped))
                if len(self._lines) > self._max:
                    self._lines = self._lines[-self._max:]
                subs = list(self._subscribers)
            for cb in subs:
                try:
                    cb(stripped)
                except Exception:
                    pass

    def get_lines(self, last_n=200):
        with self._lock:
            return list(self._lines[-last_n:])

    def get_lines_since(self, after_ts=0.0, max_lines=500):
        with self._lock:
            result = []
            for ts, text in reversed(self._lines):
                if ts <= after_ts:
                    break
                result.append((ts, text))
                if len(result) >= max_lines:
                    break
            result.reverse()
            return result

    def subscribe(self, callback):
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass


log_buffer = LogBuffer()
