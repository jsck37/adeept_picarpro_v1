"""v1.6.2: дуговые развороты (Аккерман вместо spin), сохранение записанного
маршрута с рулением (steer_*), бэкенд WS2812 на GPIO12 (rpi_ws281x),
полярность ИК-датчиков линии."""
import time

import pytest


def call(state, cmd, params=None):
    from picarpro.commands import process
    return process(state, {'cmd': cmd, 'params': params or {}}, 'test')


# ===========================================================================
# Дуговый разворот (drive.arc_turn) — миссии и объезд препятствий
# ===========================================================================

class TestArcTurn:
    def test_right_turn_steers_drives_stops(self, clean_state, fake_gpio,
                                            monkeypatch):
        """+90°: руль вправо до упора, ход вперёд, по завершении — стоп и
        руль прямо. Время — need/AUTO_ARC_RATE_DPS (гироскопа нет)."""
        from config import (MOTOR_A_EN, SERVO_STEERING, STEER_MAP,
                            AUTO_ARC_RATE_DPS)
        from picarpro.drive import arc_turn
        state = clean_state
        seen_servo, moves = {}, []
        real_set = state.servos.set_angle
        real_move = state.motors.move

        def rec_set(sid, ang):
            seen_servo.setdefault(sid, []).append(ang)
            return real_set(sid, ang)

        def rec_move(*a, **k):
            moves.append(a)
            return real_move(*a, **k)

        monkeypatch.setattr(state.servos, 'set_angle', rec_set)
        monkeypatch.setattr(state.motors, 'move', rec_move)
        waits = []
        arc_turn(state.motors, state.servos, +90,
                 alive=lambda: not waits, wait=waits.append,
                 heading=None)
        # [ход по времени, финальная пауза «успокоиться»]
        assert len(waits) == 2
        assert abs(waits[0] - 90 / AUTO_ARC_RATE_DPS) < 0.01
        assert waits[1] == 0.12
        assert STEER_MAP['right'] in seen_servo.get(SERVO_STEERING, [])
        assert 90 in seen_servo.get(SERVO_STEERING, [])       # руль прямо
        assert moves and moves[0][1] == 'forward'             # ехал вперёд
        assert fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle == 0  # остановлен

    def test_left_turn_steers_left(self, clean_state, fake_gpio):
        from config import SERVO_STEERING, STEER_MAP
        from picarpro.drive import arc_turn
        state = clean_state
        waits = []
        arc_turn(state.motors, state.servos, -90,
                 alive=lambda: not waits, wait=waits.append)
        assert state.servos.get_angle(SERVO_STEERING) == 90   # вернули центр
        # в процессе руль был влево: воспроизводим через второй вызов с
        # перехватом — проверяем константу напрямую
        assert STEER_MAP['left'] == 150

    def test_left_turn_mid_state(self, clean_state, monkeypatch):
        from config import SERVO_STEERING, STEER_MAP
        from picarpro.drive import arc_turn
        state = clean_state
        seen = []
        real_set = state.servos.set_angle

        def rec(sid, ang):
            seen.append(ang)
            return real_set(sid, ang)

        monkeypatch.setattr(state.servos, 'set_angle', rec)
        waits = []
        arc_turn(state.motors, state.servos, -45,
                 alive=lambda: not waits, wait=waits.append)
        assert STEER_MAP['left'] in seen

    def test_small_angle_is_noop(self, clean_state):
        from picarpro.drive import arc_turn
        state = clean_state
        waits = []
        arc_turn(state.motors, state.servos, 2,
                 alive=lambda: not waits, wait=waits.append)
        assert waits == []
        assert not state.motors.is_moving

    def test_gyro_loop_stops_at_target(self, clean_state, fake_gpio):
        """Гироскоп: дуга останавливается, когда |yaw| >= need - lead."""
        from picarpro.drive import arc_turn

        class _FakeHeading:
            available = True

            def __init__(self):
                self.accum = 0.0

            def begin(self):
                self.accum = 0.0

            def update(self):
                self.accum += 30.0          # каждый опрос +30°
                return self.accum

        hd = _FakeHeading()
        waits = []
        arc_turn(clean_state.motors, clean_state.servos, 90,
                 alive=lambda: len(waits) < 10, wait=waits.append, heading=hd)
        # lead = AUTO_ARC_RATE_DPS * 0.2 = 16; порог 90-16 = 74 ->
        # 3 опроса (30/60/90): 2 паузы wait(0.01) в цикле + финальная 0.12
        assert hd.accum >= 74
        assert waits == [0.01, 0.01, 0.12]
        assert not clean_state.motors.is_moving

    def test_alive_stop_interrupts(self, clean_state, fake_gpio):
        """Остановка миссии посреди дуги: моторы остановлены, руль прямо."""
        from config import SERVO_STEERING
        from picarpro.drive import arc_turn
        waits = []
        arc_turn(clean_state.motors, clean_state.servos, 90,
                 alive=lambda: len(waits) >= 1, wait=waits.append)
        assert not clean_state.motors.is_moving
        assert clean_state.servos.get_angle(SERVO_STEERING) == 90


