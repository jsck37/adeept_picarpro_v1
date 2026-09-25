"""Flask-приложение — роуты переписаны с нуля (режим «Обновить»).

Исправления относительно исходного WebServer.py / app.py:
* CORS '*' убран; если PICARPRO_CORS задан — отдаётся белый список источников;
* опциональная токен-аутентификация (picarpro.web.auth);
* заголовки безопасности (CSP, X-Frame-Options, nosniff);
* /video_feed возвращает 503, если камера недоступна, вместо вечного
  пустого MJPEG-потока;
* безопасные ответы об ошибках вместо 500 с трейсбеком;
* MAX_CONTENT_LENGTH ограничивает размер запроса.
"""
import json
import os
import threading
import time

from flask import (Flask, Response, jsonify, request,
                   send_from_directory, render_template)

from picarpro.logger import logger, log_buffer
from picarpro.commands import process as process_command
from picarpro.network import get_ip, start_redirect_server
from picarpro.web.auth import (SECURITY_HEADERS, request_authorized,
                               is_public_path, token_required,
                               generate_secret_key, extract_request_token)
from picarpro.routes.bluetooth import (
    scan_devices, pair_and_connect, disconnect_device,
    load_config as bt_load_config, save_config as bt_save_config,
    _load_hid_sony, validate_mac,
)
from config import FLASK_PORT, WEBSOCKET_PORT, BASE_DIR, CORS_ORIGINS

WEB_DIR = os.path.join(os.path.dirname(__file__), 'static')
DOCS_DIR = os.path.join(BASE_DIR, 'docs')


def _cors_headers(resp):
    if CORS_ORIGINS:
        origin = request.headers.get('Origin')
        if origin and origin in CORS_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Vary'] = 'Origin'
    return resp


def create_app(state):
    app = Flask(__name__, template_folder=WEB_DIR, static_folder=None)
    app.config['SECRET_KEY'] = generate_secret_key(
        str(BASE_DIR / 'instance' / 'secret_key'))
    app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

    @app.after_request
    def _security(resp):
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return _cors_headers(resp)

    # ---- auth guard -------------------------------------------------------
    @app.before_request
    def _auth_guard():
        if not token_required() or is_public_path(request.path):
            return None
        # video_feed разрешаем по ?token= (для <img> тега)
        if not request_authorized():
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 401
        return None

    # ---- static UI --------------------------------------------------------
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
        return send_from_directory(WEB_DIR, 'app.js',
                                   mimetype='application/javascript')

    @app.route('/rpi_pinout.png')
    def pinout_img():
        return send_from_directory(WEB_DIR, 'rpi_pinout.png',
                                   mimetype='image/png')

    @app.route('/docs/index.json')
    def docs_index():
        return send_from_directory(DOCS_DIR, 'index.json',
                                   mimetype='application/json')

    @app.route('/docs/pinout.json')
    def docs_pinout():
        return send_from_directory(DOCS_DIR, 'pinout.json',
                                   mimetype='application/json')

    @app.route('/docs/components/<path:fn>')
    def docs_comp(fn):
        if '..' in fn or fn.startswith('/'):
            return jsonify({'ok': False, 'error': 'Bad path'}), 400
        return send_from_directory(os.path.join(DOCS_DIR, 'components'), fn,
                                   mimetype='application/json')

    # ---- auth API ---------------------------------------------------------
    @app.route('/api/auth/check')
    def auth_check():
        return jsonify({'ok': True, 'required': token_required(),
                        'authorized': request_authorized()})

    # ---- video ------------------------------------------------------------
    @app.route('/video_feed')
    def video_feed():
        state.init_camera()
        if not state.camera:
            return Response('Camera not available', status=503,
                            mimetype='text/plain')

        def gen():
            while state.running:
                try:
                    frame = state.camera.get_frame()
                except Exception:
                    frame = None
                if frame:
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                           + frame + b'\r\n')
                else:
                    time.sleep(0.05)

        return Response(gen(),
                        mimetype='multipart/x-mixed-replace; boundary=frame')

    # ---- status -----------------------------------------------------------
    @app.route('/api/status')
    def api_status():
        return jsonify(state.get_status())

    @app.route('/api/status/stream')
    def api_sse():
        def gen():
            while state.running:
                try:
                    yield f'data: {json.dumps(state.get_status())}\n\n'
                except Exception:
                    break
                time.sleep(1.0)

        return Response(gen(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache',
                                 'X-Accel-Buffering': 'no'})

    @app.route('/api/i2c_scan')
    def api_i2c():
        from picarpro.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
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

    # ---- commands ---------------------------------------------------------
    @app.route('/cmd/<action>', methods=['POST'])
    def cmd_route(action):
        data = request.get_json(silent=True)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return jsonify({'ok': False, 'error': 'JSON object expected'}), 400
        r = process_command(state, {'cmd': action, 'params': data})
        return jsonify(r)

    # ---- bluetooth --------------------------------------------------------
    def _is_gamepad(name):
        n = (name or '').lower()
        return any(k in n for k in (
            'wireless controller', 'dualshock', 'ds4', 'ds5', 'dualsense',
            'xbox', 'gamepad', '8bitdo', 'pro controller', 'joy-con',
            'sony interactive', 'playstation', 'nintendo',
        ))

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
        mac = validate_mac(data.get('mac', ''))
        if not mac:
            return jsonify({'ok': False, 'error': 'Valid MAC required'}), 400
        result = {'ok': False, 'message': ''}
        done = threading.Event()

        def _do():
            ok, msg = pair_and_connect(mac, state.ds4)
            result['ok'] = ok
            result['message'] = msg
            if ok:
                cfg = bt_load_config()
                cfg['last_gamepad_mac'] = mac
                cfg['last_gamepad_name'] = str(data.get('name', ''))[:64]
                bt_save_config(cfg)
            done.set()

        threading.Thread(target=_do, daemon=True).start()
        done.wait(timeout=45)
        return jsonify(result)

    @app.route('/api/bt/disconnect', methods=['POST'])
    def bt_disconnect():
        data = request.get_json(silent=True) or {}
        mac = validate_mac(data.get('mac', ''))
        cfg = bt_load_config()
        if not mac and cfg.get('last_gamepad_mac'):
            mac = validate_mac(cfg['last_gamepad_mac'])
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


def start_server(state):
    """Запуск Flask (фоном) + порт 80 -> 5000 + блокирующий WebSocket."""
    from picarpro.web.websocket import run_websocket_server
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
    try:
        run_websocket_server(state)
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown()
