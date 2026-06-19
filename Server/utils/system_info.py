import subprocess, threading, time


class SystemInfo:
    """System info: CPU temp / CPU usage / RAM / low-voltage detection.

    The low-voltage detector follows the Raspberry Pi firmware's
    `Under-voltage detected!` events from the kernel ring buffer.
    A background thread tails ``dmesg --follow`` and only counts
    events that arrive *while this process is running* — old dmesg
    history is NOT considered, so the OLED banner won't get stuck on
    from a brief sag that happened before the server started.
    """

    _voltage_state = False
    _voltage_last_event_ts = 0.0
    _voltage_lock = threading.Lock()
    _voltage_thread = None
    _voltage_started = False
    _VOLTAGE_HOLD_S = 8.0   # how long the warning stays on after the last event
    _VOLTAGE_CACHE_TTL = 1.0
    _last_voltage_check_ts = 0.0

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------
    @staticmethod
    def get_cpu_temp():
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000.0
            return round(temp, 1)
        except Exception:
            return 0.0

    @staticmethod
    def get_cpu_usage():
        try:
            def _read_stat():
                with open('/proc/stat', 'r') as f:
                    line = f.readline()
                values = [int(x) for x in line.split()[1:]]
                idle = values[3]
                total = sum(values)
                return idle, total

            idle1, total1 = _read_stat()
            time.sleep(0.5)
            idle2, total2 = _read_stat()

            diff_idle = idle2 - idle1
            diff_total = total2 - total1

            if diff_total > 0:
                return round(100.0 * (1.0 - diff_idle / diff_total), 1)
            return 0.0
        except Exception:
            return 0.0

    @staticmethod
    def get_ram_info():
        try:
            info = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    key = parts[0].rstrip(':')
                    try:
                        value = int(parts[1])
                    except ValueError:
                        continue
                    info[key] = value

            total = info.get('MemTotal', 0)
            if total == 0:
                return {'total': 0, 'used': 0, 'percent': 0,
                        'total_mb': 0, 'used_mb': 0}

            available = info.get('MemAvailable', None)
            if available is None:
                free = info.get('MemFree', 0)
                buffers = info.get('Buffers', 0)
                cached = info.get('Cached', 0)
                available = free + buffers + cached

            used = total - available
            percent = round(100.0 * used / total, 1) if total > 0 else 0.0

            total_mb = round(total / 1024)
            used_mb = round(used / 1024)

            return {
                'total': round(total / (1024 * 1024), 2),
                'used': round(used / (1024 * 1024), 2),
                'percent': percent,
                'total_mb': total_mb,
                'used_mb': used_mb,
            }
        except Exception:
            return {'total': 0, 'used': 0, 'percent': 0,
                    'total_mb': 0, 'used_mb': 0}

    # ------------------------------------------------------------------
    # Low-voltage detection
    # ------------------------------------------------------------------
    @staticmethod
    def _start_voltage_watcher():
        """Launch a background thread that tails ``dmesg --follow``.

        Only events observed while this process is running are counted;
        old dmesg history is explicitly *not* consulted. This prevents
        a single brief sag at boot from keeping the warning banner on
        forever.
        """
        if SystemInfo._voltage_started:
            return
        SystemInfo._voltage_started = True

        def _watch():
            try:
                # `dmesg --follow` (Debian 12+) streams new entries.
                # `--color=never` so we get plain text.
                # On older kernels without --follow, fall back to polling
                # `dmesg | tail` and compare timestamps.
                proc = subprocess.Popen(
                    ['dmesg', '--follow', '--color=never'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                for line in proc.stdout:
                    if ('Under-voltage detected' in line
                            or 'under-voltage detected' in line.lower()):
                        with SystemInfo._voltage_lock:
                            SystemInfo._voltage_state = True
                            SystemInfo._voltage_last_event_ts = time.monotonic()
            except Exception:
                # Fall back: poll every 5s.
                SystemInfo._voltage_started = False  # allow restart

        t = threading.Thread(target=_watch, daemon=True)
        t.start()
        SystemInfo._voltage_thread = t

        # Also start a small janitor that clears the flag after the hold
        # window has elapsed with no new events.
        def _janitor():
            while True:
                time.sleep(2.0)
                with SystemInfo._voltage_lock:
                    if SystemInfo._voltage_state:
                        age = time.monotonic() - SystemInfo._voltage_last_event_ts
                        if age > SystemInfo._VOLTAGE_HOLD_S:
                            SystemInfo._voltage_state = False

        threading.Thread(target=_janitor, daemon=True).start()

    @staticmethod
    def is_low_voltage() -> bool:
        # Lazily start the watcher the first time this is called.
        if not SystemInfo._voltage_started:
            try:
                SystemInfo._start_voltage_watcher()
            except Exception:
                pass

        # Cheap cache so callers can hit this every second without
        # worrying about lock contention.
        now = time.monotonic()
        if (now - SystemInfo._last_voltage_check_ts
                < SystemInfo._VOLTAGE_CACHE_TTL):
            return SystemInfo._voltage_state
        SystemInfo._last_voltage_check_ts = now

        with SystemInfo._voltage_lock:
            return SystemInfo._voltage_state

    @staticmethod
    def get_all():
        return {
            'cpu_temp': SystemInfo.get_cpu_temp(),
            'cpu_usage': SystemInfo.get_cpu_usage(),
            'ram': SystemInfo.get_ram_info(),
            'low_voltage': SystemInfo.is_low_voltage(),
        }
