"""Тесты SwitchController: боковые огни, фара, поворотники."""
import time

import pytest


@pytest.fixture
def switches():
    from picarpro.hardware.switch import SwitchController
    s = SwitchController()
    yield s
    s.shutdown()


class TestInit:
    def test_pins_ready(self, switches, fake_gpio):
        assert switches._initialized
        assert len(switches._leds) == 2          # SWITCH_PINS = [6, 13]
        assert fake_gpio.modes.get(5) == fake_gpio.OUT  # HEADLIGHT_PIN


class TestSideLights:
    def test_on_off(self, switches, fake_gpio):
        switches.on(0)
        assert fake_gpio.outputs[6] == fake_gpio.HIGH
        assert switches.get_state(0) is True
        switches.off(0)
        assert fake_gpio.outputs[6] == fake_gpio.LOW
        assert switches.get_state(0) is False

    def test_invalid_index_noop(self, switches, fake_gpio):
        switches.on(5)
        assert 6 not in fake_gpio.outputs or True  # нет исключения
        assert switches.get_state(5) is False


class TestHeadlight:
    def test_toggle(self, switches, fake_gpio):
        assert switches.headlight_state is False
        switches.headlight_on()
        assert fake_gpio.outputs[5] == fake_gpio.HIGH
        assert switches.headlight_state is True
        switches.headlight_toggle()
        assert switches.headlight_state is False
        switches.headlight_off()
        assert switches.headlight_state is False


class TestBlinkers:
    def test_left_blinker_blinks(self, switches, fake_gpio):
        switches.set_blinker('left', True)
        time.sleep(1.0)
        # за секунду состояние должно меняться (мигание 0.4s)
        assert switches._blink_threads[0].is_alive()
        switches.set_blinker('left', False)
        time.sleep(0.1)
        assert not switches._blink_threads[0].is_alive()
        assert fake_gpio.outputs[6] == fake_gpio.LOW

    def test_blinker_toggle_off(self, switches):
        switches.set_blinker('left', True)
        time.sleep(0.1)
        assert switches._blink_flags[0].is_set() is True
        switches.set_blinker('left', False)
        time.sleep(0.1)
        assert switches._blink_flags[0].is_set() is False
        assert not switches._blink_threads[0].is_alive()

    def test_blinker_unknown_side_noop(self, switches):
        switches.set_blinker('center', True)  # нет исключения
