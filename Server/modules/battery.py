#!/usr/bin/env python3
"""Battery Monitor — Read battery voltage via ADS7830 ADC."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.hardware.voltage import VoltageMonitor


def main():
    print("[Battery] Monitoring battery... Press Ctrl+C to stop.")
    monitor = VoltageMonitor()

    try:
        while True:
            voltage = monitor.get_voltage()
            percent = monitor.get_percentage()
            bar_len = 20
            filled = int(bar_len * percent / 100)
            bar = '█' * filled + '░' * (bar_len - filled)
            print(f"  [{bar}] {voltage:.2f}V ({percent}%)")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.shutdown()
        print("[Battery] Done.")


if __name__ == '__main__':
    main()
