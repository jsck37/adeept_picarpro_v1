import time


class SystemInfo:

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
            total_gb = round(total / (1024 * 1024), 2)
            used_gb = round(used / (1024 * 1024), 2)

            return {
                'total': total_gb,
                'used': used_gb,
                'percent': percent,
                'total_mb': total_mb,
                'used_mb': used_mb,
            }
        except Exception:
            return {'total': 0, 'used': 0, 'percent': 0,
                    'total_mb': 0, 'used_mb': 0}

    @staticmethod
    def get_all():
        return {
            'cpu_temp': SystemInfo.get_cpu_temp(),
            'cpu_usage': SystemInfo.get_cpu_usage(),
            'ram': SystemInfo.get_ram_info(),
        }
