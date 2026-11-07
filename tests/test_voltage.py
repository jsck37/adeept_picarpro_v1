"""Тесты модуля питания: ADS7830 (порт из v2 Voltage.py) + fallback."""
import time

import pytest


@pytest.fixture
def monitor(fake_i2c_devices):
    """Монитор с зарегистрированным фейковым АЦП (значение канала = reg 0x84)."""
    from picarpro.hardware.voltage import VoltageMonitor
    fake_i2c_devices[0x48] = {i: 0 for i in range(256)}
    m = VoltageMonitor(on_alarm=None)
    yield m
    m.shutdown()


class TestADS7830:
    def test_present(self, fake_i2c_devices):
        from picarpro.hardware.voltage import ads7830_present
        fake_i2c_devices[0x48] = {i: 0 for i in range(256)}
        assert ads7830_present() is True

    def test_absent(self, fake_i2c_devices):
        from picarpro.hardware.voltage import ads7830_present
        assert ads7830_present() is False

    def test_reader_voltage(self, fake_i2c_devices):
        from picarpro.hardware.voltage import ADS7830Reader
        regs = fake_i2c_devices.setdefault(0x48, {})
        # канал 0: cmd 0x84 -> регистр 0x84
        regs[0x84] = 128  # половина шкалы
        reader = ADS7830Reader()
        v = reader.read_voltage(vref=8.4)
        assert v == pytest.approx(4.2, abs=0.1)
        reader.close()


