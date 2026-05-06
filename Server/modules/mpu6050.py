#!/usr/bin/env python3
"""MPU6050 Accelerometer — Read X/Y/Z acceleration.

Uses injected hardware from the running server (no GPIO conflicts).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(hw=None):
    """hw: dict of hardware controllers from SharedState (optional)."""
    if hw and 'mpu6050' in hw and hw['mpu6050'] and hw['mpu6050'].initialized:
        print("[MPU6050] Reading accelerometer... Press Ctrl+C to stop (auto 5s).")
        mpu = hw['mpu6050']
        try:
            for _ in range(10):
                data = mpu.get_data()
                if data:
                    a = data['accel']
                    g = data['gyro']
                    print(f"  X:{a['x']:+.2f}g Y:{a['y']:+.2f}g Z:{a['z']:+.2f}g "
                          f"R:{data['roll']:.1f} P:{data['pitch']:.1f}")
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        print("[MPU6050] Done.")
    else:
        # Standalone mode
        from Server.config import MPU6050_ADDR, I2C_BUS
        print("[MPU6050] Reading accelerometer... Press Ctrl+C to stop.")
        try:
            import smbus
            bus = smbus.SMBus(I2C_BUS)
        except Exception as e:
            print(f"  Error: {e}")
            return
        try:
            bus.write_byte_data(MPU6050_ADDR, 0x6B, 0)
        except Exception as e:
            print(f"  Cannot access MPU6050 at 0x{MPU6050_ADDR:02X}: {e}")
            return

        def read_word(addr):
            high = bus.read_byte_data(MPU6050_ADDR, addr)
            low = bus.read_byte_data(MPU6050_ADDR, addr + 1)
            val = (high << 8) + low
            if val >= 0x8000:
                val -= 0x10000
            return val

        try:
            while True:
                x = read_word(0x3B) / 16384.0
                y = read_word(0x3D) / 16384.0
                z = read_word(0x3F) / 16384.0
                print(f"  X: {x:+.2f}g  Y: {y:+.2f}g  Z: {z:+.2f}g")
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            print("[MPU6050] Done.")


if __name__ == '__main__':
    main()
