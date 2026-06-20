import math, threading, time
from Server.logger import logger
from config import MPU6050_ADDR, I2C_BUS

try:
    from smbus2 import SMBus
    _HAS_SMBUS = True
except ImportError:
    try:
        import smbus as SMBus
        _HAS_SMBUS = True
    except ImportError:
        _HAS_SMBUS = False
        SMBus = None

REG_PWR_MGMT_1 = 0x6B
REG_SMPLRT_DIV = 0x19
REG_CONFIG = 0x1A
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_ACCEL_XOUT_H = 0x3B
REG_WHO_AM_I = 0x75

ACCEL_SCALE = 16384.0
GYRO_SCALE = 131.0
WHO_AM_I_VALUES = (0x68, 0x70, 0x71, 0x72)
POSSIBLE_ADDRS = (0x68, 0x69)


def _get_smbus():
    if not _HAS_SMBUS:
        return None
    try:
        return SMBus(I2C_BUS)
    except Exception:
        return None


def i2c_scan():
    bus = _get_smbus()
    if not bus:
        return []
    found = []
    try:
        for addr in range(0x03, 0x78):
            try:
                bus.read_byte(addr)
                found.append(addr)
            except Exception:
                pass
    finally:
        try:
            bus.close()
        except Exception:
            pass
    return found


def find_mpu6050_on_bus():
    bus = _get_smbus()
    if not bus:
        return None, None
    try:
        for addr in POSSIBLE_ADDRS:
            try:
                who = bus.read_byte_data(addr, REG_WHO_AM_I)
                if who in WHO_AM_I_VALUES:
                    return addr, who
            except Exception:
                continue
        for addr in i2c_scan():
            try:
                who = bus.read_byte_data(addr, REG_WHO_AM_I)
                if who in WHO_AM_I_VALUES:
                    return addr, who
            except Exception:
                continue
    finally:
        try:
            bus.close()
        except Exception:
            pass
    return None, None


class MPU6050Controller:
    def __init__(self):
        self._bus = None
        self._addr = MPU6050_ADDR
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._errors = 0
        self.initialized = False
        self._accel = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._gyro = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._temp = 0.0
        self._roll = self._pitch = 0.0
        self._init_sensor()

    def _init_sensor(self):
        if not _HAS_SMBUS:
            logger.warning('[MPU6050] smbus2 not available')
            return
        for addr in POSSIBLE_ADDRS:
            if self._try_init(addr):
                return
        found_addr, _ = find_mpu6050_on_bus()
        if found_addr and self._try_init(found_addr):
            return
        logger.warning('[MPU6050] not found — will retry in background')
        self._running = True
        self._thread = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    def _try_init(self, addr):
        bus = _get_smbus()
        if not bus:
            return False
        try:
            who = bus.read_byte_data(addr, REG_WHO_AM_I)
            if who not in WHO_AM_I_VALUES:
                bus.close()
                return False
            bus.write_byte_data(addr, REG_PWR_MGMT_1, 0x00)
            time.sleep(0.05)
            bus.write_byte_data(addr, REG_SMPLRT_DIV, 0x07)
            bus.write_byte_data(addr, REG_CONFIG, 0x03)
            bus.write_byte_data(addr, REG_GYRO_CONFIG, 0x00)
            bus.write_byte_data(addr, REG_ACCEL_CONFIG, 0x00)
            bus.read_i2c_block_data(addr, REG_ACCEL_XOUT_H, 14)
            self._addr = addr
            self._bus = bus
            self.initialized = True
            self._running = True
            self._errors = 0
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            logger.info(f'[MPU6050] OK at 0x{addr:02X} (WHO_AM_I=0x{who:02X})')
            return True
        except Exception as e:
            logger.info(f'[MPU6050] @0x{addr:02X} init failed: {e}')
            try:
                bus.close()
            except Exception:
                pass
            return False

    def _read_loop(self):
        while self._running:
            try:
                raw = self._bus.read_i2c_block_data(self._addr, REG_ACCEL_XOUT_H, 14)
                ax = self._s16((raw[0] << 8) | raw[1]) / ACCEL_SCALE
                ay = self._s16((raw[2] << 8) | raw[3]) / ACCEL_SCALE
                az = self._s16((raw[4] << 8) | raw[5]) / ACCEL_SCALE
                t = self._s16((raw[6] << 8) | raw[7])
                gx = self._s16((raw[8] << 8) | raw[9]) / GYRO_SCALE
                gy = self._s16((raw[10] << 8) | raw[11]) / GYRO_SCALE
                gz = self._s16((raw[12] << 8) | raw[13]) / GYRO_SCALE
                temp_c = t / 340.0 + 36.53
                roll = math.atan2(ay, az) * 57.2958
                pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 57.2958
                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._temp = round(temp_c, 1)
                    self._roll = round(roll, 1)
                    self._pitch = round(pitch, 1)
                self._errors = 0
            except Exception:
                self._errors += 1
                if self._errors >= 30:
                    logger.error('[MPU6050] too many read errors — reinit')
                    self._reinit()
            time.sleep(0.1)

    def _retry_loop(self):
        retry = 0
        while self._running and not self.initialized:
            retry += 1
            time.sleep(5 if retry <= 5 else 15)
            if not self._running or self.initialized:
                return
            logger.info(f'[MPU6050] retry #{retry}...')
            for addr in POSSIBLE_ADDRS:
                if self._try_init(addr):
                    return
            found_addr, _ = find_mpu6050_on_bus()
            if found_addr and self._try_init(found_addr):
                return

    def _reinit(self):
        self.initialized = False
        self._errors = 0
        try:
            if self._bus:
                self._bus.close()
        except Exception:
            pass
        self._bus = None
        self._running = True
        self._thread = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    @staticmethod
    def _s16(v):
        return v - 0x10000 if v >= 0x8000 else v

    def get_data(self):
        if not self.initialized:
            return None
        with self._lock:
            return {
                'accel': dict(self._accel),
                'gyro': dict(self._gyro),
                'temp': self._temp,
                'roll': self._roll,
                'pitch': self._pitch,
            }

    def shutdown(self):
        self._running = False
        if self._bus:
            try:
                self._bus.write_byte_data(self._addr, REG_PWR_MGMT_1, 0x40)
            except Exception:
                pass
            try:
                self._bus.close()
            except Exception:
                pass
        self._bus = None
        self.initialized = False
        logger.info('[MPU6050] shutdown')
