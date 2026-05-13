#!/usr/bin/env python3
"""Thread-safe ring buffer for capturing print() output.

Replaces sys.stdout with a Tee that writes to both the real stdout
and a ring buffer.  The WebSocket handler can fetch recent lines
and subscribe to new ones.
"""

import io
import sys
import threading
import time


class LogBuffer:
    """Fixed-size ring buffer of log lines."""

    def __init__(self, max_lines=2000):
        self._max = max_lines
        self._lines = []          # list of (timestamp, text)
        self._lock = threading.Lock()
        self._subscribers = []    # list of callback(text)
        self._orig_stdout = None
        self._orig_stderr = None

    # ── capture control ────────────────────────────────────────────

    def install(self):
        """Replace sys.stdout/stderr with a Tee that feeds this buffer."""
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = _Tee(self._orig_stdout, self)
        sys.stderr = _Tee(self._orig_stderr, self)

    def uninstall(self):
        """Restore original stdout/stderr."""
        if self._orig_stdout:
            sys.stdout = self._orig_stdout
        if self._orig_stderr:
            sys.stderr = self._orig_stderr

    # ── write (called from Tee) ────────────────────────────────────

    def write(self, text):
        if not text:
            return
        ts = time.time()
        # Split multi-line text into individual lines
        for line in text.splitlines():
            stripped = line.rstrip()
            if not stripped:
                continue
            with self._lock:
                self._lines.append((ts, stripped))
                if len(self._lines) > self._max:
                    self._lines = self._lines[-self._max:]
                # Notify subscribers (copy list to avoid holding lock)
                subs = list(self._subscribers)
            for cb in subs:
                try:
                    cb(stripped)
                except Exception:
                    pass

    # ── read ───────────────────────────────────────────────────────

    def get_lines(self, last_n=200):
        """Return the last_n lines as list of (timestamp, text)."""
        with self._lock:
            return list(self._lines[-last_n:])

    def get_lines_since(self, after_ts=0.0, max_lines=500):
        """Return lines newer than *after_ts*."""
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

    # ── subscribers ────────────────────────────────────────────────

    def subscribe(self, callback):
        """Register *callback(text)* for every new log line."""
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass


class _Tee:
    """Wraps a stream so writes go to both the original stream and LogBuffer."""

    def __init__(self, original, log_buffer):
        self._original = original
        self._buffer = log_buffer

    def write(self, text):
        self._original.write(text)
        self._original.flush()
        self._buffer.write(text)

    def flush(self):
        self._original.flush()

    def fileno(self):
        return self._original.fileno()

    def isatty(self):
        return self._original.isatty()

    def __getattr__(self, name):
        return getattr(self._original, name)


# ── Global singleton ───────────────────────────────────────────────
log_buffer = LogBuffer()
