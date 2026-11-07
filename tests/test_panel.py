"""Веб-панель v1.5.1: арбитраж управления, аварийный стоп, дельта-статус, статика."""
import asyncio
import gzip
import json
import os
import shutil
import subprocess
import time

import pytest

from tests.test_websocket import FakeWS, run


# ---------------------------------------------------------------------------
# ControlArbiter
# ---------------------------------------------------------------------------
class TestArbiter:
    def _arb(self, idle=20.0):
        from picarpro.control import ControlArbiter
        return ControlArbiter(idle_s=idle, enabled=True)

    def test_first_controller_becomes_driver_other_rejected(self):
        a = self._arb()
        assert a.check('phone', 'move', {'dir': 'forward'}) == (True, 'phone')
        ok, owner = a.check('pc', 'move', {'dir': 'left'})
        assert ok is False and owner == 'phone'

    def test_stop_and_reads_always_allowed(self):
        a = self._arb()
        a.check('phone', 'move', {'dir': 'forward'})
        assert a.check('pc', 'move', {'dir': 'stop'})[0] is True      # стоп — всем
        assert a.check('pc', 'estop')[0] is True
        assert a.check('pc', 'get_log')[0] is True
        assert a.check('pc', 'battery')[0] is True
        assert a.driver == 'phone'                                     # стоп не отнимает управление

    def test_take_and_release(self):
        a = self._arb()
        a.check('phone', 'speed', {'value': 10})
        a.take('pc')
        assert a.driver == 'pc'
        assert a.check('phone', 'speed', {'value': 20})[0] is False
        assert a.release('pc') is True
        assert a.driver is None
        assert a.check('phone', 'speed', {'value': 20})[0] is True

    def test_idle_driver_loses_control(self):
        a = self._arb(idle=0.05)
        a.check('phone', 'speed', {})
        time.sleep(0.1)
        assert a.driver is None
        assert a.check('pc', 'speed', {})[0] is True

    def test_anonymous_client_unrestricted(self):
        a = self._arb()
        a.check('phone', 'speed', {})
        assert a.check(None, 'move', {'dir': 'forward'})[0] is True

    def test_disabled(self):
        from picarpro.control import ControlArbiter
        a = ControlArbiter(enabled=False)
        a.check('phone', 'speed', {})
        assert a.check('pc', 'speed', {})[0] is True


# ---------------------------------------------------------------------------
# commands.process: арбитраж, estop, вытеснение автономии
# ---------------------------------------------------------------------------
class TestCommandsMultiDevice:
    def test_second_device_rejected_with_code(self, clean_state):
        from picarpro.commands import process
        assert process(clean_state, {'cmd': 'speed', 'params': {'value': 30}}, 'phone')['ok']
        r = process(clean_state, {'cmd': 'speed', 'params': {'value': 60}}, 'pc')
        assert r['ok'] is False and r['code'] == 'not_driver' and r['driver'] == 'phone'
        assert clean_state.speed == 30                      # команда НЕ выполнена

    def test_stop_from_non_driver_stops_motors(self, clean_state):
        from picarpro.commands import process
        process(clean_state, {'cmd': 'move', 'params': {'dir': 'forward'}}, 'phone')
        assert clean_state.motors.is_moving
        r = process(clean_state, {'cmd': 'move', 'params': {'dir': 'stop'}}, 'pc')
        assert r['ok'] and not clean_state.motors.is_moving

    def test_take_control_command(self, clean_state):
        from picarpro.commands import process
        process(clean_state, {'cmd': 'speed', 'params': {'value': 30}}, 'phone')
        r = process(clean_state, {'cmd': 'take_control', 'params': {}}, 'pc')
        assert r['ok'] and r['driver'] == 'pc'
        assert process(clean_state, {'cmd': 'speed', 'params': {'value': 70}}, 'pc')['ok']
        assert clean_state.speed == 70

    def test_internal_calls_without_client_unaffected(self, clean_state):
        from picarpro.commands import process
        process(clean_state, {'cmd': 'speed', 'params': {'value': 30}}, 'phone')
        assert process(clean_state, {'cmd': 'speed', 'params': {'value': 40}})['ok']

    def test_estop_stops_motors_and_autonomy(self, clean_state):
        from picarpro.commands import process
        clean_state.ultrasonic._distance = 100.0
        clean_state.autonomous.start('keepDistance')
        time.sleep(0.2)
        process(clean_state, {'cmd': 'speed', 'params': {'value': 30}}, 'phone')
        r = process(clean_state, {'cmd': 'estop', 'params': {}}, 'pc')   # не водитель — можно
        assert r['ok']
        deadline = time.monotonic() + 2
        while clean_state.autonomous.is_active() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not clean_state.autonomous.is_active()
        assert not clean_state.motors.is_moving

    def test_manual_move_interrupts_autonomous(self, clean_state):
        from picarpro.commands import process
        clean_state.ultrasonic._distance = 100.0
        clean_state.autonomous.start('keepDistance')
        time.sleep(0.2)
        r = process(clean_state, {'cmd': 'move', 'params': {'dir': 'forward'}})
        assert r['ok'] and r.get('interrupted_auto') is True
        assert not clean_state.autonomous.is_active()
        clean_state.motors.stop()


