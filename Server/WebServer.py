#!/usr/bin/env python3

import asyncio, os, signal, sys, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Server.logger import logger

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

from config import (
    FLASK_PORT, WEBSOCKET_PORT, DEFAULT_SPEED,
    SERVO_COUNT, SERVO_INIT_ANGLE,
    DS4_ENABLED, HOTSPOT_IP, CAMERA_FPS,
)
from Server.hardware.motors import MotorController
from Server.hardware.servos import ServoController
from Server.hardware.leds_ws2812 import LEDController
from Server.hardware.ultrasonic import UltrasonicSensor
from Server.hardware.switch import SwitchController
from Server.hardware.oled import OLEDDisplay
from Server.hardware.buzzer import BuzzerController
from Server.hardware.mpu6050 import MPU6050Controller
from Server.functions.autonomous import AutonomousController
from Server.state import SharedState, load_servo_cal
from Server.network import get_ip, start_redirect_server, oled_loop
from Server.ws_handler import ws_handler, status_broadcast


def main():
    state = SharedState()

    logger.info("=" * 50)
    logger.info("  PiCar Pro v2 (Flask + WebSocket)")
    logger.info("=" * 50)

    if not HAS_WS:
        logger.error("[WebServer] websockets not installed!")
        sys.exit(1)

    logger.info("[WebServer] Initialising hardware...")

    state.motors = MotorController()
    state.servos = ServoController()
    state.leds = LEDController()
    state.switches = SwitchController()
    state.oled = OLEDDisplay()
    state.buzzer = BuzzerController()

    try:
        state.mpu6050 = MPU6050Controller()
    except Exception as e:
        logger.error(f"[MPU6050] Error: {e}")

    state.ultrasonic = UltrasonicSensor()

    if DS4_ENABLED:
        try:
            from Server.hardware.ds4 import DS4Controller
            state.ds4 = DS4Controller()
        except Exception as e:
            logger.error(f"[DS4] Init error: {e}")

    state.autonomous = AutonomousController(state.motors, state.servos, state.ultrasonic)

    try:
        from Server.functions.voice_command import VoiceCommandController
        state.voice = VoiceCommandController(state.servos, state.motors)
    except Exception:
        pass

    cal = load_servo_cal()
    for i, a in enumerate(cal):
        if 0 <= i < SERVO_COUNT:
            state.servos.set_init_angle(i, a)
    try:
        state.servos.move_init()
    except Exception:
        pass

    ip = get_ip()
    if state.oled:
        state.oled.set_lines([f"{ip}:{FLASK_PORT}", "Starting...", "", ""])
    threading.Thread(target=oled_loop, args=(state,), daemon=True).start()

    from Server.app import create_app
    app = create_app(state)
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0", port=FLASK_PORT, threaded=True,
            debug=False, use_reloader=False,
        ),
        daemon=True,
    ).start()
    logger.info(f"[WebServer] Flask on :{FLASK_PORT}")

    start_redirect_server(port=80, target_port=FLASK_PORT)

    signal.signal(signal.SIGINT, lambda s, f: (state.shutdown_hardware(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (state.shutdown_hardware(), sys.exit(0)))

    if state.ds4:
        state.ds4.start(
            motors=state.motors, servos=state.servos, leds=state.leds,
            buzzer=state.buzzer, switches=state.switches,
            speed=state.speed, shared_state=state,
            autonomous=state.autonomous,
        )
        try:
            from Server.routes.bluetooth_routes import auto_connect_on_boot
            auto_connect_on_boot(state.ds4)
        except Exception as e:
            logger.warning(f"[WebServer] BT auto-connect setup failed: {e}")

    logger.info("-" * 50)
    logger.info(f"  Web UI:  http://{ip}:{FLASK_PORT}")
    logger.info(f"  Hotspot: {HOTSPOT_IP}:{FLASK_PORT}")
    logger.info("-" * 50)

    async def run():
        async with websockets.serve(lambda ws, path=None: ws_handler(state, ws, path), "0.0.0.0", WEBSOCKET_PORT):
            logger.info(f"[WebServer] Ready! http://{ip}:{FLASK_PORT}")
            await status_broadcast(state)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown_hardware()


if __name__ == "__main__":
    main()
