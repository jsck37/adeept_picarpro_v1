#!/usr/bin/env python3
import os, signal, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Server.logger import logger
from Server.state import SharedState
from Server.network import oled_loop
from config import FLASK_PORT


def main():
    logger.info('=' * 50)
    logger.info('  PiCar Pro v1 — booting...')
    logger.info('=' * 50)

    state = SharedState()

    try:
        state.init_hardware()
    except Exception as e:
        logger.error(f'[Boot] hardware init error: {e}')

    if state.ds4:
        try:
            state.ds4.start(
                motors=state.motors, servos=state.servos, leds=state.leds,
                buzzer=state.buzzer, switches=state.switches,
                speed=state.speed, shared_state=state,
                autonomous=state.autonomous,
            )
        except Exception as e:
            logger.warning(f'[Boot] DS4 start failed: {e}')
        try:
            from Server.routes.bluetooth_routes import load_config, pair_and_connect
            cfg = load_config()
            mac = cfg.get('last_gamepad_mac')
            if mac:
                def _auto():
                    time.sleep(3.0)
                    logger.info(f'[BT] auto-connecting to {mac}...')
                    ok, msg = pair_and_connect(mac, state.ds4)
                    if ok:
                        logger.info(f'[BT] auto-connect OK: {msg}')
                    else:
                        logger.warning(f'[BT] auto-connect failed: {msg}')
                threading.Thread(target=_auto, daemon=True).start()
        except Exception as e:
            logger.warning(f'[Boot] BT auto-connect setup failed: {e}')

    threading.Thread(target=oled_loop, args=(state,), daemon=True).start()

    def _shutdown(signum, frame):
        logger.info(f'[Boot] signal {signum} received')
        state.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    from Server.WebServer import start_server
    start_server(state)


if __name__ == '__main__':
    main()
