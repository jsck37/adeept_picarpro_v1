"""
MPU6050 IMU sensor module for PiCar Pro.
Reads accelerometer (X/Y/Z in g) and gyroscope (X/Y/Z in deg/s).

Strategy (matches original Adeept PiCar Pro v1):
1. Try mpu6050-raspberrypi pip package first (same as original Adeept)
2. Fall back to direct smbus register access if package not installed

Fixes:
- Scans both 0x68 (AD0=LOW) and 0x69 (AD0=HIGH) addresses
- I2C bus detection (tries bus 1, then bus 0)
- Simplified initialization matching original Adeept code
"""

import threading
import time

from Server.config import MPU6050_ADDR, I2C_BUS


class MPU6050Controller:
    """
    MPU6050 IMU controller with background reading thread.

    Reads:
    - Accelerometer: X, Y, Z in g (gravity units)
    - Gyroscope: X, Y, Z in degrees/second
    - Computed roll and pitch in degrees

    All values are updated in a background thread at ~10Hz
    and cached for thread-safe reads from the web server.
    """

    # MPU6050 register addresses
    REG_PWR_MGMT_1 = 0x6B
    REG_ACCEL_XOUT_H = 0x3B
    REG_GYRO_XOUT_H = 0x43
    REG_WHO_AM_I = 0x75

    # Sensitivity: ±2g → 16384 LSB/g, ±250°/s → 131 LSB/(°/s)
    ACCEL_SCALE = 16384.0
    GYRO_SCALE = 131.0

    # Possible I2C addresses (AD0 pin state)
    POSSIBLE_ADDRS = [0x68, 0x69]

    def __init__(self):
        self._bus = None
        self._addr = None
        self._running = False
        self._initialized = False
        self._thread = None
        self._lock = threading.Lock()
        self._use_package = False  # True if using mpu6050-raspberrypi package
        self._mpu_pkg = None      # mpu6050-raspberrypi sensor instance

        # Cached sensor values
        self._accel = {'x': 0.0, 'y': 0.0, 'z': 0.0}  # in g
        self._gyro = {'x': 0.0, 'y': 0.0, 'z': 0.0}    # in deg/s
        self._roll = 0.0   # degrees
        self._pitch = 0.0  # degrees

        self._init_sensor()

    def _init_sensor(self):
        """Initialize MPU6050 — try mpu6050-raspberrypi first (like original Adeept),
        then fall back to direct smbus register access."""
        # Try mpu6050-raspberrypi package first (matches original Adeept code)
        if self._init_mpu6050_package():
            return

        # Fall back to direct smbus register access
        if self._init_smbus():
            return

        print("[MPU6050] All initialization methods failed. IMU will not be available.")
        print("[MPU6050] Troubleshooting:")
        print("[MPU6050]   1. Check wiring: SDA→GPIO2(pin3), SCL→GPIO3(pin5), VCC→3.3V, GND→GND")
        print("[MPU6050]   2. Run: i2cdetect -y 1  (should show device at 0x68 or 0x69)")
        print("[MPU6050]   3. Make sure I2C is enabled: sudo raspi-config → I2C → Enable")
        print("[MPU6050]   4. Install driver: pip3 install mpu6050-raspberrypi")

    def _init_mpu6050_package(self):
        """Initialize using mpu6050-raspberrypi pip package (same as original Adeept)."""
        try:
            from mpu6050 import mpu6050 as MPU6050Driver

            # Try preferred address first, then alternate
            addrs_to_try = [MPU6050_ADDR] + [a for a in self.POSSIBLE_ADDRS if a != MPU6050_ADDR]

            for addr in addrs_to_try:
                try:
                    sensor = MPU6050Driver(addr)
                    # Try reading to verify connection
                    accel = sensor.get_accel_data()
                    if accel and any(v != 0 for v in accel.values()):
                        self._mpu_pkg = sensor
                        self._addr = addr
                        self._initialized = True
                        self._use_package = True
                        print(f"[MPU6050] Initialized via mpu6050-raspberrypi at 0x{addr:02X}")

                        # Start background reading thread
                        self._running = True
                        self._thread = threading.Thread(target=self._read_loop_package, daemon=True)
                        self._thread.start()
                        return True
                except Exception as e:
                    print(f"[MPU6050] Package init at 0x{addr:02X} failed: {e}")
                    continue

            print("[MPU6050] mpu6050-raspberrypi package found but no device detected")
            return False

        except ImportError:
            print("[MPU6050] mpu6050-raspberrypi package not installed")
            print("[MPU6050] Fix: pip3 install mpu6050-raspberrypi")
            return False
        except Exception as e:
            print(f"[MPU6050] mpu6050-raspberrypi init failed: {e}")
            return False

    def _init_smbus(self):
        """Fallback: initialize MPU6050 via direct smbus register access."""
        try:
            import smbus

            # Detect I2C bus — try configured bus first
            bus_num = None
            for try_bus in [I2C_BUS, 1, 0]:
                try:
                    dev_path = f"/dev/i2c-{try_bus}"
                    with open(dev_path, 'rb'):
                        pass
                    bus_num = try_bus
                    break
                except (FileNotFoundError, PermissionError, OSError):
                    continue

            if bus_num is None:
                print("[MPU6050] No I2C bus found!")
                print("[MPU6050] Fix: sudo raspi-config → Interface Options → I2C → Enable")
                return False

            self._bus = smbus.SMBus(bus_num)

            # Scan for MPU6050 at possible addresses
            addrs_to_try = [MPU6050_ADDR] + [a for a in self.POSSIBLE_ADDRS if a != MPU6050_ADDR]
            addr = None
            for try_addr in addrs_to_try:
                try:
                    # Try to read WHO_AM_I register
                    who_am_i = self._bus.read_byte_data(try_addr, self.REG_WHO_AM_I)
                    if who_am_i == 0x68:  # MPU6050 WHO_AM_I value
                        addr = try_addr
                        break
                    elif who_am_i != 0:
                        print(f"[MPU6050] Found device at 0x{try_addr:02X} with WHO_AM_I=0x{who_am_i:02X} "
                              f"(not MPU6050, expected 0x68)")
                except Exception:
                    continue

            # If WHO_AM_I scan failed, try blind init at preferred address
            if addr is None:
                print("[MPU6050] WHO_AM_I scan found no MPU6050, trying blind init at "
                      f"0x{MPU6050_ADDR:02X}...")
                try:
                    # Try to wake up the chip directly
                    self._bus.write_byte_data(MPU6050_ADDR, self.REG_PWR_MGMT_1, 0x00)
                    time.sleep(0.1)
                    # Verify by reading back
                    pwr = self._bus.read_byte_data(MPU6050_ADDR, self.REG_PWR_MGMT_1)
                    if pwr == 0x00 or pwr == 0x01:
                        addr = MPU6050_ADDR
                        print(f"[MPU6050] Blind init succeeded at 0x{addr:02X}")
                except Exception:
                    pass

            if addr is None:
                print(f"[MPU6050] No MPU6050 found on I2C bus {bus_num}")
                return False

            self._addr = addr

            # Wake up MPU6050 (clear sleep bit) — same as original Adeept init
            self._bus.write_byte_data(self._addr, self.REG_PWR_MGMT_1, 0x00)
            time.sleep(0.1)

            # Configure defaults (same as mpu6050-raspberrypi library):
            # - Accelerometer: ±2g (register 0x1C = 0x00)
            # - Gyroscope: ±250°/s (register 0x1B = 0x00)
            self._bus.write_byte_data(self._addr, 0x1C, 0x00)
            self._bus.write_byte_data(self._addr, 0x1B, 0x00)

            self._initialized = True
            print(f"[MPU6050] Initialized via smbus at 0x{self._addr:02X} on I2C bus {bus_num}")

            # Start background reading thread
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            return True

        except ImportError:
            print("[MPU6050] smbus module not installed!")
            print("[MPU6050] Fix: sudo apt install python3-smbus i2c-tools")
            return False
        except FileNotFoundError:
            print("[MPU6050] I2C device not found!")
            print("[MPU6050] Fix: sudo raspi-config → Interface Options → I2C → Enable, then reboot")
            return False
        except PermissionError:
            print("[MPU6050] Permission denied accessing I2C!")
            print("[MPU6050] Fix: run with sudo, or add user to i2c group: sudo usermod -aG i2c $USER")
            return False
        except Exception as e:
            print(f"[MPU6050] smbus init failed: {e}")
            return False

    def _read_word(self, addr):
        """Read a signed 16-bit word from two consecutive registers."""
        high = self._bus.read_byte_data(self._addr, addr)
        low = self._bus.read_byte_data(self._addr, addr + 1)
        val = (high << 8) | low
        if val >= 0x8000:
            val -= 0x10000
        return val

    def _read_loop(self):
        """Background thread: read sensor data at ~10Hz via smbus."""
        import math

        while self._running:
            try:
                # Read accelerometer
                ax = self._read_word(self.REG_ACCEL_XOUT_H) / self.ACCEL_SCALE
                ay = self._read_word(self.REG_ACCEL_XOUT_H + 2) / self.ACCEL_SCALE
                az = self._read_word(self.REG_ACCEL_XOUT_H + 4) / self.ACCEL_SCALE

                # Read gyroscope
                gx = self._read_word(self.REG_GYRO_XOUT_H) / self.GYRO_SCALE
                gy = self._read_word(self.REG_GYRO_XOUT_H + 2) / self.GYRO_SCALE
                gz = self._read_word(self.REG_GYRO_XOUT_H + 4) / self.GYRO_SCALE

                # Compute roll and pitch from accelerometer
                roll = math.atan2(ay, az) * 180.0 / math.pi
                pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 180.0 / math.pi

                # Update cached values (thread-safe)
                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._roll = round(roll, 1)
                    self._pitch = round(pitch, 1)

            except Exception:
                pass  # I2C read error — keep last known values

            time.sleep(0.1)  # ~10Hz

    def _read_loop_package(self):
        """Background thread: read sensor data via mpu6050-raspberrypi package."""
        import math

        while self._running:
            try:
                accel = self._mpu_pkg.get_accel_data()
                gyro = self._mpu_pkg.get_gyro_data()

                ax = accel.get('x', 0.0)
                ay = accel.get('y', 0.0)
                az = accel.get('z', 0.0)
                gx = gyro.get('x', 0.0)
                gy = gyro.get('y', 0.0)
                gz = gyro.get('z', 0.0)

                roll = math.atan2(ay, az) * 180.0 / math.pi
                pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 180.0 / math.pi

                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._roll = round(roll, 1)
                    self._pitch = round(pitch, 1)

            except Exception:
                pass

            time.sleep(0.1)  # ~10Hz

    def get_data(self):
        """
        Get current IMU data as dict.
        Returns None if sensor is not initialized.
        """
        if not self._initialized:
            return None

        with self._lock:
            return {
                'accel': dict(self._accel),
                'gyro': dict(self._gyro),
                'roll': self._roll,
                'pitch': self._pitch,
            }

    @property
    def initialized(self):
        """Whether MPU6050 was successfully initialized."""
        return self._initialized

    def shutdown(self):
        """Stop background thread and release I2C bus."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._bus is not None and self._addr is not None:
            try:
                self._bus.write_byte_data(self._addr, self.REG_PWR_MGMT_1, 0x40)
            except Exception:
                pass
        print("[MPU6050] Shutdown complete")
