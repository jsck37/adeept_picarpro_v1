import re, subprocess, threading, time


class SystemInfo:
    _voltage_state = False
    _voltage_last_event = 0.0
    _voltage_lock = threading.Lock()
    _voltage_started = False
    _VOLTAGE_HOLD_S = 8.0

    @staticmethod
    def get_cpu_temp():
        try:
            with open('/sys/class/thermal/thermal_zone0/temp') as f:
                return round(float(f.read().strip()) / 1000.0, 1)
        except Exception:
            return 0.0

    @staticmethod
    def get_cpu_usage():
        try:
            def _read():
                with open('/proc/stat') as f:
                    vals = [int(x) for x in f.readline().split()[1:]]
                return vals[3], sum(vals)
            i1, t1 = _read()
            time.sleep(0.3)
            i2, t2 = _read()
            dt = t2 - t1
            return round(100.0 * (1.0 - (i2 - i1) / dt), 1) if dt > 0 else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def get_ram_info():
        try:
            info = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    p = line.split()
                    if len(p) >= 2:
                        try:
                            info[p[0].rstrip(':')] = int(p[1])
                        except ValueError:
                            pass
            total = info.get('MemTotal', 0)
            avail = info.get('MemAvailable') or (
                info.get('MemFree', 0) + info.get('Buffers', 0) + info.get('Cached', 0))
            used = total - avail
            return {
                'total_mb': round(total / 1024),
                'used_mb': round(used / 1024),
                'percent': round(100.0 * used / total, 1) if total else 0.0,
            }
        except Exception:
            return {'total_mb': 0, 'used_mb': 0, 'percent': 0.0}

    @staticmethod
    def _start_voltage_watcher():
        if SystemInfo._voltage_started:
            return
        SystemInfo._voltage_started = True

        def _watch():
            try:
                proc = subprocess.Popen(
                    ['dmesg', '--follow', '--color=never'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1,
                )
                for line in proc.stdout:
                    if 'Under-voltage detected' in line:
                        with SystemInfo._voltage_lock:
                            SystemInfo._voltage_state = True
                            SystemInfo._voltage_last_event = time.monotonic()
            except Exception:
                SystemInfo._voltage_started = False

        threading.Thread(target=_watch, daemon=True).start()

        def _janitor():
            while True:
                time.sleep(2.0)
                with SystemInfo._voltage_lock:
                    if SystemInfo._voltage_state:
                        age = time.monotonic() - SystemInfo._voltage_last_event
                        if age > SystemInfo._VOLTAGE_HOLD_S:
                            SystemInfo._voltage_state = False

        threading.Thread(target=_janitor, daemon=True).start()

    @staticmethod
    def is_low_voltage():
        if not SystemInfo._voltage_started:
            try:
                SystemInfo._start_voltage_watcher()
            except Exception:
                pass
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
