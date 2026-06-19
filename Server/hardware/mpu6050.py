"""
MPU6050 controller.

Strategy (most reliable first):
  1. Use the external ``mpu6050`` Python package — this is the same
     library the original Adeept PiCar-Pro examples use
     (``from mpu6050 import mpu6050``).  It is well-tested, simple and
     hides all the bit-banging details.
  2. Fall back to a minimal in-tree SMBus driver that reads the
     WHO_AM_I register, configures the sensor and reads accel/gyro in
     one 14-byte block.  Works with both ``smbus2`` and ``robot_hat``.

The original project's controller tried to be too clever (multiple
address scans, WHO_AM_I guessing, double retry-loops, error
re-init). That complexity is what made it unreliable. This version is
deliberately small.
"""

import math, threading, time

from config import MPU6050_ADDR, I2C_BUS
from Server.logger import logger

# --- Register map --------------------------------------------------------
REG_PWR_MGMT_1   = 0x6B
REG_SMPLRT_DIV   = 0x19
REG_CONFIG       = 0x1A
REG_GYRO_CONFIG  = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_WHO_AM_I     = 0x75
REG_ACCEL_XOUT_H = 0x3B
REG_GYRO_XOUT_H  = 0x43

ACCEL_SCALE = 16384.0   # ±2g full scale -> 16384 LSB/g
GYRO_SCALE  = 131.0     # ±250 dps full scale -> 131 LSB/(dps)
WHO_AM_I_VALUES = (0x68, 0x72, 0x71, 0x70)   # MPU6050 / MPU6500 / MPU9250
POSSIBLE_ADDRS  = (0x68, 0x69)


# --- I2C backend abstraction --------------------------------------------
_I2C_BACKEND = None
try:
    from mpu6050 import mpu6050 as _ext_mpu6050   # external library
    _I2C_BACKEND = "ext_pkg"
except ImportError:
    pass

if _I2C_BACKEND is None:
    try:
        from smbus2 import SMBus as _SMBus
        _I2C_BACKEND = "smbus2"
    except ImportError:
        try:
            import smbus as _SMBus
            _I2C_BACKEND = "smbus"
        except ImportError:
            try:
                from robot_hat import I2C as _RobotHatI2C
                _I2C_BACKEND = "robot_hat"
            except ImportError:
                _I2C_BACKEND = None


class _SMBusWrapper:
    """Tiny adapter that gives the same API for smbus2 / smbus / robot_hat."""

    def __init__(self, address, bus=1):
        self.address = address
        self._backend = _I2C_BACKEND
        self._bus = None
        self._i2c = None
        if self._backend in ("smbus2", "smbus"):
            self._bus = _SMBus(bus)
        elif self._backend == "robot_hat":
            self._i2c = _RobotHatI2C(address=address, bus=bus)
        else:
            raise RuntimeError("No I2C backend available. "
                               "Install: pip3 install mpu6050 smbus2")

    def write_byte_data(self, reg, data):
        if self._backend == "robot_hat":
            return self._i2c._write_byte_data(reg, data)
        return self._bus.write_byte_data(self.address, reg, data)

    def read_byte_data(self, reg):
        if self._backend == "robot_hat":
            return self._i2c._read_byte_data(reg)
        return self._bus.read_byte_data(self.address, reg)

    def read_i2c_block(self, reg, length):
        if self._backend == "robot_hat":
            r = self._i2c._read_i2c_block_data(reg, length)
            if r is False:
                raise OSError("I2C block read failed")
            return r
        return self._bus.read_i2c_block_data(self.address, reg, length)

    def close(self):
        try:
            if self._backend == "robot_hat":
                if getattr(self._i2c, "_smbus", None):
                    self._i2c._smbus.close()
                    self._i2c._smbus = None
            elif self._bus:
                self._bus.close()
                self._bus = None
        except Exception:
            pass


