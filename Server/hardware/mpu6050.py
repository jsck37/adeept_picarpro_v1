"""MPU6050 IMU — accelerometer/gyroscope, roll/pitch calculation."""

import math
import threading
import time
from Server.config import MPU6050_ADDR, I2C_BUS


class MPU6050Controller:

    ACCEL_SCALE = 16384.0
    GYRO_SCALE = 131.0

    def __init__(self):
        self._bus = None
        self._addr = None
        self._running = False
        self._initialized = False
        self._thread = None
        self._lock = threading.Lock()
        self._sensor = None
        self._use_package = False
        self._accel = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._gyro = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._roll = 0.0
        self._pitch = 0.0
        self._init_sensor()

    def _init_sensor(self):
        if self._init_package():
            return
        if self._init_smbus():
            return
        print("[MPU6050] Not found. Check wiring and i2cdetect.")

    def _init_package(self):
        """Initialize using mpu6050-raspberrypi package (same as original Adeept repo)."""
        try:
            from mpu6050 import mpu6050 as MPU6050Driver
            sensor = MPU6050Driver(MPU6050_ADDR, bus=I2C_BUS)
            accel = sensor.get_accel_data()
            if accel:
                self._sensor = sensor
                self._addr = MPU6050_ADDR
                self._initialized = True
                self._use_package = True
                self._running = True
                self._thread = threading.Thread(target=self._read_loop_package, daemon=True)
                self._thread.start()
                print(f"[MPU6050] Package driver at 0x{MPU6050_ADDR:02X}")
                return True
        except ImportError:
            print("[MPU6050] mpu6050-raspberrypi not installed, trying smbus fallback")
        except Exception as e:
            print(f"[MPU6050] Package init failed: {e}")
        return False

    def _init_smbus(self):
        """Fallback: raw smbus I2C access."""
        try:
            import smbus
            self._bus = smbus.SMBus(I2C_BUS)
            addr = MPU6050_ADDR
            self._bus.write_byte_data(addr, 0x6B, 0x00)
            time.sleep(0.1)
            self._bus.write_byte_data(addr, 0x1C, 0x00)
            self._bus.write_byte_data(addr, 0x1B, 0x00)
            self._addr = addr
            self._initialized = True
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            print(f"[MPU6050] smbus fallback at 0x{addr:02X}")
            return True
        except Exception as e:
            print(f"[MPU6050] smbus init failed: {e}")
        return False

    def _read_word(self, addr):
        high = self._bus.read_byte_data(self._addr, addr)
        low = self._bus.read_byte_data(self._addr, addr + 1)
        val = (high << 8) | low
        if val >= 0x8000:
            val -= 0x10000
        return val

    def _read_loop(self):
        while self._running:
            try:
                ax = self._read_word(0x3B) / self.ACCEL_SCALE
                ay = self._read_word(0x3D) / self.ACCEL_SCALE
                az = self._read_word(0x3F) / self.ACCEL_SCALE
                gx = self._read_word(0x43) / self.GYRO_SCALE
                gy = self._read_word(0x45) / self.GYRO_SCALE
                gz = self._read_word(0x47) / self.GYRO_SCALE
                roll = math.atan2(ay, az) * 180.0 / math.pi
                pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 180.0 / math.pi
                with self._lock:
                    self._accel = {'x': round(ax, 3), 'y': round(ay, 3), 'z': round(az, 3)}
                    self._gyro = {'x': round(gx, 1), 'y': round(gy, 1), 'z': round(gz, 1)}
                    self._roll = round(roll, 1)
                    self._pitch = round(pitch, 1)
            except Exception:
                pass
            time.sleep(0.1)

    def _read_loop_package(self):
        while self._running:
            try:
                accel = self._sensor.get_accel_data()
                gyro = self._sensor.get_gyro_data()
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
            time.sleep(0.1)

    def get_data(self):
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
        return self._initialized

    def shutdown(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._bus is not None and self._addr is not None:
            try:
                self._bus.write_byte_data(self._addr, 0x6B, 0x40)
            except Exception:
                pass
        print("[MPU6050] Shutdown")
