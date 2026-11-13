"""Тесты мод-дополнений v1.1.0: команды статусов (yolo_status, push),
лимиты серво через команды. NavTCP/ArUco/ColorScan удалены в v1.6.7
(требовали вторую камеру / хост-компоненты)."""
import pytest


@pytest.fixture
def state(clean_state):
    return clean_state


def call(state, cmd, params=None):
    from picarpro.commands import process
    return process(state, {'cmd': cmd, 'params': params or {}})


class TestStatusCommands:
    def test_push_status_idle(self, state):
        r = call(state, 'push', {'action': 'status'})
        assert r['ok'] is True
        assert r['pushes_done'] == 0

    def test_aruco_nav_command_removed(self, state):
        r = call(state, 'aruco_nav', {'action': 'status'})
        assert r['ok'] is False            # команда удалена в v1.6.7

    def test_color_scan_command_removed(self, state):
        r = call(state, 'color_scan', {'action': 'status'})
        assert r['ok'] is False            # команда удалена в v1.6.7

    def test_yolo_status(self, state):
        r = call(state, 'yolo_status')
        assert r['ok'] is True
        assert 'loaded' in r

    def test_status_broadcast_contains_new_fields(self, state):
        d = state.get_status()
        assert 'yolo' in d and 'push' in d
        assert 'nav_tcp' not in d and 'aruco_nav' not in d \
            and 'color_scan' not in d


class TestServoLimitsViaCommands:
    """v1.3.0: лимиты серво — только из config.py, редактирование удалено."""

    def test_limits_readonly_from_config(self, state):
        from config import SERVO_LIMITS
        r = call(state, 'servo_get_limits')
        assert r['ok'] is True
        assert r['limits'] == {
            int(k): dict(v) for k, v in SERVO_LIMITS.items()}

    def test_angle_clamped_by_config_limits(self, state):
        # tilt: 80..170, grip: 100..180 — углы в стоп больше не проходят
        call(state, 'servo', {'id': 2, 'angle': 10})
        assert state.servos.get_angle(2) == 80
        call(state, 'servo', {'id': 2, 'angle': 200})
        assert state.servos.get_angle(2) == 170
        from config import SERVO_CRANE_GRIP, SERVO_CRANE_ARM
        # v1.6.4: grip_high (180°) удалён — максимум grip_mid (135)
        call(state, 'crane', {'action': 'grip_mid'})
        assert state.servos.get_angle(SERVO_CRANE_GRIP) == 135
        call(state, 'crane', {'action': 'grip_low'})
        assert state.servos.get_angle(SERVO_CRANE_GRIP) == 100
        call(state, 'crane', {'action': 'arm_open'})
        assert state.servos.get_angle(SERVO_CRANE_ARM) == 100


class TestObstaclePusher:
    def test_start_without_ultrasonic_fails(self, state):
        from picarpro.functions.arm_push import ObstaclePusher
        p = ObstaclePusher(state)
        state.ultrasonic = None
        ok, msg = p.start()
        assert ok is False
