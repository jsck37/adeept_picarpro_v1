import math, threading, time
from config import MPU6050_ADDR, I2C_BUS
from Server.logger import logger

REG_PWR_MGMT_1   = 0x6B
REG_SMPLRT_DIV   = 0x19
REG_CONFIG       = 0x1A
REG_GYRO_CONFIG  = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_WHO_AM_I     = 0x75
REG_ACCEL_XOUT_H = 0x3B
REG_GYRO_XOUT_H  = 0x43

ACCEL_SCALE = 16384.0
GYRO_SCALE  = 131.0
WHO_AM_I_VALUES = (0x68, 0x72, 0x71)
POSSIBLE_ADDRS  = [0x68, 0x69]

_I2C_BACKEND = None
try:
    from robot_hat import I2C as RobotHatI2C
    _I2C_BACKEND = "robot_hat"
except ImportError:
    pass
if _I2C_BACKEND is None:
    try:
        from smbus2 import SMBus
        _I2C_BACKEND = "smbus2"
    except ImportError:
        try:
            import smbus as SMBus
            _I2C_BACKEND = "smbus"
        except ImportError:
            _I2C_BACKEND = None


class _I2CWrapper:
    def __init__(self, address, bus=1):
        self.address = address
        self._backend = _I2C_BACKEND
        self._bus = None
        self._i2c = None
        if self._backend == "robot_hat":
            self._i2c = RobotHatI2C(address=address, bus=bus)
        elif self._backend in ("smbus2", "smbus"):
            self._bus = SMBus(bus)
        else:
            raise RuntimeError("No I2C backend. Install: pip3 install robot-hat")

    def write_byte_data(self, reg, data):
        if self._backend == "robot_hat":
            return self._i2c._write_byte_data(reg, data)
        return self._bus.write_byte_data(self.address, reg, data)

    def read_byte_data(self, reg):
        if self._backend == "robot_hat":
            return self._i2c._read_byte_data(reg)
        return self._bus.read_byte_data(self.address, reg)

    def read_word_signed(self, reg):
        if self._backend == "robot_hat":
            result = self._i2c._read_word_data(reg)
            if result is False:
                raise OSError("I2C read_word failed")
            low, high = result[0], result[1]
            value = (high << 8) | low
        else:
            value = self._bus.read_word_data(self.address, reg)
        return value - 0x10000 if value >= 0x8000 else value

    def read_i2c_block(self, reg, length):
        if self._backend == "robot_hat":
            result = self._i2c._read_i2c_block_data(reg, length)
            if result is False:
                raise OSError("I2C block read failed")
            return result
        return self._bus.read_i2c_block_data(self.address, reg, length)

    def scan(self):
        if self._backend == "robot_hat":
            return self._i2c.scan()
        found = []
        for addr in range(0x03, 0x78):
            try:
                self._bus.read_byte(addr)
                found.append(addr)
            except Exception:
                pass
        return found

    def close(self):
        try:
            if self._backend == "robot_hat":
                if self._i2c and hasattr(self._i2c, '_smbus') and self._i2c._smbus:
                    self._i2c._smbus.close()
                    self._i2c._smbus = None
            elif self._bus:
                self._bus.close()
                self._bus = None
        except Exception:
            pass


def i2c_scan(bus_number=None):
    bus_number = bus_number or I2C_BUS
    found = []
    try:
        wrapper = _I2CWrapper(0x00, bus_number)
        found = wrapper.scan()
        wrapper.close()
    except Exception as e:
        logger.error(f"[I2C] Scan failed: {e}")
    return found


def find_mpu6050_on_bus(bus_number=None):
    bus_number = bus_number or I2C_BUS
    try:
        wrapper = _I2CWrapper(0x00, bus_number)
        for addr in wrapper.scan():
            try:
                w2 = _I2CWrapper(addr, bus_number)
                who = w2.read_byte_data(REG_WHO_AM_I)
                w2.close()
                if who in WHO_AM_I_VALUES:
                    logger.info(f"[MPU6050] Found WHO_AM_I=0x{who:02X} at 0x{addr:02X}")
                    return addr, who
            except Exception:
                continue
        wrapper.close()
    except Exception as e:
        logger.error(f"[MPU6050] Bus scan failed: {e}")
    return None, None


