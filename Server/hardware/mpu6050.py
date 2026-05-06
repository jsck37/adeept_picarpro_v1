"""MPU6050 IMU — accelerometer/gyroscope with robust initialization.

Key fixes vs original:
- Proper wake-up sequence: write 0x00 to PWR_MGMT_1, then wait 100ms
- WHO_AM_I verification before starting the read thread
- Re-initialization on persistent I2C errors
- Background retry when sensor is not connected at startup
- Configurable sample rate divider and DLPF
- Tries both addresses 0x68 and 0x69 (AD0 LOW / HIGH)
- Fixed: _read_signed_word() was missing addr parameter in test read
"""

import math
import threading
import time
from Server.config import MPU6050_ADDR, I2C_BUS

# MPU6050 register addresses
REG_PWR_MGMT_1   = 0x6B
REG_SMPLRT_DIV   = 0x19
REG_CONFIG       = 0x1A
REG_GYRO_CONFIG  = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_WHO_AM_I     = 0x75

ACCEL_SCALE = 16384.0   # +/- 2g
GYRO_SCALE  = 131.0     # +/- 250 deg/s

RETRY_INTERVAL = 5.0     # seconds between re-init attempts
READ_INTERVAL  = 0.1     # 10 Hz read rate
MAX_ERRORS     = 20      # re-init after this many consecutive errors

# Possible I2C addresses
POSSIBLE_ADDRS = [0x68, 0x69]


