"""Тесты модуля питания: ADS7830 (порт из v2 Voltage.py) + fallback."""
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

    def test_low_battery_alarm(self, fake_i2c_devices):
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
