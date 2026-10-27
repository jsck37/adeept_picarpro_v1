"""Тесты диспетчера команд (безопасный парсинг + новые команды слияния)."""
import pytest


@pytest.fixture
def state(clean_state):
    return clean_state


def call(state, cmd, params=None):
    from picarpro.commands import process
    return process(state, {'cmd': cmd, 'params': params or {}})


class TestMove:
    def test_forward(self, state):
        r = call(state, 'move', {'dir': 'forward'})
        assert r['ok'] is True
        assert r['dir'] == 'forward'

    def test_stop(self, state, fake_gpio):
        call(state, 'move', {'dir': 'forward'})
        r = call(state, 'move', {'dir': 'stop'})
        assert r['ok'] is True
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        assert fake_gpio.pwm_for(17).duty_cycle == 0

    def test_steering_applied(self, state):
        call(state, 'move', {'dir': 'left'})
        assert state.servos.get_angle(0) == 150

    def test_forward_left_diagonal(self, state):
        r = call(state, 'move', {'dir': 'forward_left'})
        assert r['ok'] is True
        assert state.servos.get_angle(0) == 120


class TestSpeed:
    def test_set(self, state):
        r = call(state, 'speed', {'value': 80})
        assert r['ok'] and state.speed == 80

    def test_clamped(self, state):
        call(state, 'speed', {'value': 500})
        assert state.speed == 100

    def test_garbage_falls_to_default(self, state):
        call(state, 'speed', {'value': 90})
        call(state, 'speed', {'value': 'garbage'})
        assert state.speed == 50  # safe_int -> DEFAULT_SPEED

    def test_none_value_safe(self, state):
        r = call(state, 'speed', {'value': None})
        assert r['ok'] is True


class TestServo:
    def test_valid(self, state):
        r = call(state, 'servo', {'id': 1, 'angle': 45})
        assert r['ok'] is True
        assert state.servos.get_angle(1) == 45

    def test_garbage_no_crash(self, state):
        """int('abc') больше не роняет обработчик."""
        r = call(state, 'servo', {'id': 'abc', 'angle': 'xyz'})
        assert r['ok'] is True  # safe_int подставил дефолты

    def test_out_of_range_id_clamped(self, state):
        """ID за пределами диапазона зажимается safe_int до 6."""
        r = call(state, 'servo', {'id': 99, 'angle': 90})
        assert r['ok'] is True
        assert r['id'] == 6

    def test_home(self, state):
        state.servos.set_angle(1, 30)
        r = call(state, 'servo_home')
        assert r['ok'] is True
        assert state.servos.get_angle(1) == 90

    def test_limits_readonly_from_config(self, state):
        """v1.3.0: лимиты только из config.py, редактирование удалено."""
        from config import SERVO_LIMITS
        r = call(state, 'servo_get_limits')
        assert r['ok'] is True
        assert r['limits'] == {
            int(k): dict(v) for k, v in SERVO_LIMITS.items()}

    def test_servo_set_limits_removed(self, state):
        r = call(state, 'servo_set_limits', {'id': 3, 'min': 20, 'max': 100})
        assert r['ok'] is False
        assert 'Unknown command' in r.get('error', '')


class TestLedBuzzer:
    def test_led_solid(self, state):
        r = call(state, 'led', {'mode': 'solid', 'color': [10, 20, 30]})
        assert r['ok'] is True
        assert state.led_mode == 'solid'

    def test_led_color_clamped(self, state):
        r = call(state, 'led', {'mode': 'solid', 'color': [999, -5, 'x']})
        assert r['ok'] is True

    def test_led_bad_mode(self, state):
        r = call(state, 'led', {'mode': 'explode'})
        assert r['ok'] is False

    def test_buzzer_melodies(self, state):
        for melody in ('beep', 'birthday', 'alarm'):
            r = call(state, 'buzzer', {'melody': melody})
            assert r['ok'] is True
        r = call(state, 'buzzer_stop')
        assert r['ok'] is True


