"""Тесты bluetooth-модуля: валидация MAC, конфиг, сессия bluetoothctl."""
import json

import pytest

from picarpro.routes.bluetooth import validate_mac


class TestValidateMac:
    def test_valid(self):
        assert validate_mac('AA:BB:CC:DD:EE:FF') == 'AA:BB:CC:DD:EE:FF'
        assert validate_mac('aa:bb:cc:dd:ee:ff') == 'AA:BB:CC:DD:EE:FF'

    def test_injection(self):
        assert validate_mac('AA:BB:CC:DD:EE:FF\nremove *') is None

    def test_short(self):
        assert validate_mac('AA:BB') is None

    def test_wrong_chars(self):
        assert validate_mac('ZZ:BB:CC:DD:EE:FF') is None

    def test_empty(self):
        assert validate_mac('') is None
        assert validate_mac(None) is None

    def test_extra_spaces_stripped(self):
        assert validate_mac('  AA:BB:CC:DD:EE:FF  ') == 'AA:BB:CC:DD:EE:FF'


class TestConfig:
    def test_roundtrip(self, tmp_path, monkeypatch):
        import picarpro.routes.bluetooth as bt
        monkeypatch.setattr(bt, 'BT_CONFIG_FILE', tmp_path / 'bt.json')
        bt.save_config({'last_gamepad_mac': 'AA:BB:CC:DD:EE:FF'})
        data = json.loads((tmp_path / 'bt.json').read_text())
        assert data['last_gamepad_mac'] == 'AA:BB:CC:DD:EE:FF'
        assert bt.load_config() == {'last_gamepad_mac': 'AA:BB:CC:DD:EE:FF'}

    def test_load_missing(self, tmp_path, monkeypatch):
        import picarpro.routes.bluetooth as bt
        monkeypatch.setattr(bt, 'BT_CONFIG_FILE', tmp_path / 'nope.json')
        assert bt.load_config() == {}

    def test_load_corrupt(self, tmp_path, monkeypatch):
        import picarpro.routes.bluetooth as bt
        p = tmp_path / 'bt.json'
        p.write_text('{broken json')
        monkeypatch.setattr(bt, 'BT_CONFIG_FILE', p)
        assert bt.load_config() == {}

    def test_save_atomic(self, tmp_path, monkeypatch):
        """Сохранение атомарно (tmp + os.replace) — не оставляет мусора."""
        import picarpro.routes.bluetooth as bt
        monkeypatch.setattr(bt, 'BT_CONFIG_FILE', tmp_path / 'bt.json')
        bt.save_config({'a': 1})
        assert not (tmp_path / 'bt.json.tmp').exists()


class TestSession:
    def test_pair_invalid_mac(self, monkeypatch):
        import picarpro.routes.bluetooth as bt
        ok, msg = bt.pair_and_connect('evil\nmac', None)
        assert ok is False
        assert 'Invalid' in msg

    def test_disconnect_invalid_mac_safe(self, monkeypatch):
        import picarpro.routes.bluetooth as bt
        # не должно упасть и не должно трогать сессию
        monkeypatch.setattr(bt, '_get_session', lambda: None)
        bt.disconnect_device('nope')
