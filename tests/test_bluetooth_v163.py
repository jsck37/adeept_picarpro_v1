"""v1.6.3: Bluetooth — скан, подключение, спаривание на имитаторе bluetoothctl."""
import json
import os
import stat
import sys
import threading
import time

import pytest

FAKE = os.path.join(os.path.dirname(__file__), 'fakes', 'fake_bluetoothctl.py')
MAC = 'AA:BB:CC:DD:EE:01'
MAC2 = 'AA:BB:CC:DD:EE:02'


def dev(name='Wireless Controller', **kw):
    d = {'name': name, 'icon': 'input-gaming', 'paired': False, 'trusted': False,
         'connected': False, 'known': False, 'visible': True, 'rssi': -55}
    d.update(kw)
    return d


@pytest.fixture
def bt(tmp_path, monkeypatch):
    """Подменяет bluetoothctl имитатором; возвращает (модуль, scenario, команды())."""
    from picarpro.routes import bluetooth as mod
    scenario = {'adapter': True, 'devices': {}, 'pair': 'ok', 'connect': 'ok',
                'scan_delay': 0.15}
    sc_path = tmp_path / 'scenario.json'
    log = tmp_path / 'cmds.log'
    log.write_text('')
    binary = tmp_path / 'bin' / 'bluetoothctl'
    binary.parent.mkdir()
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE}\n')
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)

    def write():
        sc_path.write_text(json.dumps(scenario))
    write()
    monkeypatch.setenv('FAKE_BT_SCENARIO', str(sc_path))
    monkeypatch.setenv('FAKE_BT_LOG', str(log))
    monkeypatch.setenv('PATH', f'{binary.parent}:{os.environ["PATH"]}')
    monkeypatch.setattr(mod, '_load_hid_sony', lambda: True)
    mod.shutdown_session()

    class H:
        module = mod
        sc = scenario

        def reload(self):
            """Перезапустить сессию с обновлённым сценарием."""
            write()
            mod.shutdown_session()

        def cmds(self):
            return [l for l in log.read_text().splitlines() if l.strip()]
    h = H()
    yield h
    mod.shutdown_session()


class _Ds4:
    def __init__(self):
        self.rescans = 0

    def trigger_rescan(self):
        self.rescans += 1


# ---------------------------------------------------------------------------
class TestParsing:
    def test_clean_line_strips_ansi_and_prompt(self):
        from picarpro.routes.bluetooth import clean_line
        raw = '\x1b[0;94m[bluetooth]\x1b[0m# \x1b[0;92m[NEW]\x1b[0m Device AA:BB:CC:DD:EE:01 Pad\r'
        # колорированная «[NEW]» после приглашения
        assert clean_line(raw) == '[NEW] Device AA:BB:CC:DD:EE:01 Pad'
        assert clean_line('\x01\x1b[0;94m\x02[Wireless Controller]\x01\x1b[0m\x02# ') == ''

    def test_gamepad_detection(self):
        from picarpro.routes.bluetooth import is_gamepad
        assert is_gamepad('Wireless Controller')
        assert is_gamepad('whatever', icon='input-gaming')
        assert not is_gamepad('JBL Speaker')

    def test_mac_validation_blocks_injection(self):
        from picarpro.routes.bluetooth import validate_mac
        assert validate_mac(MAC.lower()) == MAC
        assert validate_mac(MAC + '\nremove ' + MAC2) is None
        assert validate_mac('AA:BB') is None and validate_mac(None) is None


