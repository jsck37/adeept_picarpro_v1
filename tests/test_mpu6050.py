"""Тесты MPU6050 (фейковая I2C-шина с регистрами) и мониторинга батареи."""
import time

import pytest


@pytest.fixture
def mpu(fake_i2c_devices):
    from picarpro.hardware.mpu6050 import MPU6050Controller
    # регистрируем устройство MPU6050 на шине (temp_raw=68 — «живой» сенсор,
    # не пустая шина; нейтральное значение — не ломает wait-условия тестов)
    fake_i2c_devices[0x68] = {0x75: 0x68, 0x42: 68}
    c = MPU6050Controller()
    yield c
    c.shutdown()


class TestScan:
    def test_scan_finds_device(self, fake_i2c_devices):
        from picarpro.hardware.mpu6050 import i2c_scan
        fake_i2c_devices[0x68] = {0x75: 0x68}
        devs = i2c_scan()
        assert 0x68 in devs

    def test_scan_empty_bus(self, fake_i2c_devices):
        from picarpro.hardware.mpu6050 import i2c_scan
        assert i2c_scan() == []

    def test_find_mpu_by_whoami(self, fake_i2c_devices):
        from picarpro.hardware.mpu6050 import find_mpu6050_on_bus
        make = fake_i2c_devices
        make[0x69] = {0x75: 0x71}  # MPU9250-совместимый
        addr, who = find_mpu6050_on_bus()
        assert addr == 0x69
        assert who == 0x71

    def test_clone_whoami_accepted(self, fake_i2c_devices):
        """v1.3.0: клоны с нестандартным WHO_AM_I (0x98 и т.п.) принимаются
        по ACK + живым данным — раньше «not found» навсегда."""
        fake_i2c_devices[0x68] = {0x75: 0x98, 0x42: 68}
        from picarpro.hardware.mpu6050 import MPU6050Controller
        c = MPU6050Controller()
        try:
            assert c.initialized is True
            assert c._addr == 0x68
        finally:
            c.shutdown()

    def test_empty_data_rejected(self, fake_i2c_devices):
        """Устройство ACKает шину, но данные пустые (все нули) — не MPU."""
        fake_i2c_devices[0x68] = {0x75: 0x68}
        from picarpro.hardware.mpu6050 import MPU6050Controller
        c = MPU6050Controller()
        try:
            assert c.initialized is False
        finally:
            c.shutdown()


class TestController:
    def test_init_found(self, mpu):
        assert mpu.initialized is True
        assert mpu._addr == 0x68

    def test_data_parsing(self, mpu, fake_i2c_devices):
        from tests.fakes.all_fakes import set_mpu6050_sample
        regs = fake_i2c_devices[0x68]
        # az = 1g, gx = 131 (=1 град/с)
        set_mpu6050_sample(regs, ax=0, ay=0, az=16384, gx=131, temp_raw=0)
        # фоновой поток читает каждые 0.1 с — ждём обновления кэша
        data = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            data = mpu.get_data()
            if data and data['accel']['z'] > 0.5:
                break
            time.sleep(0.05)
        assert data is not None
        assert data['accel']['z'] == pytest.approx(1.0, abs=0.05)
        assert data['gyro']['x'] == pytest.approx(1.0, abs=0.05)
        assert data['roll'] == pytest.approx(0.0, abs=2.0)
        assert data['pitch'] == pytest.approx(0.0, abs=2.0)

    def test_temp_parsing(self, mpu, fake_i2c_devices):
        from tests.fakes.all_fakes import set_mpu6050_sample
        regs = fake_i2c_devices[0x68]
        # temp = raw/340 + 36.53: raw=340 -> 37.53
        set_mpu6050_sample(regs, temp_raw=340)
        data = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            data = mpu.get_data()
            if data and data['temp'] > 37.0:
                break
            time.sleep(0.05)
        assert data['temp'] == pytest.approx(37.53, abs=0.1)

    def test_roll_tilt(self, mpu, fake_i2c_devices):
        from tests.fakes.all_fakes import set_mpu6050_sample
        regs = fake_i2c_devices[0x68]
        # ay = 0.5g, az = 0.866g -> roll ~ 30 градусов
        set_mpu6050_sample(regs, ay=8192, az=14265)
        data = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            data = mpu.get_data()
            if data and abs(data['roll']) > 5:
                break
            time.sleep(0.05)
        assert data['roll'] == pytest.approx(30.0, abs=3.0)

    def test_read_errors_trigger_reinit(self, mpu, fake_i2c_devices, monkeypatch):
        """После 30 ошибок чтения контроллер переинициализируется."""
        assert mpu.initialized
        fake_i2c_devices.pop(0x68)  # устройство «отвалилось»
        import time as _t
        deadline = _t.monotonic() + 5.0
        while _t.monotonic() < deadline:
            if not mpu.initialized:
                break
            _t.sleep(0.05)
        assert mpu.initialized is False

    def test_shutdown(self, mpu, fake_i2c_devices):
        regs = fake_i2c_devices.get(0x68)
        if regs is not None:
            mpu.shutdown()
            # PWR_MGMT_1 = 0x40 (sleep)
            assert regs.get(0x6B) == 0x40