# ---------------------------------------------------------------------------
# WebSocket: hello/view, арбитраж, дельта
# ---------------------------------------------------------------------------
class TestWebsocketPanel:
    def _run(self, state, monkeypatch, msgs):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'token_required', lambda: False)
        ws = FakeWS(incoming=[json.dumps(m) for m in msgs])
        run(ws_mod.ws_handler(state, ws))
        return ws

    def test_hello_sets_id_and_driver_is_that_id(self, clean_state, monkeypatch):
        ws = self._run(clean_state, monkeypatch, [
            {'cmd': 'hello', 'params': {'id': 'phone1'}},
            {'cmd': 'speed', 'params': {'value': 33}},
        ])
        resp = [m['data'] for m in ws.sent if m['type'] == 'response']
        assert resp[0]['id'] == 'phone1'
        assert resp[1]['ok'] and resp[1]['driver'] == 'phone1'

    def test_driver_released_on_disconnect(self, clean_state, monkeypatch):
        self._run(clean_state, monkeypatch, [
            {'cmd': 'hello', 'params': {'id': 'phone1'}},
            {'cmd': 'speed', 'params': {'value': 33}},
        ])
        assert clean_state.control.driver is None       # ушёл — управление свободно

    def test_non_driver_ws_rejected(self, clean_state, monkeypatch):
        clean_state.control.take('someone')
        ws = self._run(clean_state, monkeypatch, [
            {'cmd': 'hello', 'params': {'id': 'me'}},
            {'cmd': 'speed', 'params': {'value': 80}},
        ])
        last = [m['data'] for m in ws.sent if m['type'] == 'response'][-1]
        assert last['code'] == 'not_driver'
        assert clean_state.speed != 80

    def test_view_command(self, clean_state, monkeypatch):
        ws = self._run(clean_state, monkeypatch, [
            {'cmd': 'view', 'params': {'visible': False}}])
        assert [m['data'] for m in ws.sent if m['type'] == 'response'][0]['ok']


class TestStatusDelta:
    def test_first_full_then_only_changes(self):
        from picarpro.web.websocket import status_delta
        meta = {'last': None, 'full_ts': 0.0}
        st = {'a': 1, 'b': {'x': 1}, 'c': 'same'}
        data, is_delta = status_delta(meta, st, 100.0)
        assert data == st and not is_delta
        assert status_delta(meta, dict(st), 101.0) == (None, False)    # ничего не менялось
        st2 = {'a': 2, 'b': {'x': 1}, 'c': 'same'}
        data, is_delta = status_delta(meta, st2, 102.0)
        assert data == {'a': 2} and is_delta
        st3 = {'a': 2, 'b': {'x': 2}, 'c': 'same', 'new': 1}
        data, _ = status_delta(meta, st3, 103.0)
        assert data == {'b': {'x': 2}, 'new': 1}

    def test_periodic_full_resync(self):
        from picarpro.web.websocket import status_delta
        from config import WS_STATUS_FULL_EVERY_S
        meta = {'last': None, 'full_ts': 0.0}
        st = {'a': 1}
        status_delta(meta, st, 100.0)
        data, is_delta = status_delta(meta, dict(st), 100.0 + WS_STATUS_FULL_EVERY_S + 1)
        assert data == st and not is_delta

    def test_client_without_meta_gets_full(self):
        from picarpro.web.websocket import status_delta
        assert status_delta(None, {'a': 1}, 1.0) == ({'a': 1}, False)

    def test_broadcast_skips_hidden_and_unchanged(self, clean_state, monkeypatch):
        import picarpro.web.websocket as ws_mod
        monkeypatch.setattr(ws_mod, 'TELEMETRY_INTERVAL_S', 0.05)
        visible, hidden = FakeWS(), FakeWS()
        for w, vis in ((visible, True), (hidden, False)):
            clean_state.ws_clients.add(w)
            clean_state.ws_meta[w] = {'id': str(id(w)), 'visible': vis, 'last': None,
                                      'full_ts': 0.0, 'sent_ts': 0.0}
        clean_state.get_status = lambda: {'v': 1}

        async def go():
            t = asyncio.ensure_future(ws_mod.status_broadcast(clean_state))
            await asyncio.sleep(0.4)
            clean_state.running = False
            await t
        clean_state.running = True
        asyncio.new_event_loop().run_until_complete(go())
        clean_state.running = True
        assert len(visible.sent) == 1             # данные не менялись — одно сообщение
        assert visible.sent[0]['delta'] is False
        assert len(hidden.sent) == 1              # скрытый — первое сразу, дальше реже


