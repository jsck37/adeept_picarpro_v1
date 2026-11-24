"""v1.6.16: починено положение «high» у наклона клешни (grip_high вернулся
как верх рабочего хода 100..150, а не старые 180° в механический стоп);
в админ-панели видны имена устройств (lease-файлы NetworkManager, память
имён по MAC, ручное переименование) и активное время использования
(реестр instance/admin_devices.json: session_s — сессия, active_s — всего).
"""
import pytest

# фикстуры app/client/admin_reset и хелпер авторизации — общие с v1.6.15
from tests.test_v1615 import admin_reset, app, client, _headers  # noqa: F401


def call(state, cmd, params=None):
    from picarpro.commands import process
    return process(state, {'cmd': cmd, 'params': params or {}})


# ---------------------------------------------------------------------------
# Клешня: «Верхний» снова работает
# ---------------------------------------------------------------------------
class TestCraneHigh:
    def test_grip_high_command(self, clean_state):
        from config import SERVO_CRANE_GRIP

        r = call(clean_state, 'crane', {'action': 'grip_high'})
        assert r['ok'] is True
        assert clean_state.servos.get_angle(SERVO_CRANE_GRIP) == 150
        assert clean_state.crane_grip_position == 'high'

    def test_grip_high_within_working_range(self):
        # 150° — верх РАБОЧЕГО хода (v1.6.10, задан владельцем), а не
        # удалённые в v1.6.4 180°, упиравшиеся в механический стоп
        import config
        assert config.CRANE_GRIP_HIGH == 150
        lim = config.SERVO_LIMITS[config.SERVO_CRANE_GRIP]
        assert lim['min'] <= config.CRANE_GRIP_HIGH <= lim['max']

    def test_high_is_part_of_ds4_cycle(self):
        from picarpro.hardware.ds4 import CRANE_GRIP_POSITIONS, \
            CRANE_GRIP_LABELS
        assert CRANE_GRIP_POSITIONS[-1] == 150
        assert CRANE_GRIP_LABELS[-1] == 'high'

    def test_ui_high_button_contract(self, client):
        # кнопка «Верхний» шлёт grip_high; бэкенд понимает команду;
        # подпись угла — 150 (внутри рабочего хода), а не 170/180
        html = client.get('/').data.decode('utf-8')
        assert 'id="crane-grip-high"' in html
        assert 'data-action="grip_high"' in html
        js = client.get('/app.js').data.decode('utf-8')
        assert 'high: 150' in js
        from picarpro import commands as cmd_mod
        import inspect
        src = inspect.getsource(cmd_mod)
        assert "'grip_high'" in src


