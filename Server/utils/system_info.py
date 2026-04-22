"""
System information utility.
Provides CPU temperature, CPU usage, and RAM information.
"""

import time


class SystemInfo:
    """Read system information: CPU temp, CPU usage, RAM."""

    @staticmethod
    def get_cpu_temp():
        """Get CPU temperature in Celsius."""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000.0
            return round(temp, 1)
        except Exception:
            return 0.0

    @staticmethod
    def get_cpu_usage():
        """Get CPU usage percentage (averaged over 1 second)."""
        try:
            import psutil
            return psutil.cpu_percent(interval=1)
        except ImportError:
            # Fallback: read from /proc/stat
            try:
                def _read_stat():
                    with open('/proc/stat', 'r') as f:
                        line = f.readline()
                    values = [int(x) for x in line.split()[1:]]
                    return sum(values[:4]), sum(values)

                total1, idle1 = _read_stat()[1], _read_stat()[0] - sum(_read_stat()[:4])
                time.sleep(1)
                total2, idle2 = _read_stat()[1], _read_stat()[0] - sum(_read_stat()[:4])

                total_diff = total2 - total1
                idle_diff = idle2 - idle1

                if total_diff > 0:
                    return round(100 * (1 - idle_diff / total_diff), 1)
                return 0.0
            except Exception:
                return 0.0

    @staticmethod
    def get_ram_info():
        """Get RAM usage info as dict."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            return {
                'total': round(mem.total / (1024 ** 3), 1),
                'used': round(mem.used / (1024 ** 3), 1),
                'percent': mem.percent,
            }
        except ImportError:
            return {'total': 0, 'used': 0, 'percent': 0}

    @staticmethod
    def get_all():
        """Get all system info as dict."""
        return {
            'cpu_temp': SystemInfo.get_cpu_temp(),
            'cpu_usage': SystemInfo.get_cpu_usage(),
            'ram': SystemInfo.get_ram_info(),
        }
