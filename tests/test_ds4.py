"""Тесты геймпада DS4: нормализация осей, deadzone, турбо, кран, поворотники."""
import time

import pytest


@pytest.fixture
def ds4():
    from picarpro.hardware.ds4 import DS4Controller
    d = DS4Controller()
    yield d
    d.shutdown()


@pytest.fixture
def hw(clean_state):
    """Набор фейкового железа из clean_state."""
    return clean_state


class TestAxes:
    def test_norm_axis_center(self, ds4):
        assert ds4._norm_axis(128, 0) == pytest.approx(0.0, abs=0.05)

    def test_norm_axis_full(self, ds4):
        v = ds4._norm_axis(255, 0)
        assert v == pytest.approx(1.0, abs=0.05)
        v = ds4._norm_axis(0, 0)
        assert v == pytest.approx(-1.0, abs=0.05)

    def test_deadzone(self, ds4):
        """Малые отклонения в deadzone дают ровный 0."""
        assert ds4._norm_axis(128 + 5, 0) == 0.0

    def test_connect_and_axis(self, ds4, hw):
        from tests.fakes.all_fakes import FakeInputDevice, FakeEcodes
        dev = FakeInputDevice()
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._connect(dev)
        assert ds4.connected
        # полный вперёд
        ds4._on_axis(FakeEcodes.ABS_Y, 0)   # вверх = -1 после инверсии
        ds4._on_axis(FakeEcodes.ABS_X, 128)
        assert hw.motors._speed > 0
        ds4._disconnect()

    def test_neutral_stops(self, ds4, hw, fake_gpio):
        from tests.fakes.all_fakes import FakeInputDevice, FakeEcodes
        dev = FakeInputDevice()
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._connect(dev)
        ds4._on_axis(FakeEcodes.ABS_Y, 0)
        ds4._on_axis(FakeEcodes.ABS_X, 128)
        # нейтраль -> стоп: скважность обнулена
        ds4._ly = 0.0
        ds4._lx = 0.0
        ds4._apply_move()
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        ds4._disconnect()


class TestTurbo:
    def test_turbo_full_speed(self, ds4, hw):
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._l2 = 1.0
        ds4._ly = 0.3
        ds4._apply_turbo()
        assert ds4._turbo_active is True
        ds4._apply_move()
        assert hw.motors._speed == 100

    def test_triggers(self, ds4):
        assert ds4._norm_trigger(0, 2) == 0.0
        assert ds4._norm_trigger(255, 2) == 1.0


class TestButtons:
    def test_crane_arm_toggle(self, ds4, hw):
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._connected = True  # без «подключения» поток сглаживания не активен
        ds4._btn_press(FakeEcodes.BTN_SOUTH)
        assert ds4._crane_arm_closed is True
        # плавный ход асинхронный — ждём достижения цели
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if hw.servos.get_angle(6) == 150:  # CRANE_ARM_CLOSED
                break
            time.sleep(0.02)
        assert hw.servos.get_angle(6) == 150
        ds4._btn_press(FakeEcodes.BTN_SOUTH)
        assert ds4._crane_arm_closed is False
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if hw.servos.get_angle(6) == 100:  # CRANE_ARM_OPEN (v1.3.0)
                break
            time.sleep(0.02)
        assert hw.servos.get_angle(6) == 100
        ds4._connected = False

    def test_crane_grip_cycle(self, ds4, hw):
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._crane_grip_index = 2  # high
        ds4._btn_press(FakeEcodes.BTN_EAST)
        assert ds4._crane_grip_index == 1  # mid
        assert hw.crane_grip_position == 'mid'

    def test_headlight_toggle(self, ds4, hw):
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._btn_press(FakeEcodes.BTN_TL)
        # side lights on
        assert hw.switches.get_state(0) or hw.switches.get_state(1)

    def test_btn_mode_homes(self, ds4, hw):
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        hw.servos.set_angle(1, 30)
        ds4._btn_press(FakeEcodes.BTN_MODE)
        assert ds4._crane_grip_index == 2


class TestSmoothCrane:
    def test_no_thread_flood(self, ds4, hw):
        """Частые нажатия не плодят потоки сглаживания (фикс слияния)."""
        import threading
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._connected = True
        before = threading.active_count()
        for target in (100, 140, 180):
            ds4._smooth_crane(6, target)
        time.sleep(0.15)
        spawned = threading.active_count() - before
        assert spawned <= 1, f'наплодилось потоков: {spawned}'
        ds4._connected = False


class TestStatus:
    def test_get_status_shape(self, ds4):
        st = ds4.get_status()
        assert 'connected' in st and 'speed' in st and 'crane_grip' in st