# ---------------------------------------------------------------------------
# hotspot: lease-файлы NetworkManager и память имён
# ---------------------------------------------------------------------------
class TestHotspotNames:
    def test_lease_glob_discovery(self, tmp_path, monkeypatch):
        """v1.6.15 брал leases только из фиксированных путей: leases точки
        доступа NetworkManager (10.42.0.x) не находились, и имена были
        пустыми. Теперь шаблоны /var/lib/NetworkManager/*.leases тоже
        проверяются."""
        from picarpro.utils import hotspot
        nm_dir = tmp_path / 'NetworkManager'
        nm_dir.mkdir()
        (nm_dir / 'dnsmasq-wlan0.leases').write_text(
            '4000000000 aa:bb:cc:dd:ee:42 10.42.0.65 pixel 01:aa\n',
            encoding='utf-8')
        monkeypatch.setattr(hotspot, '_LEASE_GLOBS',
                            (str(nm_dir / '*.leases'),))
        out = hotspot.list_clients(ifaces=(), arp_path=str(tmp_path / 'nope'))
        assert [d['mac'] for d in out] == ['aa:bb:cc:dd:ee:42']
        assert out[0]['hostname'] == 'pixel'

    def test_lease_entries_merge_all_files(self, tmp_path):
        """Раньше брался только ПЕРВЫЙ найденный lease-файл; теперь все
        найденные сливаются по MAC (hostname — первая не пустая)."""
        from picarpro.utils import hotspot
        f1 = tmp_path / 'a.leases'
        f2 = tmp_path / 'b.leases'
        f1.write_text(
            '4000000000 aa:bb:cc:dd:ee:01 10.42.0.50 * 01:ab\n', encoding='utf-8')
        f2.write_text(
            '4000000000 aa:bb:cc:dd:ee:01 10.42.0.50 phone 01:ab\n'
            '4000000000 bb:11:22:33:44:55 10.42.0.51 laptop 02:cd\n',
            encoding='utf-8')
        out = hotspot._lease_entries(paths=[str(f1), str(f2)])
        by_mac = {d['mac']: d for d in out}
        assert by_mac['aa:bb:cc:dd:ee:01']['hostname'] == 'phone'
        assert by_mac['bb:11:22:33:44:55']['hostname'] == 'laptop'

    def test_known_name_fallback(self, tmp_path):
        """ Lease не дал hostname ('*') — имя берётся из реестра (known)."""
        from picarpro.utils import hotspot
        leases = tmp_path / 'leases'
        leases.write_text(
            '4000000000 aa:bb:cc:dd:ee:01 10.42.0.50 * 01:ab\n',
            encoding='utf-8')
        out = hotspot.list_clients(ifaces=(), lease_paths=[str(leases)],
                                   arp_path=str(tmp_path / 'nope'),
                                   known={'aa:bb:cc:dd:ee:01':
                                          {'name': 'гостиная-телефон'}})
        assert out[0]['hostname'] == 'гостиная-телефон'

    def test_lease_hostname_beats_known(self, tmp_path):
        """Если lease hostname есть — он сильнее памяти реестра."""
        from picarpro.utils import hotspot
        leases = tmp_path / 'leases'
        leases.write_text(
            '4000000000 aa:bb:cc:dd:ee:01 10.42.0.50 fresh 01:ab\n',
            encoding='utf-8')
        out = hotspot.list_clients(ifaces=(), lease_paths=[str(leases)],
                                   arp_path=str(tmp_path / 'nope'),
                                   known={'aa:bb:cc:dd:ee:01':
                                          {'name': 'old-memory'}})
        assert out[0]['hostname'] == 'fresh'


# ---------------------------------------------------------------------------
# admin: реестр устройств — имена + активное время использования
# ---------------------------------------------------------------------------
@pytest.fixture
def dev_registry(admin_reset, tmp_path):
    """Админ-модуль с реестром во временном каталоге."""
    from picarpro.web import admin
    admin._load_devices(str(tmp_path / 'admin_devices.json'))
    return admin


