#!/usr/bin/env python3
"""
Blue breath backlight effect while waiting for WiFi connection to "adeept_robot".
Runs on first boot until the robot connects to WiFi or starts the web server.
Stops automatically when WiFi is connected or when the main server starts.
"""

import time
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TARGET_SSID = "adeept_robot"
BREATH_COLOR = (0, 100, 255)  # Blue
BREATH_SPEED = 0.02


def is_wifi_connected():
    try:
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME,DEVICE', 'con', 'show', '--active'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split(':')
                if len(parts) >= 2 and parts[1]:
                    return True
    except Exception:
        pass
    return False


def check_server_running():
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'WebServer.py'],
            capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0
    except Exception:
        return False


def run_breath():
    try:
        from Server.hardware.leds_ws2812 import LEDController
        leds = LEDController()
        if not leds._initialized:
            print("[BootBacklight] LED strip not available, exiting")
            return
        leds.set_mode('breath', BREATH_COLOR)
        print(f"[BootBacklight] Blue breath effect started — waiting for WiFi...")

        while True:
            if is_wifi_connected():
                print("[BootBacklight] WiFi connected! Stopping breath effect")
                break
            if check_server_running():
                print("[BootBacklight] Server started! Stopping breath effect")
                break
            time.sleep(2.0)

        leds.set_mode('off', (0, 0, 0))
        leds.shutdown()

    except Exception as e:
        print(f"[BootBacklight] Error: {e}")


if __name__ == "__main__":
    run_breath()
