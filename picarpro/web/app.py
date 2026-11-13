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
import gzip
import hashlib
import os
import threading
import time

from flask import (Flask, Response, jsonify, request,
                   send_from_directory)

from picarpro.logger import logger, log_buffer
from picarpro.commands import process as process_command
from picarpro.network import get_ip, start_redirect_server
from picarpro.web.auth import (SECURITY_HEADERS, request_authorized,
                               is_public_path, token_required,
                               generate_secret_key)
from picarpro.routes.bluetooth import (
    scan_devices, pair_and_connect, disconnect_device,
    load_config as bt_load_config, save_config as bt_save_config,
    _load_hid_sony, validate_mac, BluetoothBusy,
    diagnose as bt_diagnose,
)
from config import (FLASK_PORT, BASE_DIR, CORS_ORIGINS,
                    TELEMETRY_INTERVAL_S, MAX_CONTENT_LENGTH, UPDATE_MAX_BYTES)

WEB_DIR = os.path.join(os.path.dirname(__file__), 'static')
DOCS_DIR = os.path.join(BASE_DIR, 'docs')


def _cors_headers(resp):
    if CORS_ORIGINS:
        origin = request.headers.get('Origin')
        if origin and origin in CORS_ORIGINS:
            resp.headers['Access-Control-Allow-Origin'] = origin
            resp.headers['Vary'] = 'Origin'
    return resp


_STATIC_CACHE = {}    # имя -> (mtime, etag, raw, gzipped|None)
_COMPRESSIBLE = ('text/', 'application/javascript', 'application/json')


def _static_entry(filename, mimetype):
    path = os.path.join(WEB_DIR, filename)
    mtime = os.stat(path).st_mtime
    ent = _STATIC_CACHE.get(filename)
    if ent is None or ent[0] != mtime:
        with open(path, 'rb') as f:
            raw = f.read()
        gz = (gzip.compress(raw, 6)
              if mimetype.startswith(_COMPRESSIBLE) and len(raw) > 512 else None)
        etag = '"%s"' % hashlib.md5(raw).hexdigest()[:16]
        ent = _STATIC_CACHE[filename] = (mtime, etag, raw, gz)
    return ent


def _static(filename, mimetype, max_age=0):
    """Статика из памяти: ETag (304 без тела), gzip для текстовых файлов
    (app.js 50 КБ -> ~14 КБ по Wi-Fi), и ни одного рендера шаблона на запрос.
    Для html/js/css — revalidate (после обновления проекта сразу новая
    версия), для картинок — суточный кэш."""
    try:
        _, etag, raw, gz = _static_entry(filename, mimetype)
    except OSError:
        return Response('Not found', status=404, mimetype='text/plain')
    headers = {'ETag': etag, 'Vary': 'Accept-Encoding',
               'Cache-Control': (f'public, max-age={max_age}' if max_age
                                 else 'no-cache')}
    if request.headers.get('If-None-Match') == etag:
        return Response(status=304, headers=headers)
    body = raw
    if gz is not None and 'gzip' in request.headers.get('Accept-Encoding', ''):
        body = gz
        headers['Content-Encoding'] = 'gzip'
    return Response(body, mimetype=mimetype.split(';')[0], headers=headers,
                    content_type=mimetype)