class TestDeviceRegistry:
    def test_track_accumulates_active_time(self, dev_registry):
        admin = dev_registry
        c = {'mac': 'AA:BB:CC:DD:EE:01', 'ip': '10.42.0.50', 'hostname': ''}
        admin.devices_track([c], now=1000.0)
        info = admin.devices_info()['aa:bb:cc:dd:ee:01']
        assert info['active_s'] == pytest.approx(admin.TRACK_INTERVAL_S)
        admin.devices_track([c], now=1010.0)          # +10 c непрерывно
        info = admin.devices_info()['aa:bb:cc:dd:ee:01']
        assert info['active_s'] == pytest.approx(20.0)
        assert info['session_s'] == pytest.approx(20.0)
        assert info['first_seen'] == 1000.0

    def test_gap_starts_new_session(self, dev_registry):
        admin = dev_registry
        c = {'mac': 'aa:bb:cc:dd:ee:01', 'ip': '', 'hostname': ''}
        admin.devices_track([c], now=1000.0)
        admin.devices_track([c], now=1010.0)
        admin.devices_track([c], now=1600.0)          # вернулся через 10 мин
        info = admin.devices_info()['aa:bb:cc:dd:ee:01']
        # активное время копится, сессия начата заново с текущего шага
        assert info['active_s'] == pytest.approx(30.0)
        assert info['session_s'] == pytest.approx(admin.TRACK_INTERVAL_S)

    def test_hostname_memory(self, dev_registry):
        """Запоминаем последнее виденное hostname — имя переживает lease."""
        admin = dev_registry
        admin.devices_track([{'mac': 'aa:bb:cc:dd:ee:01', 'ip': '',
                              'hostname': 'phone'}], now=1000.0)
        admin.devices_track([{'mac': 'aa:bb:cc:dd:ee:01', 'ip': '',
                              'hostname': ''}], now=1010.0)
        assert admin.devices_info()['aa:bb:cc:dd:ee:01']['name'] == 'phone'

    def test_star_hostname_ignored(self, dev_registry):
        admin = dev_registry
        admin.devices_track([{'mac': 'aa:bb:cc:dd:ee:01', 'ip': '',
                              'hostname': '*'}], now=1000.0)
        assert admin.devices_info()['aa:bb:cc:dd:ee:01']['name'] == ''

    def test_manual_name_beats_hostname(self, dev_registry):
        admin = dev_registry
        mac = 'aa:bb:cc:dd:ee:01'
        admin.devices_track([{'mac': mac, 'ip': '', 'hostname': 'phone'}],
                            now=1000.0)
        r = admin.device_set_name(mac, 'Гостиная')
        assert r['ok'] is True
        # lease снова прислал hostname — ручное имя не перезаписалось
        admin.devices_track([{'mac': mac, 'ip': '', 'hostname': 'android-x'}],
                            now=1010.0)
        info = admin.devices_info()[mac]
        assert info['name'] == 'Гостиная'
        assert info['manual'] is True

    def test_manual_name_reset(self, dev_registry):
        admin = dev_registry
        mac = 'aa:bb:cc:dd:ee:01'
        admin.device_set_name(mac, 'Гостиная')
        r = admin.device_set_name(mac, '   ')
        assert r['ok'] is True
        info = admin.devices_info()[mac]
        assert info['manual'] is False and info['name'] == ''

    def test_manual_name_truncated(self, dev_registry):
        admin = dev_registry
        r = admin.device_set_name('aa:bb:cc:dd:ee:01', 'x' * 300)
        assert len(r['name']) == admin.DEVICE_NAME_MAX

    def test_bad_mac_rejected(self, dev_registry):
        r = dev_registry.device_set_name('не-mac', 'x')
        assert r['ok'] is False and 'error' in r

    def test_registry_roundtrip(self, dev_registry, tmp_path):
        """Реестр переживает перезапуск: save при трекинге, load при старте."""
        admin = dev_registry
        path = tmp_path / 'admin_devices.json'
        admin.devices_track([{'mac': 'aa:bb:cc:dd:ee:01', 'ip': '',
                              'hostname': 'phone'}], now=1000.0)
        assert path.exists()          # структурная правка -> сохранено сразу
        # «перезапуск»: чистая память + загрузка из файла
        admin._devices.clear()
        admin._load_devices(str(path))
        info = admin.devices_info()['aa:bb:cc:dd:ee:01']
        assert info['name'] == 'phone'
        assert info['active_s'] == pytest.approx(admin.TRACK_INTERVAL_S)
        assert info['first_seen'] == 1000.0

    def test_prune_stale_devices(self, dev_registry):
        admin = dev_registry
        mac = 'aa:bb:cc:dd:ee:01'
        admin.devices_track([{'mac': mac, 'ip': '', 'hostname': ''}],
                            now=1000.0)
        # 31 день без присутствия — вычищается при следующем трекинге
        admin.devices_track([{'mac': 'aa:bb:cc:dd:ee:02', 'ip': '',
                              'hostname': ''}], now=1000.0 + 31 * 86400)
        assert mac not in admin.devices_info()

    def test_max_devices_cap(self, dev_registry):
        admin = dev_registry
        now = 1000.0
        for i in range(admin.MAX_DEVICES + 10):
            admin.devices_track([{'mac': f'aa:bb:cc:dd:{i:02x}:01',
                                  'ip': '', 'hostname': ''}], now=now + i)
        assert len(admin.devices_info()) <= admin.MAX_DEVICES

    def test_known_names_shape(self, dev_registry):
        admin = dev_registry
        admin.device_set_name('aa:bb:cc:dd:ee:01', 'hall')
        known = admin.known_names()
        assert known == {'aa:bb:cc:dd:ee:01': {'name': 'hall'}}


