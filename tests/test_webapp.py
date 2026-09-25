"""Тесты Flask-приложения: роуты, безопасность, авторизация, инъекции."""
import pytest


@pytest.fixture
def app(clean_state, monkeypatch, tmp_path):
    monkeypatch.setenv('PICARPRO_SECRET_KEY', 'test-secret')
    monkeypatch.setattr('config.AUTH_TOKEN', '')
    import picarpro.web.auth as auth_mod
    monkeypatch.setattr(auth_mod, 'AUTH_TOKEN', '')
    from picarpro.web.app import create_app
    application = create_app(clean_state)
    application.config['TESTING'] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


class TestStatic:
    def test_index(self, client):
        r = client.get('/')
        assert r.status_code == 200
        assert b'PiCar' in r.data

    def test_app_js(self, client):
        r = client.get('/app.js')
        assert r.status_code == 200
        assert b'sendCommand' in r.data

    def test_style_css(self, client):
        assert client.get('/style.css').status_code == 200

    def test_docs_json(self, client):
        assert client.get('/docs/index.json').status_code == 200
        assert client.get('/docs/pinout.json').status_code == 200

    def test_docs_path_traversal_blocked(self, client):
        r = client.get('/docs/components/..%2F..%2Fconfig.py')
        assert r.status_code in (400, 404)


class TestSecurityHeaders:
    def test_headers_present(self, client):
        r = client.get('/')
        assert r.headers['X-Content-Type-Options'] == 'nosniff'
        assert r.headers['X-Frame-Options'] == 'DENY'
        assert 'default-src' in r.headers['Content-Security-Policy']

    def test_no_wildcard_cors(self, client):
        r = client.get('/api/status')
        assert 'Access-Control-Allow-Origin' not in r.headers

    def test_whitelisted_cors(self, app, client, monkeypatch):
        monkeypatch.setattr('picarpro.web.app.CORS_ORIGINS',
                            ['https://my-ui.example'])
        r = client.get('/api/status',
                       headers={'Origin': 'https://my-ui.example'})
        assert r.headers['Access-Control-Allow-Origin'] == 'https://my-ui.example'
        r2 = client.get('/api/status',
                        headers={'Origin': 'https://evil.example'})
        assert 'Access-Control-Allow-Origin' not in r2.headers


class TestAPI:
    def test_status_shape(self, client):
        r = client.get('/api/status')
        assert r.status_code == 200
        d = r.get_json()
        for key in ('cpu_temp', 'ram', 'speed', 'hw', 'battery',
                    'servo_angles', 'auto_mode'):
            assert key in d, f'нет {key}'

    def test_logs(self, client):
        assert client.get('/api/logs').status_code == 200

    def test_i2c_scan(self, client):
        r = client.get('/api/i2c_scan')
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

    def test_cmd_move(self, client, clean_state):
        r = client.post('/cmd/move', json={'dir': 'forward'})
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

    def test_cmd_get_json_only(self, client):
        r = client.post('/cmd/speed', data='speed=10',
                        content_type='application/x-www-form-urlencoded')
        # JSON отсутствует -> params={}, команда выполняется без 500
        assert r.status_code == 200

    def test_cmd_bad_body_safe(self, client):
        r = client.post('/cmd/speed', json='not-a-dict')
        assert r.status_code in (200, 400)

    def test_cmd_unknown(self, client):
        r = client.post('/cmd/hack_me', json={})
        assert r.get_json()['ok'] is False

    def test_video_feed_no_camera_503(self, client, clean_state, monkeypatch):
        """Без камеры — 503 вместо вечного пустого MJPEG (фикс)."""
        monkeypatch.setattr(clean_state, 'init_camera',
                            lambda: None)
        clean_state.camera = None
        r = client.get('/video_feed')
        assert r.status_code == 503