# ---------------------------------------------------------------------------
# Статика: ETag, 304, gzip
# ---------------------------------------------------------------------------
@pytest.fixture
def client(clean_state, monkeypatch):
    monkeypatch.setenv('PICARPRO_SECRET_KEY', 'test-secret')
    monkeypatch.setattr('config.AUTH_TOKEN', '')
    import picarpro.web.auth as auth_mod
    monkeypatch.setattr(auth_mod, 'AUTH_TOKEN', '')
    from picarpro.web.app import create_app
    app = create_app(clean_state)
    app.config['TESTING'] = True
    return app.test_client()


class TestStaticAssets:
    def test_etag_and_304(self, client):
        r = client.get('/app.js')
        assert r.status_code == 200 and r.headers['ETag']
        r2 = client.get('/app.js', headers={'If-None-Match': r.headers['ETag']})
        assert r2.status_code == 304 and r2.data == b''

    def test_gzip_when_accepted(self, client):
        plain = client.get('/app.js')
        gz = client.get('/app.js', headers={'Accept-Encoding': 'gzip'})
        assert gz.headers['Content-Encoding'] == 'gzip'
        assert gzip.decompress(gz.data) == plain.data
        assert len(gz.data) < len(plain.data) / 2

    def test_html_and_core_served(self, client):
        assert b'panel-core.js' in client.get('/').data
        r = client.get('/panel-core.js')
        assert r.status_code == 200 and b'PanelCore' in r.data

    def test_png_cacheable(self, client):
        r = client.get('/rpi_pinout.png')
        assert r.status_code == 200 and 'max-age=86400' in r.headers['Cache-Control']

    def test_header_client_id_used_for_arbitration(self, client, clean_state):
        clean_state.control.take('phone')
        r = client.post('/cmd/speed', json={'value': 77}, headers={'X-Client-Id': 'pc'})
        assert r.get_json()['code'] == 'not_driver'
        r = client.post('/cmd/speed', json={'value': 77})        # без id — как раньше
        assert r.get_json()['ok'] is True


# ---------------------------------------------------------------------------
# JS: чистая логика и DOM-поведение (если есть node / jsdom)
# ---------------------------------------------------------------------------
JS_DIR = os.path.join(os.path.dirname(__file__), 'js')


@pytest.mark.skipif(shutil.which('node') is None, reason='node не установлен')
def test_panel_core_js():
    r = subprocess.run(['node', '--test', os.path.join(JS_DIR, 'panel_core.test.js')],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]


@pytest.mark.skipif(shutil.which('node') is None, reason='node не установлен')
def test_panel_dom_js():
    env = dict(os.environ)
    probe = subprocess.run(['node', '-e', "require('jsdom')"], capture_output=True, env=env)
    if probe.returncode != 0:
        pytest.skip('jsdom не установлен (npm i jsdom; задайте NODE_PATH)')
    r = subprocess.run(['node', '--test', '--test-force-exit', '--test-timeout=15000',
                        os.path.join(JS_DIR, 'panel_dom.test.js')],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
