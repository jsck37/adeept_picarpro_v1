"""Тесты мод-дополнений v1.1.0: NavTCP-протокол, портированные команды
(hugo-Peltier), yolo_status, лимиты серво через команды."""
import json
import threading
import time

import pytest


@pytest.fixture
def state(clean_state):
    return clean_state


def call(state, cmd, params=None):
    from picarpro.commands import process
    return process(state, {'cmd': cmd, 'params': params or {}})


class TestPortedCommands:
    def test_aruco_nav_status_idle(self, state):
        r = call(state, 'aruco_nav', {'action': 'status'})
        assert r['ok'] is True
        assert r['active'] is False

    def test_push_status_idle(self, state):
        r = call(state, 'push', {'action': 'status'})
        assert r['ok'] is True
        assert r['pushes_done'] == 0

    def test_color_scan_status_idle(self, state):
        r = call(state, 'color_scan', {'action': 'status'})
        assert r['ok'] is True
        assert r['active'] is False

    def test_yolo_status(self, state):
        r = call(state, 'yolo_status')
        assert r['ok'] is True
        assert 'loaded' in r

    def test_status_broadcast_contains_new_fields(self, state):
        d = state.get_status()
        assert 'yolo' in d and 'nav_tcp' in d
        assert 'aruco_nav' in d and 'push' in d and 'color_scan' in d


class TestServoLimitsViaCommands:
    def test_set_and_get_limits(self, state, tmp_path):
        r = call(state, 'servo_set_limits', {'id': 0, 'min': 40, 'max': 140})
        assert r['ok'] is True
        r = call(state, 'servo_get_limits')
        assert r['ok'] is True
        lim = r['limits'][0]
        assert lim['min'] == 40 and lim['max'] == 140

    def test_limit_swap_on_inversion(self, state):
        r = call(state, 'servo_set_limits', {'id': 1, 'min': 150, 'max': 20})
        assert r['ok'] is True
        assert r['min'] == 20 and r['max'] == 150

    def test_angle_clamped_by_limits(self, state):
        call(state, 'servo_set_limits', {'id': 2, 'min': 60, 'max': 120})
        call(state, 'servo', {'id': 2, 'angle': 10})
        assert state.servos.get_angle(2) == 60
        call(state, 'servo', {'id': 2, 'angle': 200})
        assert state.servos.get_angle(2) == 120


class TestNavTcpProtocol:
    """NavTCP: построчный протокол команд хоста (порт hugo-Peltier)."""

    @pytest.fixture
    def server(self, state, unused_tcp_port_factory):
        from picarpro.routes.nav_server import NavTcpServer
        srv = NavTcpServer(state, port=unused_tcp_port_factory())
        srv.start()
        time.sleep(0.15)
        yield srv
        srv.shutdown()

    def _talk(self, port, messages):
        import socket
        results = []
        with socket.create_connection(('127.0.0.1', port), timeout=3) as s:
            f = s.makefile('rwb')
            for msg in messages:
                f.write((msg + '\n').encode('utf-8'))
                f.flush()
                if msg == 'STATUS':
                    results.append(json.loads(f.readline().decode()))
                elif msg == 'QUIT':
                    results.append(f.readline().decode().strip())
                else:
                    results.append(f.readline().decode().strip())
        return results

    def test_move_commands(self, server, state):
        port = server.port
        resp = self._talk(port, ['FORWARD', 'LEFT', 'STOP', 'QUIT'])
        assert resp[0] == 'OK FORWARD'
        assert resp[1] == 'OK LEFT'
        assert resp[2] == 'OK STOP'
        assert resp[3] == 'BYE'
        assert state.nav_last_cmd == 'QUIT'

    def test_speed_command(self, server, state):
        resp = self._talk(server.port, ['SPEED 42', 'QUIT'])
        assert resp[0] == 'OK SPEED 42'
        assert state.speed == 42

    def test_speed_garbage(self, server):
        resp = self._talk(server.port, ['SPEED abc', 'QUIT'])
        assert resp[0].startswith('ERR')

    def test_unknown_command(self, server):
        resp = self._talk(server.port, ['FLYWHEEL', 'QUIT'])
        assert resp[0].startswith('ERR unknown')

    def test_status_json(self, server):
        resp = self._talk(server.port, ['STATUS', 'QUIT'])
        st = resp[0]
        assert st['ok'] is True
        assert 'speed' in st and 'last_cmd' in st

    def test_pick_blue_and_drop(self, server, state):
        resp = self._talk(server.port, ['PICK_BLUE', 'DROP', 'QUIT'])
        assert resp[0] == 'OK PICK_BLUE'
        assert resp[1] == 'OK DROP'
        assert state.crane_arm_closed is False


class TestImuStream:
    def test_start_stop(self, state):
        from picarpro.functions.imu_stream import ImuUdpStreamer
        s = ImuUdpStreamer(state)
        s.start()
        time.sleep(0.1)
        s.stop()

    def test_no_subscriber_no_crash(self, state):
        """Без подключённого хоста стример молчит и не падает."""
        from picarpro.functions.imu_stream import ImuUdpStreamer
        s = ImuUdpStreamer(state)
        s.start()
        time.sleep(0.2)
        assert s._running
        s.stop()
        assert not s._running


class TestObstaclePusher:
    def test_start_without_ultrasonic_fails(self, state):
        from picarpro.functions.arm_push import ObstaclePusher
        p = ObstaclePusher(state)
        state.ultrasonic = None
        ok, msg = p.start()
        assert ok is False
