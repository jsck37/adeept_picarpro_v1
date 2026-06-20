import asyncio, json, threading, time, os
import websockets
from flask import Flask, Response, jsonify, request, send_from_directory, render_template
from Server.logger import logger, log_buffer
from Server.commands import process as process_command
from Server.utils.system_info import SystemInfo
from Server.network import get_ip, start_redirect_server
from config import FLASK_PORT, WEBSOCKET_PORT, SECRET_KEY

WEB_DIR = os.path.join(os.path.dirname(__file__), 'dist')
DOCS_DIR = os.path.join(os.path.dirname(__file__), '..', 'docs')


def create_app(state):
    app = Flask(__name__, template_folder=WEB_DIR, static_folder=None)
    app.config['SECRET_KEY'] = SECRET_KEY

    @app.after_request
    def cors(r):
        r.headers['Access-Control-Allow-Origin'] = '*'
        return r

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/favicon.ico')
    def favicon():
        return '', 204

    @app.route('/style.css')
    def css():
        return send_from_directory(WEB_DIR, 'style.css', mimetype='text/css')

    @app.route('/app.js')
    def js():
        return send_from_directory(WEB_DIR, 'app.js', mimetype='application/javascript')

    @app.route('/rpi_pinout.png')
    def pinout_img():
        return send_from_directory(WEB_DIR, 'rpi_pinout.png', mimetype='image/png')

    @app.route('/docs/index.json')
    def docs_index():
        return send_from_directory(DOCS_DIR, 'index.json', mimetype='application/json')

    @app.route('/docs/pinout.json')
    def docs_pinout():
        return send_from_directory(DOCS_DIR, 'pinout.json', mimetype='application/json')

    @app.route('/docs/components/<path:fn>')
    def docs_comp(fn):
        return send_from_directory(os.path.join(DOCS_DIR, 'components'), fn,
                                   mimetype='application/json')

    @app.route('/video_feed')
    def video_feed():
        state.init_camera()
        def gen():
            while state.running:
                frame = state.camera.get_frame() if state.camera else None
                if frame:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                else:
                    time.sleep(0.05)
        return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route('/api/status')
    def api_status():
        return jsonify(state.get_status())

    @app.route('/api/status/stream')
    def api_sse():
        def gen():
            while state.running:
                yield f'data: {json.dumps(state.get_status())}\n\n'
                time.sleep(1.0)
        return Response(gen(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.route('/api/i2c_scan')
    def api_i2c():
        from Server.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
        devs = i2c_scan()
        addr, who = find_mpu6050_on_bus()
        return jsonify({
            'ok': True,
            'devices': [f'0x{a:02X}' for a in devs],
            'mpu6050_found': addr is not None,
            'mpu6050_addr': f'0x{addr:02X}' if addr else None,
            'mpu6050_who_am_i': f'0x{who:02X}' if who else None,
        })

    @app.route('/api/logs')
    def api_logs():
        lines = log_buffer.get_lines(last_n=200)
        return jsonify({'ok': True, 'lines': [[ts, txt] for ts, txt in lines]})

    @app.route('/cmd/<action>', methods=['POST'])
    def cmd_route(action):
        data = request.get_json(silent=True) or {}
        r = process_command(state, {'cmd': action, 'params': data})
        return jsonify(r)

    from Server.routes.bluetooth_routes import (
        scan_devices, pair_and_connect, disconnect_device,
        load_config as bt_load_config, save_config as bt_save_config,
        _load_hid_sony,
    )

    @app.route('/api/bt/scan')
    def bt_scan():
        try:
            devices = scan_devices(scan_time=6)
            for d in devices:
                d['is_gamepad'] = _is_gamepad(d['name'])
            devices.sort(key=lambda d: (0 if d['is_gamepad'] else 1, d['name']))
            return jsonify({'ok': True, 'devices': devices})
        except Exception as e:
            logger.error(f'[BT] scan error: {e}')
            return jsonify({'ok': False, 'error': str(e), 'devices': []})

    @app.route('/api/bt/connect', methods=['POST'])
    def bt_connect():
        data = request.get_json(silent=True) or {}
        mac = data.get('mac', '').strip()
        if not mac:
            return jsonify({'ok': False, 'error': 'MAC required'})
        result = {'ok': False, 'message': ''}
        done = threading.Event()
        def _do():
            ok, msg = pair_and_connect(mac, state.ds4)
            result['ok'] = ok
            result['message'] = msg
            if ok:
                cfg = bt_load_config()
                cfg['last_gamepad_mac'] = mac.upper()
                cfg['last_gamepad_name'] = data.get('name', '')
                bt_save_config(cfg)
            done.set()
        threading.Thread(target=_do, daemon=True).start()
        done.wait(timeout=45)
        return jsonify(result)

    @app.route('/api/bt/disconnect', methods=['POST'])
    def bt_disconnect():
        data = request.get_json(silent=True) or {}
        mac = data.get('mac', '').strip()
        cfg = bt_load_config()
        if not mac and cfg.get('last_gamepad_mac'):
            mac = cfg['last_gamepad_mac']
        if mac:
            disconnect_device(mac)
        cfg.pop('last_gamepad_mac', None)
        cfg.pop('last_gamepad_name', None)
        bt_save_config(cfg)
        return jsonify({'ok': True})

    @app.route('/api/bt/status')
    def bt_status():
        cfg = bt_load_config()
        return jsonify({
            'ok': True,
            'connected': state.ds4.connected if state.ds4 else False,
            'saved_mac': cfg.get('last_gamepad_mac'),
            'saved_name': cfg.get('last_gamepad_name'),
        })

    @app.route('/api/bt/auto_connect', methods=['POST'])
    def bt_auto():
        cfg = bt_load_config()
        mac = cfg.get('last_gamepad_mac')
        if not mac:
            return jsonify({'ok': False, 'error': 'No saved gamepad MAC'})
        result = {'ok': False, 'message': ''}
        done = threading.Event()
        def _do():
            ok, msg = pair_and_connect(mac, state.ds4)
            result['ok'] = ok
            result['message'] = msg
            done.set()
        threading.Thread(target=_do, daemon=True).start()
        done.wait(timeout=45)
        return jsonify(result)

    @app.route('/api/bt/load_hid_sony', methods=['POST'])
    def bt_load_hid():
        return jsonify({'ok': _load_hid_sony()})

    return app


def _is_gamepad(name):
    n = name.lower()
    return any(k in n for k in (
        'wireless controller', 'dualshock', 'ds4', 'ds5', 'dualsense',
        'xbox', 'gamepad', '8bitdo', 'pro controller', 'joy-con',
        'sony interactive', 'playstation', 'nintendo',
    ))


async def ws_handler(state, ws, path=None):
    state.ws_clients.add(ws)
    try:
        await ws.send(json.dumps({'type': 'status', 'data': state.get_status()}))
        recent = log_buffer.get_lines(last_n=100)
        if recent:
            await ws.send(json.dumps({
                'type': 'log_history',
                'lines': [[ts, txt] for ts, txt in recent],
            }))
    except Exception:
        pass
    log_queue = asyncio.Queue()
    def on_log(ts, text):
        try:
            log_queue.put_nowait((ts, text))
        except Exception:
            pass
    log_buffer.subscribe(on_log)
    log_task = asyncio.create_task(_forward_logs(ws, log_queue, state))
    try:
        async for msg in ws:
            try:
                r = process_command(state, json.loads(msg))
                await ws.send(json.dumps({'type': 'response', 'data': r}))
            except json.JSONDecodeError:
                await ws.send(json.dumps({'type': 'response',
                                          'data': {'ok': False, 'error': 'Invalid JSON'}}))
            except Exception as e:
                await ws.send(json.dumps({'type': 'response',
                                          'data': {'ok': False, 'error': str(e)}}))
    except Exception:
        pass
    finally:
        log_task.cancel()
        log_buffer.unsubscribe(on_log)
        state.ws_clients.discard(ws)


async def _forward_logs(ws, queue, state):
    try:
        while state.running:
            try:
                ts, text = await asyncio.wait_for(queue.get(), timeout=0.5)
                await ws.send(json.dumps({'type': 'log', 'text': text, 'ts': ts}))
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
    except Exception:
        pass


async def status_broadcast(state):
    while state.running:
        if state.ws_clients:
            try:
                msg = json.dumps({'type': 'status', 'data': state.get_status()})
                gone = set()
                for ws in list(state.ws_clients):
                    try:
                        await ws.send(msg)
                    except Exception:
                        gone.add(ws)
                state.ws_clients -= gone
            except Exception:
                pass
        await asyncio.sleep(1.0)


def start_server(state):
    app = create_app(state)
    threading.Thread(
        target=lambda: app.run(
            host='0.0.0.0', port=FLASK_PORT, threaded=True,
            debug=False, use_reloader=False,
        ), daemon=True,
    ).start()
    logger.info(f'[Web] Flask on :{FLASK_PORT}')
    start_redirect_server(port=80, target_port=FLASK_PORT)
    ip = get_ip()
    logger.info(f'[Web] http://{ip}:{FLASK_PORT}')

    async def _ws_main():
        async with websockets.serve(
            lambda ws, path=None: ws_handler(state, ws, path),
            '0.0.0.0', WEBSOCKET_PORT,
        ):
            logger.info(f'[Web] WebSocket on :{WEBSOCKET_PORT}')
            await status_broadcast(state)

    try:
        asyncio.run(_ws_main())
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown()
