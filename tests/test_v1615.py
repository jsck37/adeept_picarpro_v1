"""v1.6.15: вкладка «Админ» (пароль C2H5OH) — прямой редактор конфига и
временное отключение Wi-Fi-клиентов (iptables по MAC), фиксированные
джойстики в полноэкранном режиме."""
import ast
import json

import pytest


@pytest.fixture
def admin_reset():
    """Чистое состояние админ-модуля между тестами."""
    from picarpro.web import admin
    admin._fails.clear()
    admin._blocks.clear()
    admin._blocks_path = None
    with admin._tokens_lock:
        admin._issued_nonces.clear()
    yield admin
    admin._fails.clear()
    admin._blocks.clear()
    admin._blocks_path = None
    with admin._tokens_lock:
        admin._issued_nonces.clear()


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


def _login(client, password='C2H5OH'):
    return client.post('/api/admin/login', json={'password': password})


def _headers(client, **kw):
    r = _login(client, **kw)
    assert r.status_code == 200
    return {'X-Admin-Token': r.get_json()['token']}


# ---------------------------------------------------------------------------
# вход
# ---------------------------------------------------------------------------
class TestAdminAuth:
    def test_wrong_password(self, client, admin_reset):
        assert client.post('/api/admin/login',
                           json={'password': 'beer'}).status_code == 403

    def test_right_password_returns_token(self, client, admin_reset):
        r = _login(client)
        j = r.get_json()
        assert r.status_code == 200 and j['ok']
        assert j['token'].count('.') == 2 and j['ttl_s'] > 0

    def test_lockout_after_fails(self, client, admin_reset):
        for _ in range(5):
            client.post('/api/admin/login', json={'password': 'bad'})
        r = client.post('/api/admin/login', json={'password': 'C2H5OH'})
        assert r.status_code == 429
        assert r.get_json()['error'] == 'login_locked'

    def test_config_requires_admin_token(self, client, admin_reset):
        assert client.get('/api/admin/config').status_code == 403
        h = _headers(client)
        assert client.get('/api/admin/config', headers=h).status_code == 200

    def test_logout_revokes_token(self, client, admin_reset):
        h = _headers(client)
        client.post('/api/admin/logout', headers=h)
        assert client.get('/api/admin/config',
                          headers=h).status_code == 403

    def test_password_matches_config_default(self, client, admin_reset):
        import config as cfg
        assert cfg.ADMIN_PASSWORD == 'C2H5OH'


# ---------------------------------------------------------------------------
# редактор конфига
# ---------------------------------------------------------------------------
class TestConfigEditor:
    def test_entries_snapshot(self, admin_reset):
        entries = {e['key']: e for e in admin_reset.config_entries()}
        assert 'DEFAULT_SPEED' in entries
        assert 'ADMIN_PASSWORD' in entries
        assert entries['SERVO_LIMITS']['type'] == 'dict'

    def test_set_config_writes_local(self, admin_reset, tmp_path):
        target = tmp_path / 'config_local.py'
        r = admin_reset.set_config({'DEFAULT_SPEED': 55}, str(target))
        assert r['applied'] == 1 and r['needs_restart'] is True
        tree = ast.parse(target.read_text(encoding='utf-8'))
        vals = {n.targets[0].id: ast.literal_eval(n.value)
                for n in tree.body
                if isinstance(n, ast.Assign)
                and isinstance(n.targets[0], ast.Name)}
        assert vals['DEFAULT_SPEED'] == 55

    def test_set_config_merges_and_backups(self, admin_reset, tmp_path):
        target = tmp_path / 'config_local.py'
        target.write_text('MY_FAV = 7\n', encoding='utf-8')
        r = admin_reset.set_config({'DEFAULT_SPEED': 66}, str(target))
        assert r['applied'] == 1
        text = target.read_text(encoding='utf-8')
        assert 'MY_FAV = 7' in text and 'DEFAULT_SPEED = 66' in text
        assert (tmp_path / 'config_local.py.bak').exists()

    def test_rejects_unknown_and_bad_types(self, admin_reset, tmp_path):
        target = tmp_path / 'config_local.py'
        r = admin_reset.set_config({'NOT_A_KEY': 1}, str(target))
        assert r['applied'] == 0 and 'NOT_A_KEY' in r['errors'][0]
        r = admin_reset.set_config({'DEFAULT_SPEED': 'fast'}, str(target))
        assert r['applied'] == 0 and r['errors']

    def test_bool_is_not_int(self, admin_reset, tmp_path):
        r = admin_reset.set_config({'DEFAULT_SPEED': True},
                                   str(tmp_path / 'c.py'))
        assert r['applied'] == 0 and r['errors']

    def test_servo_limits_shape(self, admin_reset, tmp_path):
        ok = admin_reset.set_config(
            {'SERVO_LIMITS': {2: {'min': 0, 'max': 180}}},
            str(tmp_path / 'c.py'))
        assert ok['applied'] == 1
        bad = admin_reset.set_config(
            {'SERVO_LIMITS': {2: {'min': 170, 'max': 10}}},
            str(tmp_path / 'c.py'))
        assert bad['applied'] == 0 and bad['errors']

    def test_no_change_not_written(self, admin_reset, tmp_path):
        import config as cfg
        target = tmp_path / 'c.py'
        r = admin_reset.set_config({'DEFAULT_SPEED': cfg.DEFAULT_SPEED},
                                   str(target))
        assert r['applied'] == 0
        assert not target.exists()