# ===========================================================================
# Сохранение записанного маршрута с рулением (A/D)
# ===========================================================================

class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def time(self):
        return self.t


class TestSteerRoute:
    def test_validate_accepts_steer(self):
        from picarpro.functions.missions import validate_steps
        steps = validate_steps([
            {'op': 'drive', 'dir': 'steer_left', 'sec': 0.5},
            {'op': 'drive', 'dir': 'forward', 'speed': 40, 'sec': 1.0},
            {'op': 'drive', 'dir': 'steer_right', 'sec': 0.5},
        ])
        assert [s['dir'] for s in steps] == \
            ['steer_left', 'forward', 'steer_right']

    def test_recorded_route_with_steer_saves(self, clean_state, tmp_path,
                                             monkeypatch):
        """Водитель крутил руль A/D во время записи — раньше save падал
        («bad dir: steer_left») и маршрут не сохранялся (v1.6.2)."""
        from picarpro.functions import missions as missions_mod
        clock = _FakeClock()
        monkeypatch.setattr(missions_mod, 'time', clock)
        mr = missions_mod.MissionRunner(clean_state,
                                        directory=tmp_path / 'm')
        mr.record_start()
        clock.t += 0.2
        mr.note_move('steer_left', 50)
        clock.t += 1.0
        mr.note_move('forward', 50)
        clock.t += 1.0
        steps = mr.record_stop()
        assert steps and [s['dir'] for s in steps] == \
            ['steer_left', 'forward']
        mr.save('my_route', steps)      # MissionError раньше
        assert (tmp_path / 'm' / 'my_route.json').exists()

    def test_return_home_filters_steer(self, clean_state):
        """Руль без хода не смещает робота — в «вернуться домой» его нет."""
        mr = clean_state.missions
        mr._last_path = [
            {'op': 'drive', 'dir': 'steer_left', 'speed': 50, 'sec': 0.5},
            {'op': 'drive', 'dir': 'forward', 'speed': 50, 'sec': 1.0},
            {'op': 'drive', 'dir': 'backward_right', 'speed': 50, 'sec': 0.8},
        ]
        steps = mr.return_home_steps()
        # steer_* отфильтрован; reverse: forward->backward, backward_right->forward_right
        assert [s['dir'] for s in steps] == \
            ['forward_right', 'backward']

    def test_record_stop_command_accepts_steer(self, clean_state, tmp_path,
                                               monkeypatch):
        """Командный слой: record_stop с save_as сохраняет маршрут с рулением."""
        from picarpro.functions import missions as missions_mod
        clock = _FakeClock()
        monkeypatch.setattr(missions_mod, 'time', clock)
        state = clean_state
        state.missions = missions_mod.MissionRunner(
            state, directory=tmp_path / 'm')
        call(state, 'mission', {'action': 'record_start'})
        clock.t += 0.2
        call(state, 'move', {'dir': 'steer_right'})
        clock.t += 1.0
        call(state, 'move', {'dir': 'backward'})
        clock.t += 1.0
        r = call(state, 'mission',
                 {'action': 'record_stop', 'save_as': 'route2'})
        assert r['ok'] is True and r['saved'] is True
        assert (tmp_path / 'm' / 'route2.json').exists()


# ===========================================================================
# WS2812: бэкенд rpi_ws281x на GPIO12 (проводка оригинала Adeept)
# ===========================================================================

class _FakeColor:
    def __init__(self, r, g, b):
        self.r, self.g, self.b = r, g, b


