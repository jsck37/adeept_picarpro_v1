"""v1.6.10: пока счётчик «Устройства» = 0, нижняя подсветка мигает СИНИМ
(ожидание подключения); при подключении — прежний режим подсветки.
v1.6.13: «Устройства» считаются по точке доступа (iw/dnsmasq/arp), а не
только по вкладкам веб-интерфейса — подключённый к Wi-Fi робота телефон
(без открытого UI) тоже гасит подсветку ожидания."""
import time

import pytest

from tests.test_v150 import wait_for


@pytest.fixture(autouse=True)
def _no_hotspot(monkeypatch):
    """По умолчанию hotspot-счётчик «недоступен» (тесты идут не на роботе):
    подсветка ведёт себя как раньше — по ws_clients. Тесты hotspot-поведения
    (класс TestWaitingLedHotspot) подменяют счётчик явно."""
    monkeypatch.setattr('picarpro.state.hotspot.count_clients',
                        lambda *a, **k: (None, None))


def _solid_blue(leds):
    return leds._pixels[0] == (0, 0, 255)


def _dark(leds):
    return leds._pixels[0] == (0, 0, 0)


class TestWaitingLed:
    def test_boot_without_clients_blinks_blue(self, clean_state):
        leds = clean_state.leds
        assert len(clean_state.ws_clients) == 0
        assert leds._waiting is True and leds._mode == 'waiting'
        # мигает: за ~1.6 с видим и синий, и «выкл»
        seen_blue = wait_for(lambda: _solid_blue(leds), 1.6, 0.02)
        seen_dark = wait_for(lambda: _dark(leds), 1.6, 0.02)
        assert seen_blue and seen_dark

    def test_client_connect_stops_blinking_and_restores_mode(self, clean_state):
        leds = clean_state.leds
        clean_state.ws_clients.add(object())
        clean_state.refresh_waiting_led()
        assert leds._waiting is False
        assert leds._mode == 'off'                  # прежний режим (по умолчанию выкл)
        time.sleep(0.2)
        assert _dark(leds)

    def test_mode_set_while_waiting_is_applied_on_connect(self, clean_state):
        """Режим, выбранный в ожидании (геймпад, миссия), не теряется."""
        leds = clean_state.leds
        leds.set_mode('solid', (10, 200, 30))
        assert leds._mode == 'waiting'              # мигание не перебито
        clean_state.ws_clients.add(object())
        clean_state.refresh_waiting_led()
        assert leds._mode == 'solid' and leds._color == (10, 200, 30)

    def test_previous_mode_restored_after_all_clients_leave(self, clean_state):
        leds = clean_state.leds
        c = object()
        clean_state.ws_clients.add(c)
        clean_state.refresh_waiting_led()
        leds.set_mode('solid', (255, 255, 255))
        assert leds._mode == 'solid'
        clean_state.ws_clients.discard(c)
        clean_state.on_last_client_gone()           # как при закрытии вкладки
        assert leds._waiting is True and leds._mode == 'waiting'
        clean_state.ws_clients.add(object())
        clean_state.refresh_waiting_led()
        assert leds._mode == 'solid' and leds._color == (255, 255, 255)

    def test_second_client_keeps_waiting_off(self, clean_state):
        a, b = object(), object()
        clean_state.ws_clients.update((a, b))
        clean_state.refresh_waiting_led()
        clean_state.ws_clients.discard(a)
        clean_state.refresh_waiting_led()
        assert clean_state.leds._waiting is False   # устройство ещё одно

    def test_police_alarm_overrides_waiting(self, clean_state):
        leds = clean_state.leds
        assert leds._waiting is True
        leds.set_mode('police')
        assert leds._mode == 'police'               # тревога важнее ожидания

    def test_no_leds_is_safe(self, clean_state):
        clean_state.leds = None
        clean_state.refresh_waiting_led()           # не падает

    def test_blue_is_pure_blue(self, clean_state):
        leds = clean_state.leds
        assert wait_for(lambda: _solid_blue(leds), 1.6, 0.02)
        r, g, b = leds._pixels[0]
        assert (r, g) == (0, 0) and b == 255


