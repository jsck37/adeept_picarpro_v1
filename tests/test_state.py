"""Тесты SharedState: инициализация, статус, корректное выключение."""
import pytest


class TestInit:
    def test_all_controllers_created(self, clean_state):
        s = clean_state
        for name in ('motors', 'servos', 'switches', 'leds', 'buzzer',
                     'ultrasonic', 'mpu6050', 'oled', 'ds4', 'voltage',
                     'autonomous', 'voice', 'steady'):
            assert getattr(s, name) is not None, f'{name} не создан'

    def test_defaults(self, clean_state):
        s = clean_state
        assert s.speed == 50
        assert s.running is True
        assert s.crane_grip_position == 'high'
        assert s.crane_arm_closed is False


class TestStatus:
    def test_full_shape(self, clean_state, monkeypatch):
        s = clean_state
        monkeypatch.setattr('picarpro.state.SystemInfo.get_all', staticmethod(
            lambda: {'cpu_temp': 45.0, 'cpu_usage': 10.0,
                     'ram': {'total_mb': 1024, 'used_mb': 200, 'percent': 20.0},
                     'low_voltage': False}))
        st = s.get_status()
        # базовые поля jsck37
        assert st['cpu_temp'] == 45.0
        assert st['speed'] == 50
        # новые поля слияния
        assert 'battery' in st
        assert 'steady_active' in st
        assert st['hw']['battery_adc'] is False
        # статус DS4
        assert st['ds4']['connected'] is False
        # серво-статус
        assert len(st['servo_angles']) == 7
        assert st['crane_grip_position'] == 'high'

    def test_hw_flags(self, clean_state):
        hw = clean_state.get_status()['hw']
        for key in ('motors', 'servos', 'leds', 'buzzer', 'switches',
                    'ultrasonic', 'mpu6050', 'oled', 'crane', 'autonomous'):
            assert key in hw, f'нет hw.{key}'
            assert hw[key] is True, f'hw.{key} не определился на фейках'
        # голосовое управление отключено без бинарника sherpa — это норма
        assert 'voice' in hw and hw['voice'] is False


class TestCamera:
    def test_init_camera(self, clean_state):
        from picarpro.camera.camera_opencv import Camera
        Camera._instance = None
        clean_state.init_camera()
        assert clean_state.camera is not None
        clean_state.camera.shutdown()
        clean_state.camera = None
        Camera._instance = None


class TestShutdown:
    def test_shutdown_flips_running(self, clean_state):
        s = clean_state
        assert s.running is True
        s.shutdown()
        assert s.running is False

    def test_shutdown_all_modules(self, clean_state):
        s = clean_state
        s.shutdown()
        assert s.motors._initialized is True  # контроллеры живы, но остановлены
        assert s.ultrasonic._thread.is_alive() is False
        assert s.leds._thread.is_alive() is False


class TestStateThreadSafety:
    def test_ws_clients_set_operations(self, clean_state):
        """set add/discard из разных потоков не роняют broadcast."""
        s = clean_state
        fake_ws = object()
        s.ws_clients.add(fake_ws)
        s.ws_clients.discard(fake_ws)
        assert fake_ws not in s.ws_clients
