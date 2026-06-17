import re, subprocess, time


class SystemInfo:

    # Cache of the last dmesg under-voltage check.
    _last_voltage_check_ts = 0.0
    _last_voltage_check_result = False
    _VOLTAGE_CACHE_TTL = 1.5

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
    # Voltage / low-voltage detection
    # ------------------------------------------------------------------
    @staticmethod
    def _check_dmesg_under_voltage() -> bool:
        """Returns True if dmesg shows a recent under-voltage event.

        The Raspberry Pi firmware logs `Under-voltage detected!` to the
        kernel ring buffer whenever the 5V rail drops below ~4.65V — this
        is exactly the same source the desktop "Low voltage warning"
        notification listens to.
        """
        try:
            result = subprocess.run(
                ['dmesg', '--ctime', '--color=never'],
                capture_output=True, text=True, timeout=2.0,
            )
            if result.returncode != 0:
                return False
            # Only the last few hundred chars matter; if a warning was
            # recently logged (or is still pending) it'll be there.
            tail = result.stdout[-4096:]
            return ('Under-voltage detected' in tail
                    or 'under-voltage detected' in tail.lower())
        except Exception:
            return False

    @staticmethod
    def _check_vcgencmd_under_voltage() -> bool:
        """Fallback: read `vcgencmd measure_volts core` and threshold it.

        The BCM2835/2837/2712 core rail is normally ~1.2V — if it sags
        below the configured threshold the Pi is almost certainly
        under-volted on the 5V rail.
        """
        try:
            from config import LOW_VOLTAGE_THRESHOLD_V
        except Exception:
            LOW_VOLTAGE_THRESHOLD_V = 1.2
        try:
            result = subprocess.run(
                ['vcgencmd', 'measure_volts', 'core'],
                capture_output=True, text=True, timeout=2.0,
            )
            if result.returncode != 0:
                return False
            m = re.search(r'volt=([0-9.]+)V', result.stdout)
            if not m:
                return False
            return float(m.group(1)) < LOW_VOLTAGE_THRESHOLD_V
        except Exception:
            return False

    @staticmethod
    def is_low_voltage() -> bool:
        """Cached, single-source-of-truth low-voltage check.

        Resolution order (controlled by ``VOLTAGE_CHECK_SOURCE`` in
        ``config.py``):

          * 'dmesg'    -> only dmesg scan
          * 'vcgencmd' -> only vcgencmd measurement
          * 'auto'     -> dmesg first; if dmesg is unavailable, fall
                          back to vcgencmd.
        """
        now = time.monotonic()
        if (now - SystemInfo._last_voltage_check_ts
                < SystemInfo._VOLTAGE_CACHE_TTL):
            return SystemInfo._last_voltage_check_result

        try:
            from config import VOLTAGE_CHECK_SOURCE
            source = (VOLTAGE_CHECK_SOURCE or 'auto').lower()
        except Exception:
            source = 'auto'

        result = False
        if source == 'dmesg':
            result = SystemInfo._check_dmesg_under_voltage()
        elif source == 'vcgencmd':
            result = SystemInfo._check_vcgencmd_under_voltage()
        else:  # 'auto'
            result = SystemInfo._check_dmesg_under_voltage()
            if not result:
                result = SystemInfo._check_vcgencmd_under_voltage()

        SystemInfo._last_voltage_check_ts = now
        SystemInfo._last_voltage_check_result = result
        return result

    @staticmethod
    def get_all():
        return {
            'cpu_temp': SystemInfo.get_cpu_temp(),
            'cpu_usage': SystemInfo.get_cpu_usage(),
            'ram': SystemInfo.get_ram_info(),
            'low_voltage': SystemInfo.is_low_voltage(),
        }
