"""Тесты автономных режимов (radar, mazeExit, trackLine, trackHand, findColor,
watchDog) и контроллера стабилизации камеры (steady_camera из v2).

v1.6.10: режимы automatic / keepDistance / obstacleGrab / mazeU удалены,
радар переписан, добавлены mazeExit и trackHand через YOLO-pose."""
import time
import types

import numpy as np
import pytest

from tests.test_v150 import wait_for

class TestGuards:
    def test_no_ultrasonic_refuses(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ok, msg = ctrl.start('mazeExit')
        assert ok is False
        assert 'Ultrasonic' in msg
        ok, msg = ctrl.start('radarScan')
        assert ok is False and 'Ultrasonic' in msg

    @pytest.mark.parametrize('mode', ['automatic', 'keepDistance',
                                      'obstacleGrab', 'mazeU'])
    def test_removed_modes_refuse(self, fake_gpio, mode):
        """v1.6.10: Auto Drive / Distance / Grab / Maze U удалены."""
        from picarpro.functions.autonomous import AutonomousController, ALL_MODES
        assert mode not in ALL_MODES
        ctrl = AutonomousController(None, None, None)
        ok, msg = ctrl.start(mode)
        assert ok is False and 'Unknown mode' in msg

    def test_no_camera_refuses(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ok, msg = ctrl.start('trackLineCV')
        assert ok is False
        assert 'Camera' in msg

    def test_unknown_mode_refuses(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ok, msg = ctrl.start('dance')
        assert ok is False

    def test_shutdown_clean(self, fake_gpio):
        from picarpro.functions.autonomous import AutonomousController
        ctrl = AutonomousController(None, None, None)
        ctrl.shutdown()
        assert ctrl.is_active() is False


class TestRadar:
    def test_radar_scan_produces_data(self, clean_state, monkeypatch):
        """Скан непрерывный (крутится, пока режим не остановят), точки по углам
        -75..75 с шагом 7.5° (21 точка), у точки есть clear/age; после остановки
        датчик возвращается в центр."""
        import picarpro.functions.radar as radar_mod
        monkeypatch.setattr(radar_mod, 'RADAR_SETTLE_BASE_S', 0.0)
        monkeypatch.setattr(radar_mod, 'RADAR_SETTLE_PER_DEG_S', 0.0)
        state = clean_state
        state.init_camera()  # камера не обязательна для радара
        ctrl = state.autonomous
        ctrl._camera = None
        ok, _ = ctrl.start('radarScan')
        assert ok is True
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if len(ctrl.get_radar_data()) >= 21:
                break
            time.sleep(0.1)
        ctrl.stop()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and (
                ctrl.is_active() or state.servos.get_angle(1) != 90):
            time.sleep(0.05)
        assert not ctrl.is_active()
        data = ctrl.get_radar_data()
        assert len(data) == 21
        assert data[0]['angle'] == -75 and data[-1]['angle'] == 75
        for p in data:
            assert set(p) >= {'angle', 'distance', 'clear', 'age'}
            assert p['distance'] > 0
        # датчик (панорама) вернулся в центр, руль прямо
        assert state.servos.get_angle(1) == 90
        assert state.servos.get_angle(0) == 90


class _FakeServos:
    """Подмена серво: помнит угол панорамы (смещение от центра)."""

    def __init__(self):
        self.angle = 0
        self.moves = []

    def move_angle(self, sid, off):
        self.angle = off
        self.moves.append(off)

    def get_angle(self, sid):
        return 90 + self.angle

    def set_angle(self, sid, angle):
        pass

    def get_limits(self, sid=None):
        return {'min': 0, 'max': 180}


class _ScriptUltra:
    """Подмена HC-SR04: дистанция — функция угла панорамы (None = нет эха)."""
    _initialized = True

    def __init__(self, servos, fn):
        self.servos = servos
        self.fn = fn
        self.held = 0

    def measure_median(self, *a, **k):
        d = self.fn(self.servos.angle)
        return (d, 3 if d is not None else 0)

    def measure_now(self, attempts=2):
        return self.fn(0)

    def get_distance_cm(self):
        return self.fn(0)

    def hold(self):
        self.held += 1

    def unhold(self):
        self.held -= 1

    def acquire(self):
        pass

    def release(self):
        pass


@pytest.fixture
def fast_radar(monkeypatch):
    import picarpro.functions.radar as radar_mod
    monkeypatch.setattr(radar_mod, 'RADAR_SETTLE_BASE_S', 0.0)
    monkeypatch.setattr(radar_mod, 'RADAR_SETTLE_PER_DEG_S', 0.0)
    return radar_mod


class TestRadarModule:
    def _radar(self, fn):
        from picarpro.functions.radar import Radar
        sv = _FakeServos()
        us = _ScriptUltra(sv, fn)
        return Radar(sv, us), sv, us

    def test_scan_angles(self):
        from picarpro.functions.radar import scan_angles
        a = scan_angles()
        assert len(a) == 21 and a[0] == -75 and a[-1] == 75
        assert scan_angles(ascending=False)[0] == 75
        assert scan_angles(60, 15) == [-60, -45, -30, -15, 0, 15, 30, 45, 60]

    def test_sweep_fills_points_sorted(self, fast_radar):
        r, sv, us = self._radar(lambda a: 100.0 - abs(a))
        assert r.sweep(lambda s: False, lambda: True) is True
        snap = r.snapshot()
        assert [p['angle'] for p in snap] == sorted(p['angle'] for p in snap)
        assert snap[0]['distance'] == 25.0 and snap[len(snap) // 2]['distance'] == 100.0
        assert all(p['clear'] is False and p['age'] >= 0 for p in snap)

    def test_no_echo_is_clear_not_stale_value(self, fast_radar):
        """«Нет эха» -> clear=True и 200 см. Раньше подставлялось ПОСЛЕДНЕЕ
        значение датчика — с другого угла («призраки» на карте)."""
        r, sv, us = self._radar(lambda a: None if a == 30 else 40.0)
        r.sweep(lambda s: False, lambda: True)
        pts = {p['angle']: p for p in r.snapshot()}
        assert pts[30]['clear'] is True and pts[30]['distance'] == 200.0
        assert pts[0]['clear'] is False and pts[0]['distance'] == 40.0

    def test_smoothing_close_values_average_big_jump_replaces(self, fast_radar):
        vals = {'d': 100.0}
        r, sv, us = self._radar(lambda a: vals['d'])
        r.point_at(0, lambda s: False)
        vals['d'] = 110.0                       # близко -> сглаживается
        r.point_at(0, lambda s: False)
        assert r.point_distance(0) == 105.0
        vals['d'] = 30.0                        # скачок -> новое значение, без инерции
        r.point_at(0, lambda s: False)
        assert r.point_distance(0) == 30.0

    def test_sector_min_and_clear_fraction(self, fast_radar):
        r, sv, us = self._radar(lambda a: 30.0 if abs(a) <= 15 else 180.0)
        r.sweep(lambda s: False, lambda: True)
        assert r.sector_min(-15, 15) == 30.0
        assert r.sector_min(30, 75) == 180.0
        assert 0.6 < r.clear_fraction(150) < 0.8        # 15 из 21 точек дальше 150

    def test_settle_time_proportional_to_travel(self):
        from picarpro.functions import radar as rm
        r, sv, us = self._radar(lambda a: 50.0)
        r._angle = 0.0
        assert r.settle_time(60) > r.settle_time(5) > rm.RADAR_SETTLE_BASE_S - 1e-9

    def test_stop_aborts_sweep(self, fast_radar):
        r, sv, us = self._radar(lambda a: 50.0)
        n = {'k': 0}

        def alive():
            n['k'] += 1
            return n['k'] < 4
        assert r.sweep(lambda s: False, alive) is False
        assert len(r.snapshot()) == 3

    def test_hold_unhold_delegates_to_sensor(self):
        r, sv, us = self._radar(lambda a: 50.0)
        r.hold()
        assert us.held == 1
        r.unhold()
        assert us.held == 0


class TestMazeDecision:
    """Правило стены по карте радара (углы: влево > 0)."""

    def _ctrl(self, fn, fast_radar):
        from picarpro.functions.autonomous import AutonomousController
        sv = _FakeServos()
        ctrl = AutonomousController(None, sv, _ScriptUltra(sv, fn))
        ctrl._radar.sweep(lambda s: False, lambda: True, step=15)
        return ctrl

    def test_left_open_turns_left_first(self, fast_radar):
        ctrl = self._ctrl(lambda a: 200.0 if a >= 45 else 30.0, fast_radar)
        action, ang, info = ctrl._maze_decide()
        assert action == 'turn_primary' and ang > 0       # влево
        ctrl.shutdown()

    def test_straight_when_left_wall_and_front_clear(self, fast_radar):
        ctrl = self._ctrl(lambda a: 35.0 if abs(a) >= 45 else 120.0, fast_radar)
        action, ang, info = ctrl._maze_decide()
        assert action == 'forward'
        ctrl.shutdown()

    def test_right_open_when_front_and_left_blocked(self, fast_radar):
        ctrl = self._ctrl(lambda a: 200.0 if a <= -45 else 25.0, fast_radar)
        action, ang, info = ctrl._maze_decide()
        assert action == 'turn_secondary' and ang < 0     # вправо
        ctrl.shutdown()

    def test_dead_end(self, fast_radar):
        ctrl = self._ctrl(lambda a: 20.0, fast_radar)
        assert ctrl._maze_decide()[0] == 'dead_end'
        ctrl.shutdown()

    def test_right_hand_rule_mirrors(self, fast_radar, monkeypatch):
        import picarpro.functions.autonomous as am
        monkeypatch.setattr(am, 'MAZE_HAND', 'right')
        ctrl = self._ctrl(lambda a: 200.0 if a <= -45 else 30.0, fast_radar)
        action, ang, info = ctrl._maze_decide()
        assert action == 'turn_primary' and ang < 0       # ведущая — правая
        ctrl.shutdown()


class _SpyMotors:
    _initialized = True

    def __init__(self):
        self.log = []

    def move(self, speed=None, direction='forward', turn='no', radius=0.5):
        self.log.append(('move', direction, speed))

    def move_differential(self, speed, direction, l, r):
        self.log.append(('diff', direction, speed))

    def spin(self, speed=None, turn='left'):
        self.log.append(('spin', turn))

    def stop(self):
        self.log.append(('stop',))


class TestMazeExitMode:
    def test_open_space_finishes_by_itself(self, fast_radar):
        """Простор во всех направлениях в двух сканах подряд = выход найден:
        режим сам завершается, моторы стоят."""
        from picarpro.functions.autonomous import AutonomousController
        sv, mo = _FakeServos(), _SpyMotors()
        ctrl = AutonomousController(mo, sv, _ScriptUltra(sv, lambda a: None))
        ok, _ = ctrl.start('mazeExit')
        assert ok
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and ctrl.is_active():
            time.sleep(0.05)
        assert not ctrl.is_active()
        assert mo.log[-1] == ('stop',)
        assert sv.moves[-1] == 0                           # датчик вернули вперёд
        ctrl.shutdown()

    def test_status_info_has_decision(self, fast_radar):
        from picarpro.functions.autonomous import AutonomousController
        sv, mo = _FakeServos(), _SpyMotors()
        ctrl = AutonomousController(mo, sv, _ScriptUltra(sv, lambda a: 25.0))
        ctrl.start('mazeExit')
        deadline = time.monotonic() + 5.0
        seen = None
        while time.monotonic() < deadline:
            info = ctrl.get_auto_info()
            if info.get('state') in ('dead_end', 'forward', 'turn_primary',
                                     'turn_secondary'):
                seen = info
                break
            time.sleep(0.02)
        ctrl.stop()
        assert seen is not None and seen['state'] == 'dead_end'
        assert 'front_cm' in seen
        ctrl.shutdown()


class TestTrackLineIR:
    def test_ir_follows_left(self, clean_state, fake_gpio):
        """Левый датчик на линии — правый борт быстрее (дифференциал)."""
        state = clean_state
        ctrl = state.autonomous
        ok, _ = ctrl.start('trackLine')
        assert ok is True
        fake_gpio.set_input(19, 0)   # LINE_LEFT: линия (active low)
        fake_gpio.set_input(16, 1)
        fake_gpio.set_input(20, 1)
        time.sleep(0.4)
        pwm_a = fake_gpio.pwm_for(4)   # A = правая сторона
        pwm_b = fake_gpio.pwm_for(17)  # B = левая сторона
        assert pwm_a.duty_cycle > pwm_b.duty_cycle
        ctrl.stop()

    def test_ir_follows_right(self, clean_state, fake_gpio):
        state = clean_state
        ctrl = state.autonomous
        ctrl.start('trackLine')
        fake_gpio.set_input(19, 1)
        fake_gpio.set_input(16, 1)
        fake_gpio.set_input(20, 0)   # правый на линии
        time.sleep(0.4)
        pwm_a = fake_gpio.pwm_for(4)
        pwm_b = fake_gpio.pwm_for(17)
        assert pwm_b.duty_cycle > pwm_a.duty_cycle
        ctrl.stop()

    def test_ir_center_goes_straight(self, clean_state, fake_gpio):
        state = clean_state
        ctrl = state.autonomous
        ctrl.start('trackLine')
        fake_gpio.set_input(19, 1)
        fake_gpio.set_input(16, 0)   # средний на линии
        fake_gpio.set_input(20, 1)
        time.sleep(0.4)
        pwm_a = fake_gpio.pwm_for(4)
        pwm_b = fake_gpio.pwm_for(17)
        assert pwm_a.duty_cycle == pytest.approx(pwm_b.duty_cycle)
        ctrl.stop()

    def test_ir_obstacle_guard_stops_before_wall(self, clean_state, fake_gpio):
        """v1.6.10: езда по линии останавливается перед препятствием
        (ультразвук смотрит вперёд, пока панорама камеры у центра)."""
        state = clean_state
        ctrl = state.autonomous
        state.ultrasonic._measure = lambda: 8.0   # препятствие в 8 см (фоновый опрос)
        fake_gpio.set_input(19, 1)
        fake_gpio.set_input(16, 0)                # линия под средним датчиком
        fake_gpio.set_input(20, 1)
        ctrl.start('trackLine')
        time.sleep(0.5)
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        assert fake_gpio.pwm_for(17).duty_cycle == 0
        assert ctrl.get_auto_info().get('state') == 'obstacle'
        ctrl.stop()

    def test_ir_lost_line_stops_instead_of_driving_blind(self, clean_state,
                                                         fake_gpio, monkeypatch):
        """Раньше при потере линии робот «полз вперёд» бесконечно."""
        import picarpro.functions.autonomous as am
        monkeypatch.setattr(am, 'LINE_LOST_SEARCH_S', 0.2)
        monkeypatch.setattr(am, 'LINE_LOST_STOP_S', 0.6)
        state = clean_state
        ctrl = state.autonomous
        fake_gpio.set_input(19, 0)                # линия слева ...
        fake_gpio.set_input(16, 1)
        fake_gpio.set_input(20, 1)
        ctrl.start('trackLine')
        time.sleep(0.2)
        fake_gpio.set_input(19, 1)                # ... и пропала
        assert wait_for(lambda: not ctrl.is_active(), 3.0), 'режим должен завершиться'
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        assert fake_gpio.pwm_for(17).duty_cycle == 0

    def test_ir_lost_line_searches_toward_last_side(self, clean_state,
                                                    fake_gpio, monkeypatch):
        import picarpro.functions.autonomous as am
        monkeypatch.setattr(am, 'LINE_LOST_SEARCH_S', 1.5)
        monkeypatch.setattr(am, 'LINE_LOST_STOP_S', 3.0)
        state = clean_state
        ctrl = state.autonomous
        fake_gpio.set_input(19, 1)
        fake_gpio.set_input(16, 1)
        fake_gpio.set_input(20, 0)                # линия ушла вправо
        ctrl.start('trackLine')
        time.sleep(0.25)
        fake_gpio.set_input(20, 1)                # потеряна
        time.sleep(0.4)
        # ищем дугой вправо: левый борт быстрее правого
        assert fake_gpio.pwm_for(17).duty_cycle > fake_gpio.pwm_for(4).duty_cycle
        ctrl.stop()

    def test_ir_values_api(self, clean_state, fake_gpio):
        """v1.6.2: get_ir_values возвращает семантику «над линией»
        (раньше отдавал сырой GPIO и UI показывал LINE на голом полу)."""
        state = clean_state
        fake_gpio.set_input(19, 0)   # над тёмной линией (active LOW)
        fake_gpio.set_input(16, 1)   # белый пол
        fake_gpio.set_input(20, 1)
        l, m, r = state.autonomous.get_ir_values()
        assert (l, m, r) == (True, False, False)


class _FakeCam:
    """Лёгкая подмена камеры для тестов цикла findColor/watchDog."""

    def __init__(self):
        self.cv_thread = types.SimpleNamespace(on_color_found=None,
                                               on_motion=None,
                                               on_hand_found=None,
                                               on_line_found=None)

    def set_cv_mode(self, mode):
        pass


class TestFindColor:
    def test_follows_color(self, clean_state):
        state = clean_state
        ctrl = state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        ok, _ = ctrl.start('findColor')
        assert ok
        # ждём, пока поток назначит колбэк
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if fc.cv_thread.on_color_found is not None:
                break
            time.sleep(0.02)
        # имитация обнаружения объекта слева от центра (центр 320)
        fc.cv_thread.on_color_found([100, 240], 5000)
        time.sleep(0.6)
        # объект слева -> руль влево (>90, как в STEER_MAP['left']=150)
        assert state.servos.get_angle(0) > 90
        ctrl.stop()

    def test_searches_when_lost(self, clean_state, fake_gpio):
        state = clean_state
        ctrl = state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        ok, _ = ctrl.start('findColor')
        assert ok
        time.sleep(0.5)
        # объект не найден — моторы стоят (двигатели не получают питание)
        assert fake_gpio.pwm_for(4).duty_cycle == 0
        ctrl.stop()


class TestWatchDogMode:
    def test_alarm_fires(self, clean_state, monkeypatch):
        state = clean_state
        ctrl = state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        alarms = []
        ctrl.set_alarm_callback(lambda px: alarms.append(px))
        ok, _ = ctrl.start('watchDog')
        assert ok
        # ждём, пока поток назначит колбэк
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if fc.cv_thread.on_motion is not None:
                break
            time.sleep(0.02)
        # подаём «движение» напрямую в on_motion (кулдаун в CVThread)
        assert fc.cv_thread.on_motion is not None
        fc.cv_thread.on_motion(5000)
        assert alarms
        ctrl.stop()


class TestSteadyCamera:
    def test_requires_mpu(self, clean_state, fake_gpio, monkeypatch):
        from picarpro.functions.steady_camera import SteadyCameraController
        ctrl = SteadyCameraController(clean_state.servos, None)
        ok, msg = ctrl.start()
        assert ok is False
        assert 'MPU6050' in msg
        ctrl.shutdown()

    def test_compensates_pitch(self, clean_state, fake_gpio, fake_i2c_devices):
        """Тангаж +10° -> наклон камеры компенсируется вверх."""
        from tests.fakes.all_fakes import set_mpu6050_sample
        from picarpro.functions.steady_camera import SteadyCameraController
        regs = fake_i2c_devices[0x68]
        # pitch = atan2(-ax, sqrt(ay^2+az^2)): ax=-2844, az=16134 -> ~+10°
        set_mpu6050_sample(regs, ax=-2844, ay=0, az=16134)
        clean_state.servos.set_angle(2, 90)
        ctrl = SteadyCameraController(clean_state.servos, clean_state.mpu6050)
        ok, _ = ctrl.start()
        assert ok
        deadline = time.monotonic() + 3.0
        tilt = 90
        while time.monotonic() < deadline:
            tilt = clean_state.servos.get_angle(2)
            if tilt > 90:
                break
            time.sleep(0.05)
        assert tilt > 90, f'компенсация не применилась (tilt={tilt})'
        ctrl.stop()
        time.sleep(0.2)
        assert clean_state.servos.get_angle(2) == 90
        ctrl.shutdown()
