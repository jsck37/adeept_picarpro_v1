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
        """v1.6.4: L2/R2 = скорость 100 %, но только при отклонённом стике."""
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._l2 = 1.0
        ds4._ly = 0.3
        ds4._apply_turbo()
        assert ds4._turbo_active is True
        ds4._apply_move()
        assert hw.motors._speed == 100

    def test_turbo_without_stick_does_not_move(self, ds4, hw, fake_gpio):
        """Раньше L2/R2 сами запускали движение вперёд — теперь нет: стик в
        нейтрали -> робот стоит (без движения вперёд)."""
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._l2 = 1.0
        ds4._lx = 0.0
        ds4._ly = 0.0
        ds4._apply_turbo()
        ds4._apply_move()
        # моторы остановлены: ШИМ обоих бортов обнулён (MOTOR_A_EN=4, B=17)
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        assert fake_gpio.pwm_for(17).duty_cycle == 0

    def test_triggers(self, ds4):
        assert ds4._norm_trigger(0, 2) == 0.0
        assert ds4._norm_trigger(255, 2) == 1.0


class TestButtons:
    def test_crane_arm_toggle(self, ds4, hw):
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._connected = True  # без «подключения» поток сглаживания не активен
        from config import SERVO_CRANE_ARM, CRANE_ARM_CLOSED, CRANE_ARM_OPEN
        ds4._btn_press(FakeEcodes.BTN_SOUTH)
        assert ds4._crane_arm_closed is True
        # плавный ход асинхронный — ждём достижения цели
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if hw.servos.get_angle(SERVO_CRANE_ARM) == CRANE_ARM_CLOSED:
                break
            time.sleep(0.02)
        assert hw.servos.get_angle(SERVO_CRANE_ARM) == CRANE_ARM_CLOSED
        ds4._btn_press(FakeEcodes.BTN_SOUTH)
        assert ds4._crane_arm_closed is False
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if hw.servos.get_angle(SERVO_CRANE_ARM) == CRANE_ARM_OPEN:
                break
            time.sleep(0.02)
        assert hw.servos.get_angle(SERVO_CRANE_ARM) == CRANE_ARM_OPEN
        ds4._connected = False

    def test_crane_grip_cycle(self, ds4, hw):
        """v1.6.4: осталось low/mid (high удалён); цикл mid -> low -> mid."""
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        assert ds4._crane_grip_index == 1  # mid (старт)
        ds4._btn_press(FakeEcodes.BTN_EAST)
        assert ds4._crane_grip_index == 0  # low
        assert hw.crane_grip_position == 'low'

    def test_removed_modes_are_noops(self, ds4, hw):
        """v1.6.4: △/□ (CV line/hand track), Options/Share (police) и PS
        (home all servos) больше ничего не делают."""
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        hw.servos.set_angle(1, 30)
        for code in (FakeEcodes.BTN_MODE, FakeEcodes.BTN_START,
                     FakeEcodes.BTN_SELECT, FakeEcodes.BTN_NORTH,
                     FakeEcodes.BTN_WEST):
            ds4._btn_press(code)          # не должно падать и что-то двигать
        assert hw.servos.get_angle(1) == 30          # PS не сделал move_init
        assert ds4.get_status().get('police_turbo') is None
        assert not hw.autonomy_active()

    def test_headlight_toggle(self, ds4, hw):
        from tests.fakes.all_fakes import FakeEcodes
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)
        ds4._btn_press(FakeEcodes.BTN_TL)
        # side lights on
        assert hw.switches.get_state(0) or hw.switches.get_state(1)


class TestCameraStick:
    """v1.6.1: инверсия камеры убрана; v1.6.4: шаг ровно 5° (DS4_CAM_STEP_DEG)
    не чаще DS4_CAM_STEP_INTERVAL_S. Спецификация владельца робота:
    стик вправо = камера вправо = pan МЕНЬШЕ (как стрелка вправо);
    стик вверх = камера вверх = tilt больше (как стрелка вверх)."""

    def _start(self, ds4, hw):
        ds4.start(motors=hw.motors, servos=hw.servos, leds=hw.leds,
                  buzzer=hw.buzzer, switches=hw.switches, shared_state=hw)

    def test_step_is_exactly_5_deg(self, ds4, hw):
        self._start(ds4, hw)
        hw.servos.set_angle(1, 90)
        ds4._rx, ds4._ry = 1.0, 0.0
        ds4._apply_pan_tilt()
        assert hw.servos.get_angle(1) == 85     # ровно 5°, не 6 и не «улетела»

    def test_rate_limit_no_flyaway(self, ds4, hw):
        """Десятки событий в секунду не должны гонять камеру: пока не прошло
        DS4_CAM_STEP_INTERVAL_S, второй вызов угол не меняет."""
        from config import DS4_CAM_STEP_INTERVAL_S
        self._start(ds4, hw)
        hw.servos.set_angle(1, 90)
        ds4._rx, ds4._ry = 1.0, 0.0
        ds4._apply_pan_tilt()
        ds4._apply_pan_tilt()
        ds4._apply_pan_tilt()
        assert hw.servos.get_angle(1) == 85
        time.sleep(DS4_CAM_STEP_INTERVAL_S + 0.05)
        ds4._apply_pan_tilt()
        assert hw.servos.get_angle(1) == 80

    def test_pan_right_decreases(self, ds4, hw):
        self._start(ds4, hw)
        hw.servos.set_angle(1, 90)
        ds4._rx, ds4._ry = 1.0, 0.0
        ds4._apply_pan_tilt()
        assert hw.servos.get_angle(1) < 90

    def test_pan_left_increases(self, ds4, hw):
        self._start(ds4, hw)
        hw.servos.set_angle(1, 90)
        ds4._rx, ds4._ry = -1.0, 0.0
        ds4._apply_pan_tilt()
        assert hw.servos.get_angle(1) > 90

    def test_tilt_up_increases(self, ds4, hw):
        self._start(ds4, hw)
        hw.servos.set_angle(2, 90)
        ds4._rx, ds4._ry = 0.0, 1.0   # +1 = стик вверх (после инверсии оси)
        ds4._apply_pan_tilt()
        assert hw.servos.get_angle(2) > 90

    def test_tilt_down_decreases(self, ds4, hw):
        self._start(ds4, hw)
        hw.servos.set_angle(2, 90)
        ds4._rx, ds4._ry = 0.0, -1.0
        ds4._apply_pan_tilt()
        assert hw.servos.get_angle(2) < 90


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