class TestScan:
    def test_finds_gamepad_with_rssi_sorted_first(self, bt):
        bt.sc['devices'] = {MAC: dev(), MAC2: dev('JBL Speaker', icon='audio-card', rssi=-30)}
        bt.reload()
        devs = bt.module.scan_devices(scan_time=2)
        assert [d['mac'] for d in devs] == [MAC, MAC2]       # геймпад первым, хотя слабее
        assert devs[0]['is_gamepad'] and devs[0]['rssi'] == -55
        assert devs[0]['name'] == 'Wireless Controller'
        assert not devs[1]['is_gamepad']

    def test_stale_cache_hidden_known_devices_shown(self, bt):
        # «known» без visible — устаревший кэш BlueZ; paired+known — наш геймпад
        bt.sc['devices'] = {
            MAC: dev(visible=False, known=True),                               # устаревший
            MAC2: dev('Old Pad', visible=False, known=True, paired=True),        # сопряжён
        }
        bt.reload()
        macs = [d['mac'] for d in bt.module.scan_devices(scan_time=1)]
        assert MAC2 in macs and MAC not in macs

    def test_early_exit_when_gamepad_found(self, bt):
        bt.sc['devices'] = {MAC: dev()}
        bt.reload()
        t0 = time.monotonic()
        devs = bt.module.scan_devices(scan_time=12)
        assert devs and time.monotonic() - t0 < 8         # не ждём все 12 с

    def test_flood_of_events_not_lost(self, bt):
        bt.sc['devices'] = {MAC: dev()}
        bt.sc['flood'] = 400
        bt.sc['scan_delay'] = 0.05
        bt.reload()
        devs = bt.module.scan_devices(scan_time=3)
        assert len([d for d in devs if d['name'].startswith('Noise')]) == 400

    def test_no_adapter_raises_clear_error(self, bt):
        bt.sc['adapter'] = False
        bt.reload()
        with pytest.raises(RuntimeError, match='No Bluetooth adapter'):
            bt.module.scan_devices(scan_time=1)

    def test_hostile_name_returned_verbatim_for_ui_to_escape(self, bt):
        evil = '<img src=x onerror=alert(1)>'
        bt.sc['devices'] = {MAC: dev(evil)}
        bt.reload()
        assert bt.module.scan_devices(scan_time=1)[0]['name'] == evil

    def test_busy_when_another_operation_running(self, bt):
        bt.sc['devices'] = {MAC: dev()}
        bt.reload()
        res = {}
        t = threading.Thread(target=lambda: res.setdefault('d', bt.module.scan_devices(scan_time=3)))
        t.start()
        time.sleep(0.5)
        with pytest.raises(bt.module.BluetoothBusy):
            bt.module.scan_devices(scan_time=1)
        t.join()


class TestPairing:
    def test_new_controller_pairs_trusts_connects(self, bt):
        bt.sc['devices'] = {MAC: dev()}
        bt.reload()
        ds4 = _Ds4()
        ok, msg = bt.module.pair_and_connect(MAC, ds4)
        assert ok, msg
        cmds = bt.cmds()
        assert f'pair {MAC}' in cmds and f'trust {MAC}' in cmds
        assert cmds.index('scan on') < cmds.index(f'pair {MAC}')     # сначала найден
        assert f'remove {MAC}' not in cmds
        assert ds4.rescans == 1

    def test_controller_not_in_pairing_mode(self, bt):
        bt.sc['devices'] = {MAC: dev(visible=False)}
        bt.reload()
        import picarpro.routes.bluetooth as m
        orig = m._discover
        m._discover = lambda s, mac, timeout=12.0: orig(s, mac, 1.0)   # не ждать 12 с
        try:
            ok, msg = bt.module.pair_and_connect(MAC)
        finally:
            m._discover = orig
        assert not ok and 'Share + PS' in msg
        assert f'pair {MAC}' not in bt.cmds()

    def test_pair_refused_gives_actionable_hint(self, bt):
        bt.sc['devices'] = {MAC: dev()}
        bt.sc['pair'] = 'auth_fail'
        bt.reload()
        ok, msg = bt.module.pair_and_connect(MAC)
        assert not ok and 'pairing mode' in msg

    def test_paired_controller_connects_without_scan(self, bt):
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, trusted=True, visible=False)}
        bt.reload()
        ds4 = _Ds4()
        ok, msg = bt.module.pair_and_connect(MAC, ds4)
        assert ok, msg
        assert 'scan on' not in bt.cmds() and f'remove {MAC}' not in bt.cmds()

    def test_already_connected_is_ok(self, bt):
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, connected=True)}
        bt.reload()
        ok, msg = bt.module.pair_and_connect(MAC)
        assert ok and 'Already connected' in msg
        assert f'connect {MAC}' not in bt.cmds()

    def test_REGRESSION_controller_off_keeps_pairing(self, bt):
        """Раньше выключенный геймпад приводил к remove + повторному спариванию:
        сопряжение терялось при каждой загрузке."""
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, trusted=True, visible=False)}
        bt.sc['connect'] = 'page_timeout'
        bt.reload()
        ok, msg = bt.module.pair_and_connect(MAC)           # даже с allow_pairing=True
        assert not ok and 'not reachable' in msg
        cmds = bt.cmds()
        assert f'remove {MAC}' not in cmds and f'pair {MAC}' not in cmds
        assert bt.sc['devices'][MAC]['paired'] is True

    def test_auto_reconnect_never_pairs_or_removes(self, bt):
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, visible=False)}
        bt.sc['connect'] = 'auth_fail'
        bt.reload()
        ok, msg = bt.module.auto_reconnect(MAC)
        assert not ok and 'invalid' in msg.lower()
        cmds = bt.cmds()
        assert not any(c.startswith(('remove', 'pair ', 'scan on')) for c in cmds)

    def test_auto_reconnect_unknown_device(self, bt):
        bt.sc['devices'] = {}
        bt.reload()
        ok, msg = bt.module.auto_reconnect(MAC)
        assert not ok and 'not paired' in msg.lower()
        # NB: 'pairable on' — стартовая команда сессии, поэтому префикс 'pair '
        assert not any(c.startswith(('scan', 'pair ')) for c in bt.cmds())

    def test_stale_bond_is_repaired_when_allowed(self, bt):
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, trusted=True)}
        bt.sc['connect'] = 'auth_fail'
        bt.sc['after_remove_connect'] = 'ok'      # после чистой перепривязки всё работает
        bt.sc['autoconnect_after_pair'] = False
        bt.reload()
        ok, msg = bt.module.pair_and_connect(MAC, _Ds4())
        assert ok, msg
        cmds = bt.cmds()
        assert f'remove {MAC}' in cmds and f'pair {MAC}' in cmds
        assert cmds.index(f'remove {MAC}') < cmds.index(f'pair {MAC}')

    def test_invalid_mac_rejected_without_touching_bluetooth(self, bt):
        ok, msg = bt.module.pair_and_connect('zz')
        assert not ok and 'Invalid' in msg
        assert bt.cmds() == []

    def test_no_adapter_message(self, bt):
        bt.sc['adapter'] = False
        bt.reload()
        ok, msg = bt.module.pair_and_connect(MAC)
        assert not ok and 'adapter' in msg.lower()

    def test_disconnect_forget_and_keep(self, bt):
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, connected=True)}
        bt.reload()
        bt.module.disconnect_device(MAC, forget=False)
        assert f'disconnect {MAC}' in bt.cmds() and f'remove {MAC}' not in bt.cmds()
        bt.module.disconnect_device(MAC)
        assert f'remove {MAC}' in bt.cmds()