def create_app(state):
    app = Flask(__name__, template_folder=WEB_DIR, static_folder=None)
    app.config['SECRET_KEY'] = generate_secret_key(
        str(BASE_DIR / 'instance' / 'secret_key'))
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

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
        return _static('index.html', 'text/html; charset=utf-8')

    @app.route('/favicon.ico')
    def favicon():
        return '', 204

    @app.route('/style.css')
    def css():
        return _static('style.css', 'text/css; charset=utf-8')

    @app.route('/app.js')
    def js():
        return _static('app.js', 'application/javascript; charset=utf-8')

    @app.route('/panel-core.js')
    def js_core():
        return _static('panel-core.js', 'application/javascript; charset=utf-8')

    @app.route('/rpi_pinout.png')
    def pinout_img():
        return _static('rpi_pinout.png', 'image/png', max_age=86400)

    # ---- обновление по воздуху ------------------------------------------------
    def _update_guard():
        """Только водитель (или клиент без id); робот при этом останавливается."""
        client = (request.headers.get('X-Client-Id') or '')[:32] or None
        allowed, owner = state.control.check(client, 'update', {})
        if not allowed:
            return jsonify({'ok': False, 'code': 'not_driver', 'driver': owner,
                            'error': 'Controlled by another device'}), 409
        return None

    @app.route('/api/update/info')
    def api_update_info():
        from picarpro import updater
        return jsonify({'ok': True, **updater.info()})

    @app.route('/api/update', methods=['POST'])
    def api_update():
        from picarpro import updater
        denied = _update_guard()
        if denied:
            return denied
        request.max_content_length = UPDATE_MAX_BYTES + 1024 * 1024
        f = request.files.get('file')
        if f is None:
            return jsonify({'ok': False, 'error': 'file required'}), 400
        os.makedirs(os.path.join(str(BASE_DIR), '.update_tmp'), exist_ok=True)
        tmp_path = os.path.join(str(BASE_DIR), '.update_tmp',
                                'upload-%d.bin' % int(time.time()))
        try:
            f.save(tmp_path)
            state.emergency_stop()          # на время замены кода робот стоит
            result = updater.apply_update(tmp_path)
        except updater.UpdateError as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        except Exception as e:
            logger.error(f'[Update] failed: {e}')
            return jsonify({'ok': False, 'error': 'update failed: ' + str(e)[:120]}), 500
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        restart = request.form.get('restart', '1') != '0'
        result['restarting'] = bool(restart and updater.schedule_restart())
        return jsonify(result)

    @app.route('/api/update/rollback', methods=['POST'])
    def api_update_rollback():
        from picarpro import updater
        denied = _update_guard()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        try:
            state.emergency_stop()
            result = updater.rollback(data.get('name') or None)
        except updater.UpdateError as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        result['restarting'] = updater.schedule_restart()
        return jsonify(result)

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

        cam = state.camera
        # держим спрос на кадры всё соединение (v1.2.0): без зрителей
        # камера живёт в экономном idle-ритме CAMERA_IDLE_INTERVAL_S
        cam.stream_open()

        def gen():
            # wait_frame отдаёт только НОВЫЕ кадры: раньше get_frame() сразу
            # возвращал последний кадр, и цикл без пауз гнал дубликаты на
            # максимальной скорости (100 % ядра и впустую трафик)
            last_id = 0
            try:
                while state.running:
                    try:
                        last_id, frame = cam.wait_frame(last_id, timeout=1.0)
                    except Exception:
                        frame = None
                        time.sleep(0.05)
                    if frame:
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                               + frame + b'\r\n')
            finally:
                cam.stream_close()

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
                time.sleep(TELEMETRY_INTERVAL_S)

        return Response(gen(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache',
                                 'X-Accel-Buffering': 'no'})

    @app.route('/api/i2c_scan')
    def api_i2c():
        from picarpro.hardware.mpu6050 import i2c_scan_report
        return jsonify(i2c_scan_report())

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
        # X-Client-Id — то же устройство, что и в WebSocket (арбитраж управления)
        client = (request.headers.get('X-Client-Id') or '')[:32] or None
        r = process_command(state, {'cmd': action, 'params': data}, client)
        if client and isinstance(r, dict):
            r.setdefault('driver', state.control.driver)
        return jsonify(r)

    # ---- bluetooth --------------------------------------------------------
    @app.route('/api/bt/scan')
    def bt_scan():
        try:
            return jsonify({'ok': True, 'devices': scan_devices(scan_time=6)})
        except BluetoothBusy as e:
            return jsonify({'ok': False, 'busy': True, 'error': str(e), 'devices': []})
        except Exception as e:
            logger.error(f'[BT] scan error: {e}')
            return jsonify({'ok': False, 'error': str(e)[:200], 'devices': [],
                            'diagnose': bt_diagnose()})

    @app.route('/api/bt/diagnose')
    def bt_diag():
        return jsonify({'ok': True, **bt_diagnose()})

    def _bt_connect(mac, on_success=None, allow_pairing=True):
        """Подключение в фоновом потоке с ожиданием до 60 с (общий код
        /api/bt/connect и /api/bt/auto_connect)."""
        result = {'ok': False, 'message': ''}
        done = threading.Event()

        def _do():
            try:
                ok, msg = pair_and_connect(mac, state.ds4, allow_pairing=allow_pairing)
                result['ok'] = ok
                result['message'] = msg
                if ok and on_success:
                    on_success()
            except Exception as e:
                result['message'] = str(e)[:200]
            finally:
                done.set()

        threading.Thread(target=_do, daemon=True).start()
        if not done.wait(timeout=60):
            result['message'] = 'Timed out — check the controller and retry'
        return result

    @app.route('/api/bt/connect', methods=['POST'])
    def bt_connect():
        data = request.get_json(silent=True) or {}
        mac = validate_mac(data.get('mac', ''))
        if not mac:
            return jsonify({'ok': False, 'error': 'Valid MAC required'}), 400
        def _remember():
            cfg = bt_load_config()
            cfg['last_gamepad_mac'] = mac
            cfg['last_gamepad_name'] = str(data.get('name', ''))[:64]
            bt_save_config(cfg)

        return jsonify(_bt_connect(mac, on_success=_remember))

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
        # автоподключение: только connect уже сопряжённого — без remove/pair
        return jsonify(_bt_connect(mac, allow_pairing=False))

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