class TestWaitingLedWebsocket:
    """Через настоящий обработчик вебсокета: подключился — синее мигание
    выключено, закрыл вкладку — снова ждём подключения."""

    def test_connect_and_disconnect_toggle_waiting(self, clean_state, monkeypatch):
        import json
        import picarpro.web.websocket as ws_mod
        from tests.test_websocket import FakeWS, run
        monkeypatch.setattr(ws_mod, 'token_required', lambda: False)
        state = clean_state
        assert state.leds._waiting is True                  # клиентов нет
        seen = {}

        class SpyWS(FakeWS):
            async def send(self, msg):
                # пока соединение открыто (идёт обмен), подсветка НЕ ждёт
                seen.setdefault('waiting_during', state.leds._waiting)
                await super().send(msg)

        ws = SpyWS(incoming=[json.dumps({'cmd': 'speed', 'params': {'value': 40}})])
        run(ws_mod.ws_handler(state, ws))
        assert seen['waiting_during'] is False
        # соединение закрылось, клиентов снова нет -> ждём подключения
        assert len(state.ws_clients) == 0
        assert state.leds._waiting is True and state.leds._mode == 'waiting'


class TestWaitingLedHotspot:
    """v1.6.13: подсветка ожидания реагирует на подключение к ТОЧКЕ ДОСТУПА,
    а не только на открытые вкладки веб-интерфейса."""

    def test_hotspot_device_goes_out_of_waiting(self, clean_state, monkeypatch):
        monkeypatch.setattr('picarpro.state.hotspot.count_clients',
                            lambda *a, **k: (2, 'iw'))
        assert len(clean_state.ws_clients) == 0
        clean_state.refresh_waiting_led()
        # телефон подключён к AP робота, но вкладок нет — ждать нечего
        assert clean_state.leds._waiting is False

    def test_empty_hotspot_keeps_waiting(self, clean_state, monkeypatch):
        monkeypatch.setattr('picarpro.state.hotspot.count_clients',
                            lambda *a, **k: (0, 'dnsmasq'))
        clean_state.refresh_waiting_led()
        assert clean_state.leds._waiting is True and clean_state.leds._mode == 'waiting'

    def test_hotspot_unavailable_falls_back_to_web(self, clean_state, monkeypatch):
        monkeypatch.setattr('picarpro.state.hotspot.count_clients',
                            lambda *a, **k: (None, None))
        clean_state.refresh_waiting_led()
        assert clean_state.leds._waiting is True      # как раньше: вкладок нет
        clean_state.ws_clients.add(object())
        clean_state.refresh_waiting_led()
        assert clean_state.leds._waiting is False

    def test_ds4_counts_as_connected_device(self, clean_state, monkeypatch):
        monkeypatch.setattr('picarpro.state.hotspot.count_clients',
                            lambda *a, **k: (0, 'arp'))
        ds4 = clean_state.ds4
        if ds4 is None:
            pytest.skip('DS4 отсутствует в тестовом окружении')
        monkeypatch.setattr(type(ds4), 'connected',
                            property(lambda self: True))
        clean_state.refresh_waiting_led()
        assert clean_state.leds._waiting is False     # геймпад подключён

    def test_status_reports_hotspot_clients(self, clean_state, monkeypatch):
        monkeypatch.setattr('picarpro.state.hotspot.count_clients',
                            lambda *a, **k: (3, 'iw'))
        st = clean_state.get_status()
        assert st['hotspot_clients'] == 3

    def test_status_reports_none_without_hotspot(self, clean_state, monkeypatch):
        monkeypatch.setattr('picarpro.state.hotspot.count_clients',
                            lambda *a, **k: (None, None))
        st = clean_state.get_status()
        assert st['hotspot_clients'] is None
        assert st['clients'] == 0
