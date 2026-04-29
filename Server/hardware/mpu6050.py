"""
MPU6050 IMU sensor module for PiCar Pro.
Reads accelerometer (X/Y/Z in g) and gyroscope (X/Y/Z in deg/s).
Uses smbus (I2C) — lightweight, no extra pip dependency.
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

    def __init__(self):
        self._bus = None
        self._running = False
        self._initialized = False
        self._thread = None
        self._lock = threading.Lock()

        # Cached sensor values
        self._accel = {'x': 0.0, 'y': 0.0, 'z': 0.0}  # in g
        self._gyro = {'x': 0.0, 'y': 0.0, 'z': 0.0}    # in deg/s
        self._roll = 0.0   # degrees
        self._pitch = 0.0  # degrees

        self._init_sensor()

    def _init_sensor(self):
        """Initialize MPU6050 via I2C and start background reading."""
        try:
            import smbus
            self._bus = smbus.SMBus(I2C_BUS)

            # Check WHO_AM_I (should return 0x68)
            who_am_i = self._bus.read_byte_data(MPU6050_ADDR, self.REG_WHO_AM_I)
            if who_am_i != 0x68:
                print(f"[MPU6050] WHO_AM_I=0x{who_am_i:02X}, expected 0x68. "
                      f"Sensor may not be connected at 0x{MPU6050_ADDR:02X}")
                return

            # Wake up MPU6050 (clear sleep bit)
            self._bus.write_byte_data(MPU6050_ADDR, self.REG_PWR_MGMT_1, 0x00)
            time.sleep(0.1)

            # Configure:
            # - Accelerometer: ±2g (register 0x1C = 0x00)
            # - Gyroscope: ±250°/s (register 0x1B = 0x00)
            self._bus.write_byte_data(MPU6050_ADDR, 0x1C, 0x00)
            self._bus.write_byte_data(MPU6050_ADDR, 0x1B, 0x00)

            # Configure DLPF (Digital Low Pass Filter) — reduce noise
            # 0x1A = 0x03 → ~44Hz bandwidth, 4.9ms delay
            self._bus.write_byte_data(MPU6050_ADDR, 0x1A, 0x03)

            # Set sample rate divider: 1kHz / (1+9) = 100Hz
            self._bus.write_byte_data(MPU6050_ADDR, 0x19, 0x09)

            self._initialized = True
            print(f"[MPU6050] Initialized at 0x{MPU6050_ADDR:02X} on I2C bus {I2C_BUS}")

            # Start background reading thread
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

        except Exception as e:
            print(f"[MPU6050] Failed to initialize: {e}")
            print("[MPU6050] IMU will not be available (non-critical)")

    def _read_word(self, addr):
        """Read a signed 16-bit word from two consecutive registers."""
        high = self._bus.read_byte_data(MPU6050_ADDR, addr)
        low = self._bus.read_byte_data(MPU6050_ADDR, addr + 1)
        val = (high << 8) | low
        if val >= 0x8000:
            val -= 0x10000
        return val

    def _read_loop(self):
        """Background thread: read sensor data at ~10Hz."""
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
                # Roll: rotation around X axis
                roll = math.atan2(ay, az) * 180.0 / math.pi
                # Pitch: rotation around Y axis
                pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 180.0 / math.pi

                # Update cached values (thread-safe)
                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._roll = round(roll, 1)
                    self._pitch = round(pitch, 1)

            except Exception:
                # I2C read error — keep last known values
                pass

            time.sleep(0.1)  # ~10Hz

    def get_data(self):
        """
        Get current IMU data as dict.
        Returns None if sensor is not initialized.

        Values:
        - accel: {x, y, z} in g (gravity units, ~9.81 m/s²)
        - gyro: {x, y, z} in deg/s
        - roll: degrees (tilt around X axis)
        - pitch: degrees (tilt around Y axis)
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
        if self._bus is not None:
            try:
                # Put MPU6050 to sleep
                self._bus.write_byte_data(MPU6050_ADDR, self.REG_PWR_MGMT_1, 0x40)
            except Exception:
                pass
        print("[MPU6050] Shutdown complete")