# ---------------------------------------------------------------------------
# блокировки клиентов
# ---------------------------------------------------------------------------
IPT_LOG = []


def fake_run(cmd, **kw):
    """Стаб subprocess.run: фиксирует вызовы iptables, всё «успешно»."""
    IPT_LOG.append(list(cmd))
    class R:
        returncode = 0
        stdout = ''
        stderr = ''
    return R()


class TestBlocks:
    def test_block_adds_input_and_forward(self, admin_reset, tmp_path):
        IPT_LOG.clear()
        r = admin_reset.block('AA:BB:CC:DD:EE:FF', 15, tmp_path,
                              run=fake_run, sweep=False)
        assert r['ok'] is True
        chains = [c[2] for c in IPT_LOG if c[0] == 'iptables']
        assert 'INPUT' in chains and 'FORWARD' in chains
        assert any('--mac-source' in a for c in IPT_LOG for a in c)
        reg = json.loads((tmp_path / 'admin_blocks.json').read_text())
        assert 'aa:bb:cc:dd:ee:ff' in reg
        assert reg['aa:bb:cc:dd:ee:ff']['until'] is not None

    def test_block_until_cancelled(self, admin_reset, tmp_path):
        admin_reset.block('aa:bb:cc:dd:ee:ff', None, tmp_path,
                          run=fake_run, sweep=False)
        assert admin_reset.blocks_status()['aa:bb:cc:dd:ee:ff'][
            'remaining_s'] is None

    def test_block_bad_mac(self, admin_reset, tmp_path):
        r = admin_reset.block('evil', 10, tmp_path, run=fake_run)
        assert r['ok'] is False and 'MAC' in r['error']

    def test_block_min_limit(self, admin_reset, tmp_path):
        r = admin_reset.block('aa:bb:cc:dd:ee:ff', 999999, tmp_path,
                              run=fake_run, sweep=False)
        assert r['ok'] is False

    def test_block_without_iptables_fails_gracefully(self, admin_reset,
                                                     tmp_path):
        def no_ipt(cmd, **kw):
            raise FileNotFoundError('iptables')
        r = admin_reset.block('aa:bb:cc:dd:ee:ff', 5, tmp_path,
                              run=no_ipt, sweep=False)
        assert r['ok'] is False and 'iptables' in r['error']

    def test_unblock_removes_rules(self, admin_reset, tmp_path):
        IPT_LOG.clear()
        admin_reset.block('aa:bb:cc:dd:ee:ff', 5, tmp_path,
                          run=fake_run, sweep=False)
        admin_reset.unblock('aa:bb:cc:dd:ee:ff', run=fake_run)
        deletes = [c for c in IPT_LOG if c[:2] == ['iptables', '-D']]
        assert len(deletes) >= 2          # INPUT и FORWARD
        assert admin_reset.blocks_status() == {}

    def test_reapply_restores_and_drops_expired(self, admin_reset, tmp_path):
        admin_reset.block('aa:bb:cc:dd:ee:01', 5, tmp_path,
                          run=fake_run, sweep=False)
        admin_reset.block('aa:bb:cc:dd:ee:02', 5, tmp_path,
                          run=fake_run, sweep=False)
        with admin_reset._blocks_lock:
            admin_reset._blocks['aa:bb:cc:dd:ee:02']['until'] = 1.0  # истекла
        admin_reset._save_blocks()          # реестр — источник правды на диске
        IPT_LOG.clear()
        applied = admin_reset.reapply_blocks(tmp_path, run=fake_run)
        assert applied == 1
        status = admin_reset.blocks_status()
        assert 'aa:bb:cc:dd:ee:01' in status
        assert 'aa:bb:cc:dd:ee:02' not in status