class TestAuth:
    @pytest.fixture
    def auth_app(self, clean_state, monkeypatch):
        monkeypatch.setenv('PICARPRO_SECRET_KEY', 'test-secret')
        import picarpro.web.auth as auth_mod
        monkeypatch.setattr(auth_mod, 'AUTH_TOKEN', 'secret-token-123')
        monkeypatch.setattr('picarpro.web.app.token_required',
                            lambda: True)
        from picarpro.web.app import create_app
        application = create_app(clean_state)
        application.config['TESTING'] = True
        return application

    def test_protected_without_token(self, auth_app):
        client = auth_app.test_client()
        r = client.get('/api/status')
        assert r.status_code == 401

    def test_protected_with_header(self, auth_app):
        client = auth_app.test_client()
        r = client.get('/api/status',
                       headers={'X-Auth-Token': 'secret-token-123'})
        assert r.status_code == 200

    def test_protected_with_param(self, auth_app):
        client = auth_app.test_client()
        r = client.get('/api/status?token=secret-token-123')
        assert r.status_code == 200

    def test_wrong_token(self, auth_app):
        client = auth_app.test_client()
        r = client.get('/api/status', headers={'X-Auth-Token': 'wrong'})
        assert r.status_code == 401

    def test_cmd_protected(self, auth_app):
        client = auth_app.test_client()
        r = client.post('/cmd/move', json={'dir': 'forward'})
        assert r.status_code == 401

    def test_static_open(self, auth_app):
        """Статика UI остаётся открытой, чтобы браузер мог загрузить форму."""
        client = auth_app.test_client()
        assert client.get('/').status_code == 200

    def test_auth_check_endpoint(self, auth_app):
        client = auth_app.test_client()
        r = client.get('/api/auth/check').get_json()
        assert r['required'] is True
        assert r['authorized'] is False

    def test_timing_safe_compare(self):
        from picarpro.web.auth import check_token
        import picarpro.web.auth as auth_mod
        orig = auth_mod.AUTH_TOKEN
        try:
            auth_mod.AUTH_TOKEN = 'abc'
            assert check_token('abc') is True
            assert check_token('abd') is False
            assert check_token(None) is False
        finally:
            auth_mod.AUTH_TOKEN = orig


class TestBluetoothInjection:
    def test_mac_injection_rejected(self, client, monkeypatch):
        """newline-injection в bluetoothctl через MAC невозможен."""
        captured = {}

        def fake_pair(mac, ds4=None, timeout=30):
            captured['mac'] = mac
            return False, 'should not be called'

        monkeypatch.setattr('picarpro.routes.bluetooth.pair_and_connect',
                            fake_pair)
        evil = 'AA:BB:CC:DD:EE:FF\nremove *\nquit'
        r = client.post('/api/bt/connect', json={'mac': evil})
        assert r.status_code == 400
        assert 'mac' not in captured

    def test_valid_mac_accepted(self, client, monkeypatch, tmp_path):
        called = []
        # изолируем файл конфигурации, чтобы тест не трогал проект
        monkeypatch.setattr('picarpro.routes.bluetooth.BT_CONFIG_FILE',
                            tmp_path / 'bt.json')

        monkeypatch.setattr('picarpro.web.app.pair_and_connect',
                            lambda mac, ds4=None: called.append(mac)
                            or (True, 'ok'))
        r = client.post('/api/bt/connect',
                        json={'mac': 'AA:BB:CC:DD:EE:FF'})
        assert r.status_code == 200
        assert called == ['AA:BB:CC:DD:EE:FF']

    def test_lowercase_mac_normalized(self, client, monkeypatch, tmp_path):
        called = []
        monkeypatch.setattr('picarpro.routes.bluetooth.BT_CONFIG_FILE',
                            tmp_path / 'bt.json')
        monkeypatch.setattr('picarpro.web.app.pair_and_connect',
                            lambda mac, ds4=None: called.append(mac)
                            or (True, 'ok'))
        client.post('/api/bt/connect', json={'mac': 'aa:bb:cc:dd:ee:ff'})
        assert called == ['AA:BB:CC:DD:EE:FF']