# --- I2C scan helpers (used by /api/i2c_scan) ----------------------------
def i2c_scan(bus_number=None):
    bus_number = bus_number or I2C_BUS
    found = []
    try:
        if _I2C_BACKEND in ("smbus2", "smbus"):
            bus = _SMBus(bus_number)
            for addr in range(0x03, 0x78):
                try:
                    bus.read_byte(addr)
                    found.append(addr)
                except Exception:
                    pass
            bus.close()
        elif _I2C_BACKEND == "robot_hat":
            tmp = _RobotHatI2C(address=0x00, bus=bus_number)
            found = list(tmp.scan() or [])
    except Exception as e:
        logger.error(f"[I2C] Scan failed: {e}")
    return found


def find_mpu6050_on_bus(bus_number=None):
    bus_number = bus_number or I2C_BUS
    for addr in i2c_scan(bus_number):
        try:
            w = _SMBusWrapper(addr, bus_number)
            who = w.read_byte_data(REG_WHO_AM_I)
            w.close()
            if who in WHO_AM_I_VALUES:
                logger.info(f"[MPU6050] Found WHO_AM_I=0x{who:02X} at 0x{addr:02X}")
                return addr, who
        except Exception:
            continue
    return None, None


# --- Controller ----------------------------------------------------------
class MPU6050Controller:
    def __init__(self):
        self._ext = None          # external mpu6050 library instance
        self._bus = None          # raw SMBus wrapper (fallback path)
        self._addr = MPU6050_ADDR
        self._running = False
        self._lock = threading.Lock()
        self._errors = 0
        self.initialized = False
        self._accel = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._gyro  = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._roll = self._pitch = 0.0
        self._thread = None
        self._init_sensor()

    # ---- init paths ----------------------------------------------------
    def _init_sensor(self):
        # Path 1: external mpu6050 library (matches original Adeept examples)
        if _I2C_BACKEND == "ext_pkg":
            for addr in POSSIBLE_ADDRS:
                if self._try_ext(addr):
                    return
        # Path 2: raw SMBus driver
        for addr in POSSIBLE_ADDRS:
            if self._try_raw(addr):
                return
        # Path 3: scan whole bus
        found_addr, _ = find_mpu6050_on_bus()
        if found_addr and (
            self._try_ext(found_addr) if _I2C_BACKEND == "ext_pkg" else False
        ):
            return
        if found_addr and self._try_raw(found_addr):
            return
        logger.warning("[MPU6050] Not found — will retry in background")
        self._running = True
        self._thread = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    def _try_ext(self, addr):
        try:
            self._ext = _ext_mpu6050(addr)
            # Sanity probe: reading accel data proves the sensor is alive.
            self._ext.get_accel_data()
            self._addr = addr
            self.initialized = True
            self._running = True
            self._thread = threading.Thread(target=self._read_loop_ext, daemon=True)
            self._thread.start()
            logger.info(f"[MPU6050] OK at 0x{addr:02X} (external mpu6050 library)")
            return True
        except Exception as e:
            logger.info(f"[MPU6050] ext_pkg @0x{addr:02X} failed: {e}")
            self._ext = None
            return False

    def _try_raw(self, addr):
        try:
            bus = _SMBusWrapper(addr, I2C_BUS)
            who = bus.read_byte_data(REG_WHO_AM_I)
            if who not in WHO_AM_I_VALUES:
                logger.info(f"[MPU6050] @0x{addr:02X} WHO_AM_I=0x{who:02X} — not an MPU6050")
                bus.close()
                return False
            bus.write_byte_data(REG_PWR_MGMT_1, 0x00)   # wake up
            time.sleep(0.05)
            bus.write_byte_data(REG_SMPLRT_DIV, 0x07)
            bus.write_byte_data(REG_CONFIG, 0x03)
            bus.write_byte_data(REG_GYRO_CONFIG, 0x00)
            bus.write_byte_data(REG_ACCEL_CONFIG, 0x00)
            bus.read_i2c_block(REG_ACCEL_XOUT_H, 14)   # prime the pipe
            self._addr = addr
            self._bus = bus
            self.initialized = True
            self._running = True
            self._errors = 0
            self._thread = threading.Thread(target=self._read_loop_raw, daemon=True)
            self._thread.start()
            logger.info(f"[MPU6050] OK at 0x{addr:02X} "
                        f"(WHO_AM_I=0x{who:02X}, backend={_I2C_BACKEND})")
            return True
        except Exception as e:
            logger.info(f"[MPU6050] raw @0x{addr:02X} failed: {e}")
            return False

    # ---- read loops ----------------------------------------------------
    def _read_loop_ext(self):
        while self._running:
            try:
                accel = self._ext.get_accel_data()
                gyro  = self._ext.get_gyro_data()
                ax, ay, az = accel['x'], accel['y'], accel['z']
                gx, gy, gz = gyro['x'],  gyro['y'],  gyro['z']
                roll  = math.atan2(ay, az) * 57.2958
                pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 57.2958
                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro  = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._roll  = round(roll, 1)
                    self._pitch = round(pitch, 1)
                self._errors = 0
            except Exception:
                self._errors += 1
                if self._errors >= 30:
                    logger.error("[MPU6050] too many ext-pkg errors — reinit")
                    self._reinit()
            time.sleep(0.1)

    def _read_loop_raw(self):
        while self._running:
            try:
                raw = self._bus.read_i2c_block(REG_ACCEL_XOUT_H, 14)
                ax = self._s16((raw[0]  << 8) | raw[1])  / ACCEL_SCALE
                ay = self._s16((raw[2]  << 8) | raw[3])  / ACCEL_SCALE
                az = self._s16((raw[4]  << 8) | raw[5])  / ACCEL_SCALE
                # raw[6..7] = temperature, skip
                gx = self._s16((raw[8]  << 8) | raw[9])  / GYRO_SCALE
                gy = self._s16((raw[10] << 8) | raw[11]) / GYRO_SCALE
                gz = self._s16((raw[12] << 8) | raw[13]) / GYRO_SCALE
                roll  = math.atan2(ay, az) * 57.2958
                pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 57.2958
                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro  = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._roll  = round(roll, 1)
                    self._pitch = round(pitch, 1)
                self._errors = 0
            except Exception:
                self._errors += 1
                if self._errors >= 30:
                    logger.error("[MPU6050] too many raw-read errors — reinit")
                    self._reinit()
            time.sleep(0.1)

    # ---- recovery ------------------------------------------------------
    def _retry_loop(self):
        """Keep retrying every 15s indefinitely. The sensor might come
        online later (e.g. due to a slow I2C bus reset, hot-plug, etc.).
        Once we successfully connect, this thread exits."""
        retry = 0
        while self._running and not self.initialized:
            retry += 1
            time.sleep(15 if retry > 5 else 5)
            if not self._running or self.initialized:
                return
            logger.info(f"[MPU6050] Retry #{retry}...")
            for addr in POSSIBLE_ADDRS:
                if _I2C_BACKEND == "ext_pkg" and self._try_ext(addr):
                    return
                if self._try_raw(addr):
                    return
            # Also scan the bus periodically (in case the address changed).
            found_addr, _ = find_mpu6050_on_bus()
            if found_addr:
                if _I2C_BACKEND == "ext_pkg" and self._try_ext(found_addr):
                    return
                if self._try_raw(found_addr):
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
        self._ext = None
        self._running = True
        self._thread = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    # ---- public API ----------------------------------------------------
    @staticmethod
    def _s16(v):
        return v - 0x10000 if v >= 0x8000 else v

    def get_data(self):
        if not self.initialized:
            return None
        with self._lock:
            return {
                'accel': dict(self._accel),
                'gyro':  dict(self._gyro),
                'roll':  self._roll,
                'pitch': self._pitch,
            }

    def shutdown(self):
        self._running = False
        if self._bus:
            try:
                self._bus.write_byte_data(REG_PWR_MGMT_1, 0x40)   # sleep
            except Exception:
                pass
            try:
                self._bus.close()
            except Exception:
                pass
        self._bus = None
        self._ext = None
        self.initialized = False
        logger.info("[MPU6050] Shutdown")
