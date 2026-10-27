"""Системная информация о Raspberry Pi.

Отличие от исходной версии jsck37: показания CPU загружаются фоновым
потоком-сэмплером и отдаются из кэша. Ранее get_cpu_usage() выполнял
sleep(0.3) прямо в обработчике запроса статуса (задержка в каждом
броадкасте), а get_ip() порождал до 4 subprocess в секунду.
"""
import subprocess
import threading
import time

from config import (VOLTAGE_CHECK_SOURCE, LOW_VOLTAGE_THRESHOLD_V,
                    VOLTAGE_CHECK_INTERVAL_S)


def _read_cpu_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return round(float(f.read().strip()) / 1000.0, 1)
    except Exception:
        return 0.0


def _read_cpu_usage():
    """Доля занятого CPU по /proc/stat (двумя чтениями с интервалом)."""
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


def _read_ram_info():
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        info[parts[0].rstrip(':')] = int(parts[1])
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


def _read_core_voltage():
    """Напряжение ядра через vcgencmd (fallback-источник)."""
    try:
        r = subprocess.run(['vcgencmd', 'measure_volts', 'core'],
                           capture_output=True, text=True, timeout=2)
        # формат: volt=1.2000V
        for token in r.stdout.strip().split('='):
            token = token.strip().rstrip('V')
            try:
                return float(token)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _scan_dmesg_undervoltage():
    """Однократное сканирование кольцевого буфера ядра на under-voltage."""
    try:
        r = subprocess.run(['dmesg', '--color=never'],
                           capture_output=True, text=True, timeout=3)
        return 'Under-voltage detected' in r.stdout
    except Exception:
        return False


class _CachedSampler:
    """Фоновый сэмплер: вызывает func каждые interval секунд, кэширует результат."""

    def __init__(self, func, interval):
        self._func = func
        self._interval = interval
        self._value = func() if func is _read_ram_info else 0.0
        self._lock = threading.Lock()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while True:
            try:
                v = self._func()
                with self._lock:
                    self._value = v
            except Exception:
                pass
            time.sleep(self._interval)

    def get(self):
        with self._lock:
            return self._value


_cpu_temp_sampler = _CachedSampler(_read_cpu_temp, 2.0)
_cpu_usage_sampler = _CachedSampler(_read_cpu_usage, 2.0)
_ram_sampler = _CachedSampler(_read_ram_info, 2.0)


class SystemInfo:
    _voltage_state = False
    _voltage_last_event = 0.0
    _voltage_lock = threading.Lock()
    _voltage_started = False
    _VOLTAGE_HOLD_S = 8.0

    @staticmethod
    def get_cpu_temp():
        return _cpu_temp_sampler.get()

    @staticmethod
    def get_cpu_usage():
        return _cpu_usage_sampler.get()

    @staticmethod
    def get_ram_info():
        return _ram_sampler.get()

    @staticmethod
    def _start_voltage_watcher():
        if SystemInfo._voltage_started:
            return
        SystemInfo._voltage_started = True

        source = VOLTAGE_CHECK_SOURCE

        def _watch_dmesg():
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
                pass

        def _watch_vcgencmd():
            while True:
                v = _read_core_voltage()
                if v is not None and v < LOW_VOLTAGE_THRESHOLD_V:
                    with SystemInfo._voltage_lock:
                        SystemInfo._voltage_state = True
                        SystemInfo._voltage_last_event = time.monotonic()
                # v1.2.0: период из config (3 c вместо 2 c) — каждый вызов
                # vcgencmd это fork+exec, на Pi 3B+ это ощутимо
                time.sleep(max(1.0, VOLTAGE_CHECK_INTERVAL_S))

        def _watch_auto():
            # 'auto': сначала одноразовый dmesg-скан, затем vcgencmd-поллинг
            if _scan_dmesg_undervoltage():
                with SystemInfo._voltage_lock:
                    SystemInfo._voltage_state = True
                    SystemInfo._voltage_last_event = time.monotonic()
            _watch_vcgencmd()

        if source == 'dmesg':
            threading.Thread(target=_watch_dmesg, daemon=True).start()
        elif source == 'vcgencmd':
            threading.Thread(target=_watch_vcgencmd, daemon=True).start()
        else:
            threading.Thread(target=_watch_auto, daemon=True).start()

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