# ---------------------------------------------------------------------------
# список устройств
# ---------------------------------------------------------------------------
class TestListClients:
    def test_merge_iw_leases_arp(self, tmp_path):
        from picarpro.utils import hotspot
        leases = tmp_path / 'leases'
        leases.write_text(
            '4000000000 aa:bb:cc:dd:ee:01 10.42.0.50 phone 01:ab\n'
            'garbage line\n'
            '4000000000 bb:11:22:33:44:55 10.42.0.51 * 02:cd\n',
            encoding='utf-8')
        arp = tmp_path / 'arp'
        arp.write_text(
            'IP address       HW type     Flags       HW address            Mask     Device\n'
            '10.42.0.50       0x1         0x2         aa:bb:cc:dd:ee:01     *        uap0\n'
            '10.42.0.77       0x1         0x2         aa:bb:cc:dd:ee:09     *        uap0\n',
            encoding='utf-8')

        def run_iw(cmd, **kw):
            class R:
                returncode = 0
                stdout = ('Station aa:bb:cc:dd:ee:01 (on uap0)\n'
                          '\tsignal:  -53.00 dBm\n'
                          'Station aa:bb:cc:dd:ee:07 (on uap0)\n'
                          '\tsignal:  -70.00 dBm\n')
                stderr = ''
            assert cmd[:3] == ['iw', 'dev', 'uap0'] or cmd[0] == 'iw'
            return R()

        out = hotspot.list_clients(
            ifaces=('uap0',), lease_paths=[str(leases)],
            arp_path=str(arp), run=run_iw)
        by_mac = {d['mac']: d for d in out}
        assert set(by_mac) == {'aa:bb:cc:dd:ee:01', 'aa:bb:cc:dd:ee:07',
                               'aa:bb:cc:dd:ee:09', 'bb:11:22:33:44:55'}
        assert by_mac['aa:bb:cc:dd:ee:01']['ip'] == '10.42.0.50'
        assert by_mac['aa:bb:cc:dd:ee:01']['hostname'] == 'phone'
        assert by_mac['aa:bb:cc:dd:ee:01']['signal_dbm'] == -53
        assert by_mac['aa:bb:cc:dd:ee:07']['ip'] == ''    # только iw
        assert by_mac['aa:bb:cc:dd:ee:09']['ip'] == '10.42.0.77'
        assert by_mac['bb:11:22:33:44:55']['hostname'] == ''

    def test_expired_lease_skipped(self, tmp_path):
        from picarpro.utils import hotspot
        leases = tmp_path / 'leases'
        leases.write_text(
            '1 aa:bb:cc:dd:ee:01 10.42.0.50 old 01:ab\n',
            encoding='utf-8')
        out = hotspot.list_clients(ifaces=(), lease_paths=[str(leases)],
                                   arp_path=str(tmp_path / 'nope'))
        assert out == []


# ---------------------------------------------------------------------------
# UI-контракт
# ---------------------------------------------------------------------------
class TestAdminUI:
    def _get(self, client, path):
        rv = client.get(path)
        assert rv.status_code == 200
        return rv.data.decode('utf-8')

    def test_tab_and_sections_in_page(self, client):
        html = self._get(client, '/')
        assert 'data-tab="admin"' in html
        assert 'id="content-admin"' in html
        assert 'id="adm-cfg-list"' in html and 'id="adm-dev-tbody"' in html

    def test_app_js_admin_logic(self, client):
        js = self._get(client, '/app.js')
        assert '/api/admin/login' in js and 'X-Admin-Token' in js
        assert 'sessionStorage' in js

    def test_app_js_fixed_joystick(self, client):
        js = self._get(client, '/app.js')
        # фиксированный джойстик: вектор от точки первого касания
        assert 'origin = { x: e.clientX, y: e.clientY }' in js
        assert 'e.clientX - origin.x' in js

    def test_css_admin_rows(self, client):
        css = self._get(client, '/style.css')
        assert '.adm-row-dirty' in css and '.adm-table' in css

    def test_i18n_admin_keys(self, client):
        ru = self._get(client, '/translate/ru.js')
        en = self._get(client, '/translate/en.js')
        assert 'adm_err_wrong' in ru and 'adm_dev_blocked' in ru
        assert 'adm_err_wrong' in en and 'adm_dev_blocked' in en


# ---------------------------------------------------------------------------
# маршруты целиком
# ---------------------------------------------------------------------------
class TestAdminRoutes:
    def test_devices_route(self, client, admin_reset):
        h = _headers(client)
        r = client.get('/api/admin/devices', headers=h)
        j = r.get_json()
        assert r.status_code == 200 and j['ok']
        assert 'clients' in j and 'blocked' in j and 'now' in j

    def test_config_route_roundtrip(self, client, admin_reset, monkeypatch,
                                    tmp_path):
        # пишем во временный config_local, а не в корень проекта
        monkeypatch.setattr('picarpro.web.app.BASE_DIR', tmp_path)
        h = _headers(client)
        r = client.post('/api/admin/config', headers=h,
                        json={'values': {'DEFAULT_SPEED': 42}})
        j = r.get_json()
        assert j['ok'] and j['applied'] == 1
        assert (tmp_path / 'config_local.py').exists()

    def test_block_route_error_without_iptables(self, client, admin_reset):
        h = _headers(client)
        r = client.post('/api/admin/block', headers=h,
                        json={'mac': 'aa:bb:cc:dd:ee:ff', 'minutes': 5})
        # в песочнице iptables нет — но сервис не падает и ответ структурирован
        assert r.status_code in (200, 400)
        assert 'ok' in r.get_json()

    def test_restart_route(self, client, admin_reset, monkeypatch):
        import picarpro.updater as upd
        monkeypatch.setattr(upd, 'schedule_restart', lambda d=1.5: True)
        h = _headers(client)
        r = client.post('/api/admin/restart', headers=h)
        assert r.status_code == 200 and r.get_json()['scheduled'] is True
