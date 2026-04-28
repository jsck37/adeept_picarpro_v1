#!/usr/bin/env python3
"""MPU6050 Accelerometer — Read X/Y/Z acceleration."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import MPU6050_ADDR, I2C_BUS


def main():
    print("[MPU6050] Reading accelerometer... Press Ctrl+C to stop.")

    try:
        import smbus
        bus = smbus.SMBus(I2C_BUS)
    except Exception as e:
        print(f"  Error: {e}")
        return

    # Wake up MPU6050
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
