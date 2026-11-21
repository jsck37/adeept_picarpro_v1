"""v1.6.14: полноэкранный режим управления в горизонтальной ориентации
(авто-вход при открытии с телефона, в т.ч. по авто-редиректу captive
portal) и снятый лимит наклона камеры."""
import pytest


@pytest.fixture
def app(clean_state, monkeypatch):
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


class TestTiltNoLimit:
    def test_tilt_full_range(self):
        from config import SERVO_LIMITS
        assert SERVO_LIMITS[2] == {'min': 0, 'max': 180}

    def test_hand_tracking_uses_full_range(self, clean_state):
        """Слежение за рукой теперь доводит наклон до полного хода."""
        from config import SERVO_LIMITS
        lo, hi = SERVO_LIMITS[2]['min'], SERVO_LIMITS[2]['max']
        assert (lo, hi) == (0, 180)

    def test_servo_get_limits_reports_full_range(self, clean_state):
        from picarpro.commands import process
        r = process(clean_state, {'cmd': 'servo_get_limits', 'params': {}})
        assert r['ok'] is True
        assert r['limits'][2] == {'min': 0, 'max': 180}


class TestFullscreenLandscape:
    """Управление открывается сразу во весь экран (с телефона, в т.ч.
    после авто-редиректа captive portal) и запрашивает горизонталь."""

    def _get(self, client, path):
        rv = client.get(path)
        assert rv.status_code == 200
        return rv.data.decode('utf-8')

    def test_rotate_hint_in_page(self, client):
        assert 'drive-rotate-hint' in self._get(client, '/')

    def test_app_js_auto_enters_drive_mode(self, client):
        js = self._get(client, '/app.js')
        # авто-вход с тач-устройств (pointer: coarse) и запоминание выхода
        assert "matchMedia('(pointer: coarse)')" in js
        assert "localStorage.getItem('autoDrive')" in js
        assert "localStorage.setItem('autoDrive', '1')" in js
        assert "localStorage.setItem('autoDrive', '0')" in js

    def test_app_js_locks_landscape(self, client):
        js = self._get(client, '/app.js')
        assert "screen.orientation.lock('landscape')" in js
        assert 'screen.orientation.unlock()' in js

    def test_rotate_hint_translated(self, client):
        ru = self._get(client, '/translate/ru.js')
        en = self._get(client, '/translate/en.js')
        assert 'drv_rotate_hint' in ru and 'Поверните телефон' in ru
        assert 'drv_rotate_hint' in en and 'Rotate the phone' in en

    def test_desktop_is_not_forced_fullscreen(self, client):
        """Авто-вход только для тач-устройств: на ПК проверка pointer."""
        js = self._get(client, '/app.js')
        assert 'pointer: coarse' in js
