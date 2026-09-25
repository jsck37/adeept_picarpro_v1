"""Мониторинг батареи — слияние подходов v2 (ADS7830 по I2C) и jsck37
(dmesg under-voltage).

Поведение:
* если на шине найден ADS7830 (0x48) — читается напряжение батареи,
  сглаживается медианой (как в v2 Voltage.py), считается процент заряда;
  при напряжении ниже порога подаётся тревога (зуммер + мигание LED);
* если АЦП отсутствует — работает только системный under-voltage
  мониторинг (picarpro.utils.system_info), доступный на любом Pi.
"""
import statistics
import threading
import time

from picarpro.logger import logger
from config import (
    ADS7830_ADDR, ADS7830_CHANNEL, BATTERY_VREF, BATTERY_MIN_V,
    BATTERY_MEDIAN_WINDOW, I2C_BUS,
)

# ADS7830 команды: MUX-канал + single-ended режим (SD | C[2:0])
_ADS7830_CMD_BASE = 0x84
_ADS7830_CHANNEL_CMD = {
    0: 0x84, 1: 0xC4, 2: 0x94, 3: 0xD4,
    4: 0xA4, 5: 0xE4, 6: 0xB4, 7: 0xF4,
}


def _get_smbus():
    try:
        from smbus2 import SMBus
        return SMBus(I2C_BUS)
    except Exception:
        try:
            import smbus
            return smbus.SMBus(I2C_BUS)
        except Exception:
            return None


def ads7830_present():
    """Есть ли АЦП ADS7830 на шине?"""
    bus = _get_smbus()
    if not bus:
        return False
    try:
        try:
            bus.read_byte(ADS7830_ADDR)
            return True
        except Exception:
            return False
    finally:
        try:
            bus.close()
        except Exception:
            pass


class ADS7830Reader:
    """Чтение канала ADS7830: 8-бит значение 0..255 -> напряжение делителя."""

    def __init__(self, addr=ADS7830_ADDR, channel=ADS7830_CHANNEL):
        self.addr = addr
        self.channel = channel
        self._bus = _get_smbus()
        if self._bus is None:
            raise IOError('I2C bus not available')

    def read_raw(self):
        """Сырое значение 0..255 или None при ошибке."""
        cmd = _ADS7830_CHANNEL_CMD.get(self.channel, _ADS7830_CMD_BASE)
        try:
            return self._bus.read_byte_data(self.addr, cmd)
        except Exception:
            return None

    def read_voltage(self, vref=BATTERY_VREF):
        raw = self.read_raw()
        if raw is None:
            return None
        return raw / 255.0 * vref

    def close(self):
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None


class VoltageMonitor:
    """Единая точка статуса питания для /api/status и OLED."""

    def __init__(self, on_alarm=None):
        self._lock = threading.Lock()
        self._reader = None
        self._history = []
        self._battery_voltage = None
        self._battery_percent = None
        self._low_battery = False
        self._on_alarm = on_alarm
        self._last_alarm = 0.0
        self._running = True
        try:
            if ads7830_present():
                self._reader = ADS7830Reader()
                logger.info(f'[Voltage] ADS7830 found at 0x{ADS7830_ADDR:02X} '
                            '— battery monitoring ON')
            else:
                self._reader = None
                logger.info('[Voltage] ADS7830 not found — '
                            'under-voltage monitoring only')
        except Exception:
            self._reader = None
            logger.info('[Voltage] ADS7830 probe failed — '
                        'under-voltage monitoring only')
        if self._reader:
            threading.Thread(target=self._loop, daemon=True).start()

    @property
    def has_adc(self):
        return self._reader is not None

    def get_status(self):
        with self._lock:
            return {
                'adc': self._reader is not None,
                'voltage': self._battery_voltage,
                'percent': self._battery_percent,
                'low': self._low_battery,
            }

    def _loop(self):
        while self._running:
            v = self._reader.read_voltage()
            if v is not None:
                with self._lock:
                    self._history.append(v)
                    if len(self._history) > BATTERY_MEDIAN_WINDOW:
                        self._history.pop(0)
                    median_v = statistics.median(self._history)
                    self._battery_voltage = round(median_v, 2)
                    percent = max(0.0, min(100.0,
                                 (median_v - BATTERY_MIN_V)
                                 / max(0.1, BATTERY_VREF - BATTERY_MIN_V) * 100.0))
                    self._battery_percent = round(percent)
                    low = median_v < BATTERY_MIN_V
                    if low and not self._low_battery:
                        self._trigger_alarm()
                    self._low_battery = low
            time.sleep(1.0)

    def _trigger_alarm(self):
        now = time.monotonic()
        if now - self._last_alarm < 30.0:
            return
        self._last_alarm = now
        logger.warning(f'[Voltage] low battery: {self._battery_voltage}V')
        if self._on_alarm:
            try:
                self._on_alarm()
            except Exception:
                pass

    def shutdown(self):
        self._running = False
        if self._reader:
            self._reader.close()