class TestCrane:
    def test_arm_toggle(self, state):
        r = call(state, 'crane', {'action': 'arm_toggle'})
        assert r['ok'] and r['closed'] is True
        r = call(state, 'crane', {'action': 'arm_toggle'})
        assert r['closed'] is False

    def test_grip_positions(self, state):
        # v1.3.0: grip 100..180 (углы в механический стоп больше не проходят)
        for act, angle in (('grip_low', 100), ('grip_mid', 135),
                           ('grip_high', 180)):
            r = call(state, 'crane', {'action': act})
            assert r['ok'] is True
            assert state.servos.get_angle(5) == angle
        assert state.crane_grip_position == 'high'

    def test_grip_custom_angle(self, state):
        r = call(state, 'crane', {'action': 'grip_angle', 'angle': 60})
        assert r['ok'] is True
        # 60 ниже лимита серво 5 (min 100) — зажимается в 100
        assert state.servos.get_angle(5) == 100


class TestSwitchesHeadlightBlinker:
    def test_switch(self, state):
        r = call(state, 'switch', {'id': 0, 'state': True})
        assert r['ok'] is True

    def test_headlight(self, state):
        r = call(state, 'headlight', {'action': 'on'})
        assert r['ok'] and r['headlight'] is True
        r = call(state, 'headlight', {'action': 'off'})
        assert r['headlight'] is False

    def test_blinker_left_right(self, state):
        call(state, 'blinker', {'side': 'left', 'active': True})
        assert state.left_blinker is True
        call(state, 'blinker', {'side': 'right', 'active': True})
        assert state.left_blinker is False
        assert state.right_blinker is True
        call(state, 'blinker', {'side': 'both_off'})
        assert not state.left_blinker and not state.right_blinker


class TestAutoModes:
    def test_stop(self, state):
        r = call(state, 'auto', {'func': 'stop'})
        assert r['ok'] is True

    def test_unknown_mode(self, state):
        r = call(state, 'auto', {'func': 'dance'})
        assert r['ok'] is False

    def test_findcolor_valid(self, state):
        r = call(state, 'auto', {'func': 'findColor'})
        # камера отсутствует на фейке (init_camera требует picamera2 —
        # фейк есть, значит камера создастся)
        assert r['ok'] is True

    def test_watchdog_valid(self, state):
        r = call(state, 'auto', {'func': 'watchDog'})
        assert r['ok'] is True

    def test_keepdistance_needs_ultra(self, state):
        # ultra инициализирован на фейке -> должен стартовать
        r = call(state, 'auto', {'func': 'keepDistance'})
        assert r['ok'] is True
        call(state, 'auto', {'func': 'stop'})


class TestSteady:
    def test_toggle(self, state):
        r = call(state, 'steady', {'action': 'toggle'})
        # mpu6050 присутствует на фейке -> стартует
        assert r['ok'] is True
        r = call(state, 'steady', {'action': 'toggle'})
        assert r['steady'] is False

    def test_off_without_mpu(self, state, monkeypatch):
        state.steady = None
        r = call(state, 'steady', {'action': 'on'})
        assert r['ok'] is False


class TestBattery:
    def test_battery_command(self, state):
        r = call(state, 'battery')
        assert r['ok'] is True
        assert 'battery' in r


class TestValidation:
    def test_unknown_command(self, state):
        r = call(state, 'self_destruct')
        assert r['ok'] is False
        assert 'Unknown' in r['error']

    def test_non_dict_params(self, state):
        from picarpro.commands import process
        r = process(state, {'cmd': 'speed', 'params': [1, 2, 3]})
        assert r['ok'] is True  # params проигнорированы, дефолт применился

    def test_non_dict_data(self, state):
        from picarpro.commands import process
        r = process(state, 'garbage')
        assert r['ok'] is False

    def test_web_active(self, state):
        r = call(state, 'web_active', {'active': True})
        assert state.web_active is True

    def test_hand_color_preset(self, state):
        state.init_camera()
        r = call(state, 'hand_color', {'preset': 'red'})
        assert r['ok'] is True

    def test_ds4_status(self, state):
        r = call(state, 'ds4_status')
        assert r['ok'] is True

    def test_i2c_scan(self, state):
        r = call(state, 'i2c_scan')
        assert r['ok'] is True