class MPU6050Controller:

    def __init__(self):
        self._bus = None
        self._addr = MPU6050_ADDR
        self._running = False
        self._initialized = False
        self._thread = None
        self._lock = threading.Lock()
        self._use_package = False
        self._sensor = None
        self._consecutive_errors = 0

        self._accel = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._gyro  = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._roll  = 0.0
        self._pitch = 0.0

        self._init_sensor()

    # ── Initialization ──────────────────────────────────────────────────

    def _init_sensor(self):
        # Try the configured address first, then alternatives
        for addr in [self._addr] + [a for a in POSSIBLE_ADDRS if a != self._addr]:
            if self._init_package(addr):
                return
            if self._init_smbus(addr):
                return
        print("[MPU6050] Not found on I2C. Will retry in background.")
        self._running = True
        self._thread = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    def _init_package(self, addr=None):
        """Initialize using mpu6050-raspberrypi package."""
        if addr is None:
            addr = self._addr
        try:
            from mpu6050 import mpu6050 as MPU6050Driver
            sensor = MPU6050Driver(addr, bus=I2C_BUS)
            accel = sensor.get_accel_data()
            if accel:
                self._addr = addr
                self._sensor = sensor
                self._initialized = True
                self._use_package = True
                self._running = True
                self._thread = threading.Thread(
                    target=self._read_loop_package, daemon=True
                )
                self._thread.start()
                print(f"[MPU6050] Package driver OK at 0x{addr:02X}")
                return True
        except ImportError:
            if addr == self._addr:
                print("[MPU6050] mpu6050-raspberrypi not installed, trying smbus")
        except Exception as e:
            print(f"[MPU6050] Package init failed at 0x{addr:02X}: {e}")
        return False

    def _init_smbus(self, addr=None):
        """Raw smbus I2C with proper wake-up.

        The #1 reason MPU6050 "doesn't work" is that the chip ships in
        SLEEP mode. Writing 0x00 to PWR_MGMT_1 (0x6B) wakes it up and
        selects PLL with X-gyro reference as the clock source.
        """
        if addr is None:
            addr = self._addr
        try:
            import smbus
            bus = smbus.SMBus(I2C_BUS)

            # 1. Try to detect chip at this address
            try:
                who_am_i = bus.read_byte_data(addr, REG_WHO_AM_I)
            except Exception as e:
                print(f"[MPU6050] No response at 0x{addr:02X}: {e}")
                return False

            if who_am_i not in (0x68, 0x72, 0x71):
                print(f"[MPU6050] WHO_AM_I=0x{who_am_i:02X} at 0x{addr:02X} — unexpected")
                return False

            # 2. Wake up — clear SLEEP bit
            bus.write_byte_data(addr, REG_PWR_MGMT_1, 0x00)
            time.sleep(0.1)  # 100ms for oscillator to stabilize

            # 3. Verify chip is awake by re-reading WHO_AM_I
            who_am_i2 = bus.read_byte_data(addr, REG_WHO_AM_I)
            if who_am_i2 != who_am_i:
                print(f"[MPU6050] WHO_AM_I changed after wake-up, unstable")

            # 4. Sample rate: 1kHz / (1+7) = 125 Hz
            bus.write_byte_data(addr, REG_SMPLRT_DIV, 0x07)

            # 5. DLPF: ~44 Hz bandwidth
            bus.write_byte_data(addr, REG_CONFIG, 0x03)

            # 6. Gyro: +/- 250 deg/s
            bus.write_byte_data(addr, REG_GYRO_CONFIG, 0x00)

            # 7. Accel: +/- 2g
            bus.write_byte_data(addr, REG_ACCEL_CONFIG, 0x00)

            # 8. Test read — FIXED: was missing addr parameter
            _ = self._read_signed_word(bus, addr, 0x3B)

            self._addr = addr
            self._bus = bus
            self._initialized = True
            self._running = True
            self._thread = threading.Thread(
                target=self._read_loop_smbus, daemon=True
            )
            self._thread.start()
            print(f"[MPU6050] smbus driver OK at 0x{addr:02X} "
                  f"(WHO_AM_I=0x{who_am_i:02X})")
            return True

        except Exception as e:
            print(f"[MPU6050] smbus init failed at 0x{addr:02X}: {e}")
        return False

    # ── I2C helper ──────────────────────────────────────────────────────

    @staticmethod
    def _read_signed_word(bus, addr, reg):
        """Read a 16-bit signed value from two consecutive registers."""
        high = bus.read_byte_data(addr, reg)
        low  = bus.read_byte_data(addr, reg + 1)
        val  = (high << 8) | low
        if val >= 0x8000:
            val -= 0x10000
        return val

    # ── Background read loops ───────────────────────────────────────────

    def _read_loop_smbus(self):
        bus  = self._bus
        addr = self._addr
        while self._running:
            try:
                ax = self._read_signed_word(bus, addr, 0x3B) / ACCEL_SCALE
                ay = self._read_signed_word(bus, addr, 0x3D) / ACCEL_SCALE
                az = self._read_signed_word(bus, addr, 0x3F) / ACCEL_SCALE
                gx = self._read_signed_word(bus, addr, 0x43) / GYRO_SCALE
                gy = self._read_signed_word(bus, addr, 0x45) / GYRO_SCALE
                gz = self._read_signed_word(bus, addr, 0x47) / GYRO_SCALE

                roll  = math.atan2(ay, az) * 180.0 / math.pi
                pitch = math.atan2(-ax, math.sqrt(ay*ay + az*az)) * 180.0 / math.pi

                with self._lock:
                    self._accel = {'x': round(ax,3), 'y': round(ay,3), 'z': round(az,3)}
                    self._gyro  = {'x': round(gx,1), 'y': round(gy,1), 'z': round(gz,1)}
                    self._roll  = round(roll, 1)
                    self._pitch = round(pitch, 1)

                self._consecutive_errors = 0

            except Exception:
                self._consecutive_errors += 1
                if self._consecutive_errors >= MAX_ERRORS:
                    self._reinit_smbus()

            time.sleep(READ_INTERVAL)

    def _read_loop_package(self):
        while self._running:
            try:
                accel = self._sensor.get_accel_data()
                gyro  = self._sensor.get_gyro_data()
                ax, ay, az = accel.get('x',0), accel.get('y',0), accel.get('z',0)
                gx, gy, gz = gyro.get('x',0), gyro.get('y',0), gyro.get('z',0)

                roll  = math.atan2(ay, az) * 180.0 / math.pi
                pitch = math.atan2(-ax, math.sqrt(ay*ay + az*az)) * 180.0 / math.pi

                with self._lock:
                    self._accel = {'x': round(ax,3), 'y': round(ay,3), 'z': round(az,3)}
                    self._gyro  = {'x': round(gx,1), 'y': round(gy,1), 'z': round(gz,1)}
                    self._roll  = round(roll, 1)
                    self._pitch = round(pitch, 1)

                self._consecutive_errors = 0

            except Exception:
                self._consecutive_errors += 1
                if self._consecutive_errors >= MAX_ERRORS:
                    self._reinit_package()

            time.sleep(READ_INTERVAL)

    # ── Retry / re-init ─────────────────────────────────────────────────

    def _retry_loop(self):
        while self._running and not self._initialized:
            time.sleep(RETRY_INTERVAL)
            for addr in POSSIBLE_ADDRS:
                if self._init_package(addr) or self._init_smbus(addr):
                    print("[MPU6050] Re-initialized successfully!")
                    return

    def _reinit_smbus(self):
        self._initialized = False
        self._consecutive_errors = 0
        try:
            if self._bus:
                self._bus.write_byte_data(self._addr, REG_PWR_MGMT_1, 0x00)
                time.sleep(0.1)
                _ = self._read_signed_word(self._bus, self._addr, 0x3B)
                self._initialized = True
                print("[MPU6050] smbus re-init OK")
        except Exception:
            print("[MPU6050] smbus re-init failed, will keep trying...")

    def _reinit_package(self):
        self._consecutive_errors = 0
        try:
            if self._sensor:
                accel = self._sensor.get_accel_data()
                if accel:
                    self._initialized = True
                    return
        except Exception:
            pass
        self._init_smbus()

    # ── Public API ──────────────────────────────────────────────────────

    def get_data(self):
        if not self._initialized:
            return None
        with self._lock:
            return {
                'accel': dict(self._accel),
                'gyro':  dict(self._gyro),
                'roll':  self._roll,
                'pitch': self._pitch,
            }

    @property
    def initialized(self):
        return self._initialized

    def shutdown(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._bus is not None:
            try:
                self._bus.write_byte_data(self._addr, REG_PWR_MGMT_1, 0x40)
            except Exception:
                pass
        print("[MPU6050] Shutdown")
