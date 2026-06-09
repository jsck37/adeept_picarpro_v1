import math, threading, time
from config import MPU6050_ADDR, I2C_BUS
from Server.logger import logger

REG_PWR_MGMT_1 = 0x6B
REG_SMPLRT_DIV = 0x19
REG_CONFIG = 0x1A
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_WHO_AM_I = 0x75

ACCEL_SCALE = 16384.0
GYRO_SCALE = 131.0
WHO_AM_I_VALUES = (0x68, 0x72, 0x71)
POSSIBLE_ADDRS = [0x68, 0x69]


def i2c_scan(bus_number=None):
    bus_number = bus_number or I2C_BUS
    found = []
    try:
        import smbus
        bus = smbus.SMBus(bus_number)
        for addr in range(0x03, 0x78):
            try:
                bus.read_byte(addr)
                found.append(addr)
            except Exception:
                pass
        bus.close()
    except Exception as e:
        logger.error(f"[I2C] Scan failed: {e}")
    return found


def find_mpu6050_on_bus(bus_number=None):
    bus_number = bus_number or I2C_BUS
    try:
        import smbus
        bus = smbus.SMBus(bus_number)
        for addr in range(0x03, 0x78):
            try:
                who = bus.read_byte_data(addr, REG_WHO_AM_I)
                if who in WHO_AM_I_VALUES:
                    logger.info(f"[MPU6050] Found WHO_AM_I=0x{who:02X} at 0x{addr:02X}")
                    bus.close()
                    return addr, who
            except Exception:
                pass
        bus.close()
    except Exception as e:
        logger.error(f"[MPU6050] Bus scan failed: {e}")
    return None, None


class MPU6050Controller:
    def __init__(self):
        self._bus = None
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
            if self._try_smbus(addr):
                return
        logger.warning("[MPU6050] Standard addresses failed, scanning bus...")
        found_addr, _ = find_mpu6050_on_bus()
        if found_addr and self._try_smbus(found_addr):
            return
        logger.warning("[MPU6050] Not found — will retry in background")
        self._running = True
        self._thread = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    def _try_smbus(self, addr):
        try:
            import smbus
            bus = smbus.SMBus(I2C_BUS)
            who = bus.read_byte_data(addr, REG_WHO_AM_I)
            if who not in WHO_AM_I_VALUES:
                logger.warning(f"[MPU6050] WHO_AM_I=0x{who:02X} at 0x{addr:02X} — unexpected")
                bus.close()
                return False
            bus.write_byte_data(addr, REG_PWR_MGMT_1, 0x00)
            time.sleep(0.1)
            bus.write_byte_data(addr, REG_SMPLRT_DIV, 0x07)
            bus.write_byte_data(addr, REG_CONFIG, 0x03)
            bus.write_byte_data(addr, REG_GYRO_CONFIG, 0x00)
            bus.write_byte_data(addr, REG_ACCEL_CONFIG, 0x00)
            self._read_word(bus, addr, 0x3B)
            self._addr = addr
            self._bus = bus
            self.initialized = True
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            logger.info(f"[MPU6050] OK at 0x{addr:02X} (WHO_AM_I=0x{who:02X})")
            return True
        except Exception as e:
            logger.error(f"[MPU6050] smbus@0x{addr:02X} failed: {e}")
        return False

    @staticmethod
    def _read_word(bus, addr, reg):
        hi = bus.read_byte_data(addr, reg)
        lo = bus.read_byte_data(addr, reg + 1)
        v = (hi << 8) | lo
        return v - 0x10000 if v >= 0x8000 else v

    def _read_loop(self):
        while self._running:
            try:
                ax = self._read_word(self._bus, self._addr, 0x3B) / ACCEL_SCALE
                ay = self._read_word(self._bus, self._addr, 0x3D) / ACCEL_SCALE
                az = self._read_word(self._bus, self._addr, 0x3F) / ACCEL_SCALE
                gx = self._read_word(self._bus, self._addr, 0x43) / GYRO_SCALE
                gy = self._read_word(self._bus, self._addr, 0x45) / GYRO_SCALE
                gz = self._read_word(self._bus, self._addr, 0x47) / GYRO_SCALE
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
                if self._try_smbus(addr):
                    return
            if retry % 3 == 0:
                found, _ = find_mpu6050_on_bus()
                if found and self._try_smbus(found):
                    return

    def _reinit(self):
        self.initialized = False
        self._errors = 0
        try:
            if self._bus:
                self._bus.write_byte_data(self._addr, REG_PWR_MGMT_1, 0x00)
                time.sleep(0.1)
                self._read_word(self._bus, self._addr, 0x3B)
                self.initialized = True
        except Exception:
            logger.error("[MPU6050] Re-init failed")

    def get_data(self):
        if not self.initialized:
            return None
        with self._lock:
            return {'accel': dict(self._accel), 'gyro': dict(self._gyro),
                    'roll': self._roll, 'pitch': self._pitch}

    def shutdown(self):
        self._running = False
        if self._bus:
            try:
                self._bus.write_byte_data(self._addr, REG_PWR_MGMT_1, 0x40)
            except Exception:
                pass
        logger.info("[MPU6050] Shutdown")
