#!/usr/bin/env python3

import asyncio, os, signal, sys, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Server.logger import logger

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

from config import FLASK_PORT, WEBSOCKET_PORT, HOTSPOT_IP
from Server.network import get_ip, start_redirect_server
from Server.ws_handler import ws_handler, status_broadcast


def start_server(state):
    if not HAS_WS:
        logger.error("[WebServer] websockets not installed!")
        sys.exit(1)

    start_ds4(state)

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

    ip = get_ip()
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


def start_ds4(state):
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


if __name__ == "__main__":
    from boot import main
    main()
