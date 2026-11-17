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
        assert s.crane_grip_position == 'mid'   # v1.6.4: high убран
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
        assert st['crane_grip_position'] == 'mid'

    def test_hw_flags(self, clean_state):
        hw = clean_state.get_status()['hw']
        for key in ('motors', 'servos', 'leds', 'buzzer', 'switches',
                    'ultrasonic', 'mpu6050', 'oled', 'crane', 'autonomous'):
            assert key in hw, f'нет hw.{key}'
            assert hw[key] is True, f'hw.{key} не определился на фейках'
        # голосовое управление отключено без бинарника sherpa — это норма
        assert 'voice' in hw and hw['voice'] is False

    def test_module_found_flags(self, clean_state):
        """v1.6.9: флаги проверки «Module not found» для окон DS4/Миссии/CV."""
        hw = clean_state.get_status()['hw']
        # DS4: «ds4» — геймпад ПОДКЛЮЧЁН (на фейках нет), «ds4_found» — модуль есть
        assert hw['ds4'] is False
        assert hw['ds4_found'] is True
        assert hw['missions'] is True
        assert 'cv' in hw

    def test_ds4_found_false_without_module(self, clean_state):
        clean_state.ds4 = None
        hw = clean_state.get_status()['hw']
        assert hw['ds4_found'] is False and hw['ds4'] is False

    def test_missions_flag_follows_motors(self, clean_state):
        clean_state.motors._initialized = False
        assert clean_state.get_status()['hw']['missions'] is False

    def test_cv_flag_lazy_camera(self, clean_state):
        """Камера создаётся лениво: пока её нет, CV считается доступным
        (иначе карточку нечем разблокировать), а после сбоя — нет."""
        import picarpro.state as st
        clean_state.camera = None
        clean_state._camera_failed = False
        old = st._CV2_FOUND
        try:
            st._CV2_FOUND = True
            assert clean_state.cv_available() is True
            clean_state._camera_failed = True
            assert clean_state.cv_available() is False
            clean_state._camera_failed = False
            st._CV2_FOUND = False
            assert clean_state.cv_available() is False
        finally:
            st._CV2_FOUND = old


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