class MPU6050Controller:
    def __init__(self):
        self._i2c = None
        self._addr = MPU6050_ADDR
        self._running = False
        self.initialized = False
        self._thread = None
        self._lock = threading.Lock()
        self._errors = 0
        self._accel = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._gyro = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._roll = self._pitch = 0.0
        self._init_sensor()

    def _init_sensor(self):
        for addr in POSSIBLE_ADDRS:
            if self._try_connect(addr):
                return
        found_addr, _ = find_mpu6050_on_bus()
        if found_addr and self._try_connect(found_addr):
            return
        logger.warning("[MPU6050] Not found — will retry in background")
        self._running = True
        self._thread = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    def _try_connect(self, addr):
        try:
            i2c = _I2CWrapper(addr, I2C_BUS)
            who = i2c.read_byte_data(REG_WHO_AM_I)
            if who not in WHO_AM_I_VALUES:
                i2c.close()
                return False
            i2c.write_byte_data(REG_PWR_MGMT_1, 0x00)
            time.sleep(0.1)
            i2c.write_byte_data(REG_SMPLRT_DIV, 0x07)
            i2c.write_byte_data(REG_CONFIG, 0x03)
            i2c.write_byte_data(REG_GYRO_CONFIG, 0x00)
            i2c.write_byte_data(REG_ACCEL_CONFIG, 0x00)
            i2c.read_word_signed(REG_ACCEL_XOUT_H)
            self._addr = addr
            self._i2c = i2c
            self.initialized = True
            self._running = True
            self._errors = 0
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            logger.info(f"[MPU6050] OK at 0x{addr:02X} (WHO_AM_I=0x{who:02X}, backend={_I2C_BACKEND})")
            return True
        except Exception as e:
            logger.error(f"[MPU6050] Connect@0x{addr:02X} failed: {e}")
            try:
                i2c.close()
            except Exception:
                pass
            return False

    def _read_loop(self):
        while self._running:
            try:
                try:
                    raw = self._i2c.read_i2c_block(REG_ACCEL_XOUT_H, 14)
                    ax = self._s16((raw[0] << 8) | raw[1]) / ACCEL_SCALE
                    ay = self._s16((raw[2] << 8) | raw[3]) / ACCEL_SCALE
                    az = self._s16((raw[4] << 8) | raw[5]) / ACCEL_SCALE
                    gx = self._s16((raw[8] << 8)  | raw[9])  / GYRO_SCALE
                    gy = self._s16((raw[10] << 8) | raw[11]) / GYRO_SCALE
                    gz = self._s16((raw[12] << 8) | raw[13]) / GYRO_SCALE
                except (OSError, TypeError):
                    ax = self._i2c.read_word_signed(REG_ACCEL_XOUT_H) / ACCEL_SCALE
                    ay = self._i2c.read_word_signed(0x3D) / ACCEL_SCALE
                    az = self._i2c.read_word_signed(0x3F) / ACCEL_SCALE
                    gx = self._i2c.read_word_signed(REG_GYRO_XOUT_H) / GYRO_SCALE
                    gy = self._i2c.read_word_signed(0x45) / GYRO_SCALE
                    gz = self._i2c.read_word_signed(0x47) / GYRO_SCALE
                roll = math.atan2(ay, az) * 57.2958
                pitch = math.atan2(-ax, math.sqrt(ay*ay + az*az)) * 57.2958
                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._roll = round(roll, 1)
                    self._pitch = round(pitch, 1)
                self._errors = 0
            except Exception:
                self._errors += 1
                if self._errors >= 20:
                    self._reinit()
            time.sleep(0.1)

    def _retry_loop(self):
        retry = 0
        while self._running and not self.initialized:
            time.sleep(5)
            retry += 1
            logger.info(f"[MPU6050] Retry #{retry}...")
            for addr in POSSIBLE_ADDRS:
                if self._try_connect(addr):
                    return

    def _reinit(self):
        self.initialized = False
        self._errors = 0
        try:
            if self._i2c:
                self._i2c.write_byte_data(REG_PWR_MGMT_1, 0x00)
                time.sleep(0.1)
                self._i2c.read_word_signed(REG_ACCEL_XOUT_H)
                self.initialized = True
        except Exception:
            logger.error("[MPU6050] Re-init failed")
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
            return {'accel': dict(self._accel), 'gyro': dict(self._gyro),
                    'roll': self._roll, 'pitch': self._pitch}

    def shutdown(self):
        self._running = False
        if self._i2c:
            try:
                self._i2c.write_byte_data(REG_PWR_MGMT_1, 0x40)
            except Exception:
                pass
            try:
                self._i2c.close()
            except Exception:
                pass
        self.initialized = False
        logger.info("[MPU6050] Shutdown")
