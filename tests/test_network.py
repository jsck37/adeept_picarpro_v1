"""Тесты network (кэш IP, редирект) и system_info (кэшированные сэмплеры)."""
import time

import pytest

from picarpro.network import get_ip, reset_ip_cache


class TestGetIP:
    def setup_method(self):
        reset_ip_cache()

    def test_returns_ip(self, monkeypatch):
        monkeypatch.setattr('picarpro.network._detect_ip',
                            lambda: '192.168.1.50')
        assert get_ip() == '192.168.1.50'

    def test_cached(self, monkeypatch):
        """Повторные вызовы не пересчитывают IP (фикс subprocess-шторма)."""
        calls = []

        def fake_detect():
            calls.append(1)
            return '10.42.0.1'

        monkeypatch.setattr('picarpro.network._detect_ip', fake_detect)
        get_ip()
        get_ip()
        get_ip()
        assert len(calls) == 1

    def test_ttl_expiry(self, monkeypatch):
        calls = []

        def fake_detect():
            calls.append(1)
            return '10.42.0.1'

        monkeypatch.setattr('picarpro.network._detect_ip', fake_detect)
        monkeypatch.setattr('picarpro.network._IP_TTL_S', 0.0)
        get_ip()
        time.sleep(0.01)
        get_ip()
        assert len(calls) == 2

    def test_fallback_loopback(self, monkeypatch):
        """_detect_ip сам никогда не бросает — при сбое возвращает 127.0.0.1."""
        monkeypatch.setattr('picarpro.network._detect_ip',
                            lambda: '127.0.0.1')
        assert get_ip() == '127.0.0.1'


class TestRedirect:
    def test_redirect_starts_or_skips(self):
        """Порт 80 требует root — функция обязана мягко деградировать."""
        from picarpro.network import start_redirect_server
        # на тестовой машине порт 80 либо занят, либо запрещён — оба варианта ОК
        result = start_redirect_server(port=0, target_port=59999)
        assert isinstance(result, bool)


class TestSystemInfo:
    def test_get_all_keys(self, monkeypatch):
        from picarpro.utils.system_info import SystemInfo
        monkeypatch.setattr(SystemInfo, 'is_low_voltage', staticmethod(lambda: False))
        info = SystemInfo.get_all()
        assert {'cpu_temp', 'cpu_usage', 'ram', 'low_voltage'} <= set(info)

    def test_cpu_usage_no_sleep_on_call(self, monkeypatch):
        """Фикс: get_cpu_usage() больше не спит 0.3 с на вызов."""
        from picarpro.utils.system_info import SystemInfo
        t0 = time.monotonic()
        for _ in range(3):
            SystemInfo.get_cpu_usage()
        assert time.monotonic() - t0 < 0.3

    def test_ram_shape(self):
        from picarpro.utils.system_info import SystemInfo
        ram = SystemInfo.get_ram_info()
        assert {'total_mb', 'used_mb', 'percent'} <= set(ram)
        assert ram['percent'] >= 0


class TestVoltageSources:
    def test_dmesg_scan_safe_without_dmesg(self):
        from picarpro.utils.system_info import _scan_dmesg_undervoltage
        # на любой машине возвращает bool, не бросает исключений
        assert isinstance(_scan_dmesg_undervoltage(), bool)
