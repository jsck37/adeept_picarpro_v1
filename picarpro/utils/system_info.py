"""Системная информация о Raspberry Pi.

Показания CPU/RAM собирает ОДИН фоновый поток-сэмплер и отдаёт из кэша
(раньше было три потока, а get_cpu_usage() спал 0.3 с прямо в обработчике
статуса). Использование CPU считается по разнице двух соседних замеров
/proc/stat — без пауз внутри сэмплера.

Низкое напряжение определяется флагом under-voltage прошивки Pi (бит 0
``get_throttled``): hwmon/sysfs читаются без fork, ``vcgencmd get_throttled``
— запасной путь. Прежняя проверка «напряжение ядра < 1.2 В» была неверной:
на Pi 4/5 ядро в простое штатно работает на 0.8-0.9 В, и тревога горела
постоянно.
"""
import glob
import subprocess
import threading
import time

from config import VOLTAGE_CHECK_SOURCE, VOLTAGE_CHECK_INTERVAL_S

_SAMPLE_INTERVAL_S = 2.0
_THROTTLED_UNDERVOLT_NOW = 0x1
_FIRMWARE_THROTTLED = '/sys/devices/platform/soc/soc:firmware/get_throttled'


def _read_cpu_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return round(float(f.read().strip()) / 1000.0, 1)
    except Exception:
        return 0.0


def _read_cpu_times():
    """(idle, total) из /proc/stat или None."""
    try:
        with open('/proc/stat') as f:
            vals = [int(x) for x in f.readline().split()[1:]]
        return vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
    except Exception:
        return None


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


def _parse_hex(text):
    text = text.strip().lower()
    if '=' in text:                      # «throttled=0x50005»
        text = text.split('=', 1)[1]
    return int(text.replace('0x', ''), 16)


def _pick_undervoltage_reader():
    """Выбрать самый дешёвый доступный источник флага under-voltage.

    Возвращает функцию () -> bool|None либо None, если источников нет
    (не Raspberry Pi) — тогда опрос вообще не запускается.
    """
    for path in glob.glob('/sys/class/hwmon/hwmon*/in0_lcrit_alarm'):
        def _hwmon(path=path):
            try:
                with open(path) as f:
                    return f.read().strip() == '1'
            except Exception:
                return None
        return _hwmon
    try:
        with open(_FIRMWARE_THROTTLED) as f:
            f.read()

        def _firmware():
            try:
                with open(_FIRMWARE_THROTTLED) as f:
                    return bool(_parse_hex(f.read()) & _THROTTLED_UNDERVOLT_NOW)
            except Exception:
                return None
        return _firmware
    except Exception:
        pass

    def _vcgencmd():
        try:
            r = subprocess.run(['vcgencmd', 'get_throttled'],
                               capture_output=True, text=True, timeout=2)
            return bool(_parse_hex(r.stdout) & _THROTTLED_UNDERVOLT_NOW)
        except Exception:
            return None
    # vcgencmd есть не везде — проверяем один раз, а не опрашиваем вечно
    return _vcgencmd if _vcgencmd() is not None else None


def _scan_dmesg_undervoltage():
    """Однократное сканирование кольцевого буфера ядра на under-voltage."""
    try:
        r = subprocess.run(['dmesg', '--color=never'],
                           capture_output=True, text=True, timeout=3)
        return 'Under-voltage detected' in r.stdout
    except Exception:
        return False


class SystemInfo:
    _voltage_state = False
    _voltage_last_event = 0.0
    _voltage_lock = threading.Lock()
    _voltage_started = False
    _VOLTAGE_HOLD_S = 8.0

    _sample_lock = threading.Lock()
    _sampler_started = False
    _cpu_temp = 0.0
    _cpu_usage = 0.0
    _ram = {'total_mb': 0, 'used_mb': 0, 'percent': 0.0}

    # ---- фоновый сэмплер CPU/RAM ---------------------------------------
    @classmethod
    def _ensure_sampler(cls):
        with cls._sample_lock:
            if cls._sampler_started:
                return
            cls._sampler_started = True
            cls._ram = _read_ram_info()
            cls._cpu_temp = _read_cpu_temp()
        threading.Thread(target=cls._sampler_loop, daemon=True).start()

    @classmethod
    def _sampler_loop(cls):
        prev = _read_cpu_times()
        while True:
            time.sleep(_SAMPLE_INTERVAL_S)
            try:
                cur = _read_cpu_times()
                usage = None
                if prev and cur and cur[1] > prev[1]:
                    usage = round(100.0 * (1.0 - (cur[0] - prev[0])
                                           / (cur[1] - prev[1])), 1)
                prev = cur
                temp, ram = _read_cpu_temp(), _read_ram_info()
                with cls._sample_lock:
                    cls._cpu_temp, cls._ram = temp, ram
                    if usage is not None:
                        cls._cpu_usage = usage
            except Exception:
                pass

    @classmethod
    def get_cpu_temp(cls):
        cls._ensure_sampler()
        return cls._cpu_temp

    @classmethod
    def get_cpu_usage(cls):
        cls._ensure_sampler()
        return cls._cpu_usage

    @classmethod
    def get_ram_info(cls):
        cls._ensure_sampler()
        return dict(cls._ram)

    # ---- мониторинг питания ----------------------------------------------
    @staticmethod
    def _mark_undervoltage():
        with SystemInfo._voltage_lock:
            SystemInfo._voltage_state = True
            SystemInfo._voltage_last_event = time.monotonic()

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
                        SystemInfo._mark_undervoltage()
            except Exception:
                pass

        def _watch_flag():
            read = _pick_undervoltage_reader()
            if read is None:
                return                       # не Pi: опрашивать нечего
            while True:
                if read():
                    SystemInfo._mark_undervoltage()
                time.sleep(max(1.0, VOLTAGE_CHECK_INTERVAL_S))

        def _watch_auto():
            if _scan_dmesg_undervoltage():
                SystemInfo._mark_undervoltage()
            _watch_flag()

        target = {'dmesg': _watch_dmesg, 'vcgencmd': _watch_flag}.get(
            source, _watch_auto)
        threading.Thread(target=target, daemon=True).start()

    @staticmethod
    def is_low_voltage():
        if not SystemInfo._voltage_started:
            try:
                SystemInfo._start_voltage_watcher()
            except Exception:
                pass
        with SystemInfo._voltage_lock:
            # событие «гаснет» само через _VOLTAGE_HOLD_S (раньше для этого
            # держали отдельный поток-уборщик, будивший CPU каждые 2 с)
            if (SystemInfo._voltage_state
                    and time.monotonic() - SystemInfo._voltage_last_event
                    > SystemInfo._VOLTAGE_HOLD_S):
                SystemInfo._voltage_state = False
            return SystemInfo._voltage_state

    @staticmethod
    def get_all():
        return {
            'cpu_temp': SystemInfo.get_cpu_temp(),
            'cpu_usage': SystemInfo.get_cpu_usage(),
            'ram': SystemInfo.get_ram_info(),
            'low_voltage': SystemInfo.is_low_voltage(),
        }