class _FakeStrip:
    init_args = None
    calls = None
    shown = False

    def __init__(self, count, pin, freq, dma, invert, brightness, channel):
        _FakeStrip.init_args = (count, pin, freq, dma, channel)

    def begin(self):
        pass

    def setPixelColor(self, i, c):
        _FakeStrip.calls.append((i, c))

    def show(self):
        _FakeStrip.shown = True


class TestLEDBackend:
    def test_pwm_backend_gpio12(self, monkeypatch):
        """LED_BACKEND='pwm': лента через rpi_ws281x на GPIO12, канал 0
        (как в RobotLight.py оригинала); цвета идут с масштабом яркости."""
        import picarpro.hardware.leds_ws2812 as leds_mod
        _FakeStrip.init_args = None
        _FakeStrip.calls = []
        _FakeStrip.shown = False
        monkeypatch.setattr(leds_mod, '_HAS_WS281X', True)
        monkeypatch.setattr(leds_mod, '_PixelStrip', _FakeStrip)
        monkeypatch.setattr(leds_mod, '_WSColor', _FakeColor)
        monkeypatch.setattr(leds_mod, 'LED_BACKEND', 'pwm')
        ctrl = leds_mod.LEDController()
        assert ctrl._initialized and ctrl._backend == 'pwm'
        count, pin, freq, dma, channel = _FakeStrip.init_args
        assert (pin, channel) == (12, 0)         # GPIO12, PWM0-канал 0
        assert freq == 800_000 and dma == 10
        ctrl.fill(100, 50, 10)
        assert _FakeStrip.shown
        i0, c0 = _FakeStrip.calls[0]
        assert i0 == 0
        # масштаб LED_BRIGHTNESS=200 из 255
        assert (c0.r, c0.g, c0.b) == (int(100 * 200 / 255),
                                      int(50 * 200 / 255),
                                      int(10 * 200 / 255))
        ctrl.shutdown()

    def test_auto_falls_back_to_spi_without_ws281x(self, monkeypatch):
        """rpi_ws281x не установлен -> auto выбирает SPI (GPIO10-фолбэк)."""
        import picarpro.hardware.leds_ws2812 as leds_mod
        monkeypatch.setattr(leds_mod, '_HAS_WS281X', False)
        monkeypatch.setattr(leds_mod, 'LED_BACKEND', 'auto')
        ctrl = leds_mod.LEDController()
        assert ctrl._backend == 'spi'
        # тайминги: запрошенная частота SPI 2.4 МГц (не 8, как в v1.6.1)
        assert ctrl._spi.max_speed_hz == 2_400_000
        ctrl.shutdown()


# ===========================================================================
# ИК-датчики: флаг полярности
# ===========================================================================

class TestIRPolarity:
    def test_default_active_low(self, clean_state, fake_gpio):
        """LINE_ACTIVE_LOW=True (по умолчанию): сырой 0 = линия."""
        fake_gpio.set_input(16, 0)
        assert clean_state.autonomous._ir_line(16) is True
        fake_gpio.set_input(16, 1)
        assert clean_state.autonomous._ir_line(16) is False

    def test_flag_inverts(self, clean_state, fake_gpio, monkeypatch):
        import picarpro.functions.autonomous as auto_mod
        monkeypatch.setattr(auto_mod, 'LINE_ACTIVE_LOW', False)
        fake_gpio.set_input(16, 1)
        assert clean_state.autonomous._ir_line(16) is True
        fake_gpio.set_input(16, 0)
        assert clean_state.autonomous._ir_line(16) is False


# ===========================================================================
# Встроенные миссии
# ===========================================================================

class TestBuiltins:
    def test_turn_show_replaced_spin_show(self):
        """Аккерман-шасси не крутится на месте: spin_show -> turn_show."""
        from picarpro.functions.missions import BUILTIN
        assert 'spin_show' not in BUILTIN
        assert 'turn_show' in BUILTIN
        ops = [s['op'] for s in BUILTIN['turn_show']]
        assert 'turn' in ops

    def test_turn_estimate_uses_arc_rate(self):
        from picarpro.functions.missions import estimate_seconds
        from config import AUTO_ARC_RATE_DPS
        sec = estimate_seconds([{'op': 'turn', 'deg': 90}])
        assert abs(sec - 90 / AUTO_ARC_RATE_DPS - 0.3) < 0.01