class TestSession:
    def test_recovers_after_process_death(self, bt):
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, connected=True)}
        bt.reload()
        s = bt.module._get_session()
        s._proc.kill()
        s._proc.wait()
        ok, msg = bt.module.pair_and_connect(MAC)
        assert ok and 'Already connected' in msg


# ---------------------------------------------------------------------------
class TestRoutes:
    @pytest.fixture
    def client(self, clean_state, monkeypatch):
        import picarpro.web.auth as auth_mod
        monkeypatch.setattr('config.AUTH_TOKEN', '')
        monkeypatch.setattr(auth_mod, 'AUTH_TOKEN', '')
        from picarpro.web.app import create_app
        app = create_app(clean_state)
        app.config['TESTING'] = True
        return app.test_client()

    def test_scan_route(self, bt, client):
        bt.sc['devices'] = {MAC: dev()}
        bt.reload()
        r = client.get('/api/bt/scan').get_json()
        assert r['ok'] and r['devices'][0]['is_gamepad'] and r['devices'][0]['rssi'] == -55

    def test_scan_route_reports_adapter_error_with_diagnostics(self, bt, client):
        bt.sc['adapter'] = False
        bt.reload()
        r = client.get('/api/bt/scan').get_json()
        assert r['ok'] is False and 'adapter' in r['error'] and 'diagnose' in r

    def test_auto_connect_route_does_not_pair(self, bt, client, monkeypatch):
        monkeypatch.setattr('picarpro.web.app.bt_load_config', lambda: {'last_gamepad_mac': MAC})
        bt.sc['devices'] = {MAC: dev(known=True, paired=True, visible=False)}
        bt.sc['connect'] = 'page_timeout'
        bt.reload()
        r = client.post('/api/bt/auto_connect').get_json()
        assert r['ok'] is False
        assert not any(c.startswith(('remove', 'pair ')) for c in bt.cmds())

    def test_diagnose_route(self, client):
        r = client.get('/api/bt/diagnose').get_json()
        assert r['ok'] and 'service' in r and 'rfkill_blocked' in r

    def test_command_layer_bt_scan_error_is_not_a_traceback(self, bt, clean_state):
        from picarpro.commands import process
        bt.sc['adapter'] = False
        bt.reload()
        r = process(clean_state, {'cmd': 'bt_scan', 'params': {}})
        assert r['ok'] is False and 'adapter' in r['error']
