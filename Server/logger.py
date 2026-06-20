import os, sys, threading, time
from collections import deque
from loguru import logger as _log


class LogBuffer:
    def __init__(self, maxlen=2000):
        self._lines = deque(maxlen=maxlen)
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
                subs = list(self._subscribers)
            for cb in subs:
                try:
                    cb(ts, stripped)
                except Exception:
                    pass

    def get_lines(self, last_n=200):
        with self._lock:
            return list(self._lines)[-last_n:]

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

    def subscribe(self, cb):
        with self._lock:
            self._subscribers.append(cb)

    def unsubscribe(self, cb):
        with self._lock:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

    def clear(self):
        with self._lock:
            self._lines.clear()


log_buffer = LogBuffer()


def _logbuffer_sink(message):
    try:
        rec = message.record
        ts = rec['time']
        ts_str = ts.strftime('%H:%M:%S.') + f'{ts.microsecond // 1000:03d}'
        level = rec['level'].name
        text = rec['message']
        line_no = rec['line']
        fname = rec['file'].name
        log_buffer.write(f'{ts_str} | {level:<7} | {line_no}:{fname} {text}')
    except Exception:
        pass


_log.remove()
_log.level('ERROR', color='<red><bold>')
_log.level('WARNING', color='<yellow><bold>')
_log.level('INFO', color='<cyan><bold>')

_log.add(_logbuffer_sink, format='{message}', level='INFO')

_log.add(
    sys.stderr,
    format='<white>{time:HH:mm:ss.SSS}</white> | <level>{level:<7}</level> | <green>{line}</green>:<green>{file}</green> - <white>{message}</white>',
    level='INFO',
)

logger = _log
