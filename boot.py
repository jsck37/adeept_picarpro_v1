#!/usr/bin/env python3

import os, signal, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    FLASK_PORT, WEBSOCKET_PORT, HOTSPOT_SSID, HOTSPOT_IP,
    OLED_SCROLL_TEXT, SERVO_COUNT,
)
from Server.logger import logger
from Server.state import SharedState, load_servo_cal
from Server.network import get_ip, start_redirect_server, oled_loop


BREATH_COLOR = (0, 100, 255)


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


def wait_for_wifi(oled, leds):
    if oled and oled._initialized:
        oled.set_lines([f"AP: {HOTSPOT_SSID}", f"{HOTSPOT_IP}:{FLASK_PORT}", "Waiting WiFi...", ""])

    if leds and leds._initialized:
        leds.set_mode('breath', BREATH_COLOR)

    logger.info(f"[Boot] Waiting for WiFi... AP: {HOTSPOT_SSID}")

    while not is_wifi_connected():
        time.sleep(2.0)

    logger.info("[Boot] WiFi connected!")

    if leds and leds._initialized:
        leds.set_mode('off', (0, 0, 0))

    if oled and oled._initialized:
        oled.set_scroll_text(OLED_SCROLL_TEXT)
        ip = get_ip()
        oled.set_lines([f"{ip}:{FLASK_PORT}", "WiFi connected!", "", ""])


def init_hardware_sim():
    from Server.hardware.sim_hardware import (
        SimServoController, SimMotorController, SimLEDController,
        SimOLEDDisplay, SimBuzzerController, SimSwitchController,
        SimUltrasonicSensor, SimMPU6050Controller, SimDS4Controller,
    )

    state = SharedState()
    state.oled = SimOLEDDisplay()
    state.leds = SimLEDController()

    logger.info("[Boot] SIM MODE — skipping WiFi wait")
    logger.info("[Boot] Initializing simulated hardware...")

    state.motors = SimMotorController()
    state.servos = SimServoController()
    state.switches = SimSwitchController()
    state.buzzer = SimBuzzerController()
    state.ultrasonic = SimUltrasonicSensor()
    state.mpu6050 = SimMPU6050Controller()
    state.ds4 = SimDS4Controller()

    cal = load_servo_cal()
    for i, a in enumerate(cal):
        if 0 <= i < SERVO_COUNT and state.servos:
            state.servos.set_init_angle(i, a)
    if state.servos:
        try:
            state.servos.move_init()
        except Exception:
            pass

    state.autonomous = _try_init('Autonomous', lambda: __import__('Server.functions.autonomous', fromlist=['AutonomousController']).AutonomousController(state.motors, state.servos, state.ultrasonic))
    state.voice = None

    logger.info("[Boot] Simulated hardware initialization complete")
    return state


def init_hardware():
    state = SharedState()

    state.oled = _try_init('OLED', lambda: __import__('Server.hardware.oled', fromlist=['OLEDDisplay']).OLEDDisplay())

    leds = _try_init('LED', lambda: __import__('Server.hardware.leds_ws2812', fromlist=['LEDController']).LEDController())
    state.leds = leds

    wait_for_wifi(state.oled, leds)

    logger.info("[Boot] Initializing hardware...")

    state.motors = _try_init('Motors', lambda: __import__('Server.hardware.motors', fromlist=['MotorController']).MotorController())
    state.servos = _try_init('Servos', lambda: __import__('Server.hardware.servos', fromlist=['ServoController']).ServoController())
    state.switches = _try_init('Switches', lambda: __import__('Server.hardware.switch', fromlist=['SwitchController']).SwitchController())
    state.buzzer = _try_init('Buzzer', lambda: __import__('Server.hardware.buzzer', fromlist=['BuzzerController']).BuzzerController())
    state.ultrasonic = _try_init('Ultrasonic', lambda: __import__('Server.hardware.ultrasonic', fromlist=['UltrasonicSensor']).UltrasonicSensor())
    state.mpu6050 = _try_init('MPU6050', lambda: __import__('Server.hardware.mpu6050', fromlist=['MPU6050Controller']).MPU6050Controller())

    try:
        from Server.hardware.ds4 import DS4Controller
        state.ds4 = DS4Controller()
        logger.info("[Boot] DS4 controller initialized")
    except Exception as e:
        logger.warning(f"[Boot] DS4 init error: {e}")
        state.ds4 = None

    cal = load_servo_cal()
    for i, a in enumerate(cal):
        if 0 <= i < SERVO_COUNT and state.servos:
            state.servos.set_init_angle(i, a)
    if state.servos:
        try:
            state.servos.move_init()
        except Exception:
            pass

    state.autonomous = _try_init('Autonomous', lambda: __import__('Server.functions.autonomous', fromlist=['AutonomousController']).AutonomousController(state.motors, state.servos, state.ultrasonic))

    try:
        from Server.functions.voice_command import VoiceCommandController
        state.voice = VoiceCommandController(state.servos, state.motors)
    except Exception:
        state.voice = None

    logger.info("[Boot] Hardware initialization complete")
    return state


def _try_init(name, factory):
    try:
        obj = factory()
        return obj
    except Exception as e:
        logger.warning(f"[Boot] {name} init failed: {e}")
        return None


def main():
    from config import SIM_MODE

    logger.info("=" * 50)
    if SIM_MODE:
        logger.info("  PiCar Pro v1 — Boot Sequence [SIM MODE]")
    else:
        logger.info("  PiCar Pro v1 — Boot Sequence")
    logger.info("=" * 50)

    if SIM_MODE:
        state = init_hardware_sim()
    else:
        state = init_hardware()

    ip = get_ip()
    if state.oled and state.oled._initialized:
        label = "SIM" if SIM_MODE else ""
        state.oled.set_lines([f"{ip}:{FLASK_PORT} {label}", "Starting server...", "", ""])
    threading.Thread(target=oled_loop, args=(state,), daemon=True).start()

    signal.signal(signal.SIGINT, lambda s, f: (state.shutdown_hardware(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (state.shutdown_hardware(), sys.exit(0)))

    from Server.WebServer import start_server
    start_server(state)


if __name__ == "__main__":
    main()