class TestVoltageMonitor:
    def test_no_adc(self, fake_i2c_devices):
        from picarpro.hardware.voltage import VoltageMonitor
        m = VoltageMonitor()
        assert m.has_adc is False
        status = m.get_status()
        assert status['adc'] is False
        assert status['voltage'] is None
        m.shutdown()

    def test_reads_voltage(self, monitor, fake_i2c_devices):
        assert monitor.has_adc is True
        regs = fake_i2c_devices[0x48]
        regs[0x84] = 255  # полный заряд
        import time
        st = {}
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            st = monitor.get_status()
            if st['voltage'] is not None and st['voltage'] > 5:
                break
            time.sleep(0.05)
        assert st['voltage'] == pytest.approx(8.4, abs=0.1)
        assert st['percent'] == 100
        assert st['low'] is False

    def test_low_battery_alarm(self, fake_i2c_devices, monkeypatch):
        # v1.6.0: персистентность ускоряем до 0 (иначе тест ждал бы 10 с)
        monkeypatch.setattr('picarpro.hardware.voltage.BATTERY_ALARM_PERSIST_S', 0)
        from picarpro.hardware.voltage import VoltageMonitor
        fake_i2c_devices[0x48] = {i: 0 for i in range(256)}
        alarmed = []
        m = VoltageMonitor(on_alarm=lambda: alarmed.append(1))
        regs = fake_i2c_devices[0x48]
        regs[0x84] = 10  # ~0.33 В — ниже BATTERY_MIN_V
        import time
        deadline = time.monotonic() + 3.0
        st = {}
        while time.monotonic() < deadline:
            st = m.get_status()
            if st['low']:
                break
            time.sleep(0.05)
        assert st['low'] is True
        assert alarmed, 'тревога не сработала'
        m.shutdown()

    def test_alarm_needs_persistence(self, fake_i2c_devices, monkeypatch):
        """v1.6.0: кратковременная просадка (пусковой ток моторов) НЕ даёт
        тревогу, пока условие не продержится BATTERY_ALARM_PERSIST_S.
        Цикл _loop гоняется синхронно с фейковым временем — детерминированно."""
        from types import SimpleNamespace
        from picarpro.hardware import voltage as V

        real_sleep = time.sleep

        class _FakeTime:
            t = 0.0
            @classmethod
            def monotonic(cls):
                return cls.t
            @classmethod
            def sleep(cls, s):
                cls.t += s

        monkeypatch.setattr(V, 'time', _FakeTime)
        monkeypatch.setattr(V, 'BATTERY_ALARM_PERSIST_S', 2.0)
        monkeypatch.setattr(
            V, 'threading',
            SimpleNamespace(Thread=lambda **k: SimpleNamespace(start=lambda: None),
                            Lock=V.threading.Lock))
        fake_i2c_devices[0x48] = {i: 0 for i in range(256)}
        regs = fake_i2c_devices[0x48]
        regs[0x84] = 10                       # низкое ДО старта монитора
        alarmed = []
        m = V.VoltageMonitor(on_alarm=lambda: alarmed.append(1))

        # просадка длится 1 фейковую секунду (< PERSIST 2 с), потом батарея
        # «восстановилась» (медиана выходит из низкого за несколько выборок)
        volts = [0.33] + [8.4] * 14
        snaps = []

        def _read():
            v = volts.pop(0)
            snaps.append((m.get_status()['low'], list(alarmed)))
            if not volts:
                m._running = False
            return v

        m._reader.read_voltage = _read
        m._loop()                             # синхронный прогон

        assert not alarmed, 'тревога сработала на кратковременную просадку'
        assert all(not low for low, _ in snaps), \
            'флаг low поднялся без устойчивого проседания'
        m.shutdown()

    def test_alarm_fires_after_persistence(self, fake_i2c_devices, monkeypatch):
        """v1.6.0: при УСТОЙЧИВО низком напряжении тревога приходит после
        BATTERY_ALARM_PERSIST_S, а не с первой выборки."""
        from types import SimpleNamespace
        from picarpro.hardware import voltage as V

        class _FakeTime:
            t = 0.0
            @classmethod
            def monotonic(cls):
                return cls.t
            @classmethod
            def sleep(cls, s):
                cls.t += s

        monkeypatch.setattr(V, 'time', _FakeTime)
        monkeypatch.setattr(V, 'BATTERY_ALARM_PERSIST_S', 2.0)
        monkeypatch.setattr(
            V, 'threading',
            SimpleNamespace(Thread=lambda **k: SimpleNamespace(start=lambda: None),
                            Lock=V.threading.Lock))
        fake_i2c_devices[0x48] = {i: 0 for i in range(256)}
        fake_i2c_devices[0x48][0x84] = 10     # низкое сразу
        alarmed = []
        m = V.VoltageMonitor(on_alarm=lambda: alarmed.append(1))

        volts = [0.33] * 5
        snaps = []

        def _read():
            v = volts.pop(0)
            # снимок ПОСЛЕ итерации: low виден снаружи уже в следующем чтении
            snaps.append((m.get_status()['low'], list(alarmed)))
            if not volts:
                m._running = False
            return v

        m._reader.read_voltage = _read
        m._loop()

        assert alarmed, 'тревога не сработала при устойчиво низком питании'
        # первые итерации (t < 2 с) — ещё без тревоги
        assert all(not a for _, a in snaps[:2]), \
            'тревога сработала раньше персистентности'
        m.shutdown()

    def test_alarm_hysteresis(self, fake_i2c_devices, monkeypatch):
        """v1.6.0: снятие тревоги только выше BATTERY_LOW_CLEAR_V (6.7 В):
        между MIN (6.3) и CLEAR флаг low держится, без «хлопьев»."""
        from types import SimpleNamespace
        from picarpro.hardware import voltage as V

        class _FakeTime:
            t = 0.0
            @classmethod
            def monotonic(cls):
                return cls.t
            @classmethod
            def sleep(cls, s):
                cls.t += s

        monkeypatch.setattr(V, 'time', _FakeTime)
        monkeypatch.setattr(V, 'BATTERY_ALARM_PERSIST_S', 0)
        monkeypatch.setattr(
            V, 'threading',
            SimpleNamespace(Thread=lambda **k: SimpleNamespace(start=lambda: None),
                            Lock=V.threading.Lock))
        from config import BATTERY_VREF

        def raw_for(v):
            return max(0, min(255, round(v / BATTERY_VREF * 255)))

        fake_i2c_devices[0x48] = {i: 0 for i in range(256)}
        fake_i2c_devices[0x48][0x84] = raw_for(5.5)   # низкое сразу
        m = V.VoltageMonitor(on_alarm=lambda: None)

        # 5.5 В (тревога) -> 6.5 В (между порогами: держится) -> 6.9 В (снята)
        volts = ([5.5] * 3 + [6.5] * 12 + [6.9] * 14)
        lows = []

        def _read():
            v = volts.pop(0)
            lows.append(m.get_status()['low'])
            if not volts:
                m._running = False
            return v

        m._reader.read_voltage = _read
        m._loop()

        assert any(lows), 'тревога не поднялась'
        # lows[0] — состояние ДО первой итерации (False), далее флаг держится
        assert all(lows[1:15]), 'флаг low хлопнул между порогами (нет гистерезиса)'
        assert not lows[-1], 'тревога не снялась выше BATTERY_LOW_CLEAR_V'
        m.shutdown()