# ---------------------------------------------------------------------------
# маршруты: активное время в /api/admin/devices + ручное имя
# ---------------------------------------------------------------------------
class TestDeviceRoutes:
    def test_devices_route_activity_fields(self, client, admin_reset,
                                           monkeypatch):
        from picarpro.utils import hotspot
        from picarpro.web import admin
        admin.devices_track([{'mac': 'aa:bb:cc:dd:ee:01', 'ip': '10.42.0.50',
                              'hostname': 'phone'}], now=1000.0)
        monkeypatch.setattr(hotspot, 'list_clients',
                            lambda known=None: [
                                {'mac': 'aa:bb:cc:dd:ee:01',
                                 'ip': '10.42.0.50', 'hostname': 'phone',
                                 'signal_dbm': -55}])
        h = _headers(client)
        r = client.get('/api/admin/devices', headers=h)
        j = r.get_json()
        assert r.status_code == 200 and j['ok']
        me = next(c for c in j['clients'] if c['mac'] == 'aa:bb:cc:dd:ee:01')
        for key in ('manual_name', 'first_seen', 'last_seen',
                    'active_s', 'session_s'):
            assert key in me
        assert me['hostname'] == 'phone'
        assert me['active_s'] > 0

    def test_manual_rename_overrides_hostname(self, client, admin_reset,
                                              monkeypatch):
        from picarpro.utils import hotspot
        from picarpro.web import admin
        admin.device_set_name('aa:bb:cc:dd:ee:01', 'гостиная-телефон')
        monkeypatch.setattr(hotspot, 'list_clients',
                            lambda known=None: [
                                {'mac': 'aa:bb:cc:dd:ee:01',
                                 'ip': '10.42.0.50', 'hostname': 'android-x',
                                 'signal_dbm': -55}])
        h = _headers(client)
        j = client.get('/api/admin/devices', headers=h).get_json()
        me = j['clients'][0]
        assert me['hostname'] == 'гостиная-телефон'
        assert me['manual_name'] is True

    def test_device_name_route_roundtrip(self, client, admin_reset):
        h = _headers(client)
        r = client.post('/api/admin/device_name', headers=h,
                        json={'mac': 'aa:bb:cc:dd:ee:01', 'name': 'hall'})
        assert r.status_code == 200 and r.get_json()['ok']
        r = client.post('/api/admin/device_name', headers=h,
                        json={'mac': 'bad', 'name': 'x'})
        assert r.status_code == 400
        # без админ-токена маршрут закрыт
        r = client.post('/api/admin/device_name',
                        json={'mac': 'aa:bb:cc:dd:ee:01', 'name': 'x'})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# UI-контракт
# ---------------------------------------------------------------------------
class TestUIContract:
    def _get(self, client, path):
        rv = client.get(path)
        assert rv.status_code == 200
        return rv.data.decode('utf-8')

    def test_activity_column_in_page(self, client):
        html = self._get(client, '/')
        assert 'adm_col_active' in html
        # колонок в таблице устройств — 7 (имя, ip, mac, сигнал,
        # активность, состояние, кнопка); '<thead>' сам начинается
        # с '<th', поэтому убираем его перед подсчётом
        thead = html.split('<table class="adm-table">')[1].split('</thead>')[0]
        assert thead.replace('<thead>', '').count('<th') == 7

    def test_app_js_rename_and_time(self, client):
        js = self._get(client, '/app.js')
        assert '/api/admin/device_name' in js
        assert 'fmtActiveTime' in js
        assert "colSpan = 7" in js

    def test_css_name_button(self, client):
        css = self._get(client, '/style.css')
        assert '.adm-name-btn' in css and '.adm-col-active' in css

    def test_i18n_new_keys(self, client):
        ru = self._get(client, '/translate/ru.js')
        en = self._get(client, '/translate/en.js')
        for key in ('adm_col_active', 'adm_rename_prompt',
                    'adm_active_total', 'adm_active_session',
                    'adm_active_since', 'adm_act_h'):
            assert key in ru and key in en
