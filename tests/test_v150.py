"""Регрессионные тесты v1.5.0: исправленные баги, маневренность, энергосбережение."""
import os
import socket
import stat
import threading
import time

import numpy as np
import pytest

from tests.test_commands import call


def wait_for(cond, timeout=3.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(step)
    return cond()


# ---------------------------------------------------------------------------
# Bluetooth: deadlock Lock -> RLock
# ---------------------------------------------------------------------------
class TestBluetoothDeadlock:
    def test_session_creation_does_not_hang(self, tmp_path, monkeypatch):
        fake = tmp_path / 'bluetoothctl'
        fake.write_text('#!/bin/sh\nwhile read l; do echo "[bluetooth]# $l"; done\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv('PATH', f'{tmp_path}:{os.environ["PATH"]}')
        from picarpro.routes import bluetooth as bt
        res = {}
        t = threading.Thread(
            target=lambda: res.setdefault('s', bt.BluetoothctlSession()),
            daemon=True)
        t.start()
        t.join(5)
        assert not t.is_alive(), 'создание сессии bluetoothctl зависло (deadlock)'


# ---------------------------------------------------------------------------
# Моторы: spin, потолок скорости, is_moving
# ---------------------------------------------------------------------------
class TestMotorsV150:
    def test_spin_opposite_sides(self, clean_state, fake_gpio):
        from config import (MOTOR_A_IN1, MOTOR_A_IN2, MOTOR_B_IN1, MOTOR_B_IN2,
                            MOTOR_A_EN, MOTOR_B_EN)
        m = clean_state.motors
        m.spin(50, 'left')
        # левый разворот: правый борт (A) вперёд, левый (B) назад
        assert fake_gpio.outputs[MOTOR_A_IN1] == 1 and fake_gpio.outputs[MOTOR_A_IN2] == 0
        assert fake_gpio.outputs[MOTOR_B_IN1] == 0 and fake_gpio.outputs[MOTOR_B_IN2] == 1
        assert fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle == pytest.approx(50)
        assert fake_gpio.pwm_for(MOTOR_B_EN).duty_cycle == pytest.approx(50)
        assert m.is_moving
        m.stop()
        assert not m.is_moving

    def test_max_speed_cap(self, clean_state, fake_gpio):
        from config import MOTOR_A_EN
        m = clean_state.motors
        m.max_speed = 40
        m.move(90, 'forward', 'no')
        assert fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle == pytest.approx(40)
        m.max_speed = 100


# ---------------------------------------------------------------------------
# drive(): единый разбор направлений
# ---------------------------------------------------------------------------
class TestDrive:
    def test_parse(self):
        from picarpro.drive import parse_direction
        assert parse_direction('forward') == ('forward', 'no')
        assert parse_direction('backward_left') == ('backward', 'left')
        assert parse_direction('left') == ('forward', 'left')
        assert parse_direction('forward_x') is None
        assert parse_direction('bogus') is None

    def test_bad_direction_rejected_not_turned_right(self, clean_state):
        r = call(clean_state, 'move', {'dir': 'forward_sideways'})
        assert r['ok'] is False
        assert not clean_state.motors.is_moving

    def test_spin_command(self, clean_state):
        r = call(clean_state, 'move', {'dir': 'spin_left'})
        assert r['ok'] is True and clean_state.motors.is_moving
        call(clean_state, 'move', {'dir': 'stop'})
        assert not clean_state.motors.is_moving

    def test_steer_only_does_not_move(self, clean_state):
        """v1.6.0: A/D (steer_left/steer_right) — только рулевое серво,
        моторы стоят (раньше 'left' включал дугу вперёд)."""
        from config import SERVO_STEERING, STEER_MAP
        r = call(clean_state, 'move', {'dir': 'steer_left'})
        assert r['ok'] is True
        assert not clean_state.motors.is_moving
        assert clean_state.servos.get_angle(SERVO_STEERING) == STEER_MAP['left']
        r = call(clean_state, 'move', {'dir': 'steer_right'})
        assert r['ok'] is True
        assert not clean_state.motors.is_moving
        assert clean_state.servos.get_angle(SERVO_STEERING) == STEER_MAP['right']

    def test_steer_stops_running_motors(self, clean_state):
        """Едем вперёд -> пришли steer_left: моторы остановлены, руль свёрнут."""
        from config import SERVO_STEERING, STEER_MAP
        call(clean_state, 'move', {'dir': 'forward'})
        assert clean_state.motors.is_moving
        call(clean_state, 'move', {'dir': 'steer_left'})
        assert not clean_state.motors.is_moving
        assert clean_state.servos.get_angle(SERVO_STEERING) == STEER_MAP['left']

    def test_arc_turns_still_move(self, clean_state):
        """Дуги для автономных/TCP не тронуты: left/right = движение+руль."""
        call(clean_state, 'move', {'dir': 'left'})
        assert clean_state.motors.is_moving
        call(clean_state, 'move', {'dir': 'stop'})
        call(clean_state, 'move', {'dir': 'forward_left'})
        assert clean_state.motors.is_moving
        call(clean_state, 'move', {'dir': 'stop'})


# ---------------------------------------------------------------------------
# web_active: захват руля истекает
# ---------------------------------------------------------------------------
class TestWebActiveTtl:
    def test_expires(self, monkeypatch):
        import picarpro.state as st
        monkeypatch.setattr(st, 'WEB_ACTIVE_TTL_S', 0.05)
        s = st.SharedState()
        s.web_active = True
        assert s.web_active is True
        time.sleep(0.1)
        assert s.web_active is False     # раньше оставалось True навсегда

    def test_ds4_regains_control(self, clean_state, monkeypatch):
        import picarpro.state as st
        monkeypatch.setattr(st, 'WEB_ACTIVE_TTL_S', 0.05)
        clean_state.touch_web()
        time.sleep(0.1)
        assert clean_state.web_active is False


# ---------------------------------------------------------------------------
# Ультразвук: «нет эха» = свободно, устаревшие данные = None
# ---------------------------------------------------------------------------
class TestUltrasonicV150:
    def test_no_echo_means_clear_after_misses(self, clean_state):
        u = clean_state.ultrasonic
        u._distance, u._distance_ts = 25.0, time.monotonic()
        u._store(None)
        u._store(None)
        assert u.get_last_distance() == 25.0       # одиночные промахи игнорируем
        u._store(None)
        from config import ULTRASONIC_MAX_DISTANCE
        assert u.get_last_distance() == pytest.approx(ULTRASONIC_MAX_DISTANCE * 100)  # 3-й подряд = чисто

    def test_stale_reading_is_unknown(self, clean_state):
        u = clean_state.ultrasonic
        u._distance, u._distance_ts = 50.0, time.monotonic() - 100
        assert u.get_distance_cm() is None

    def test_demand_changes_rate(self, clean_state):
        from config import ULTRASONIC_ACTIVE_INTERVAL_S, ULTRASONIC_IDLE_INTERVAL_S
        u = clean_state.ultrasonic
        assert u._interval() == ULTRASONIC_IDLE_INTERVAL_S
        u.acquire()
        assert u._interval() == ULTRASONIC_ACTIVE_INTERVAL_S
        u.release()
        assert u._interval() == ULTRASONIC_IDLE_INTERVAL_S


# ---------------------------------------------------------------------------
# Автономные режимы
# ---------------------------------------------------------------------------
class _RecMotors:
    """Мотор-заглушка, записывающая команды."""
    _initialized = True

    def __init__(self):
        self.calls = []
        self.is_moving = False

    def move(self, speed, direction, turn='no', radius=0.5):
        self.calls.append(('move', speed, direction, turn))
        self.is_moving = True

    def move_differential(self, speed, direction, left, right):
        self.calls.append(('diff', speed, direction, round(left, 2), round(right, 2)))
        self.is_moving = True

    def spin(self, speed, turn):
        self.calls.append(('spin', speed, turn))
        self.is_moving = True

    def stop(self):
        self.calls.append(('stop',))
        self.is_moving = False


class _StubUltra:
    _initialized = True

    def __init__(self, values):
        self.values = list(values)

    def acquire(self): pass
    def release(self): pass

    def measure_now(self):
        return self.values.pop(0) if self.values else 200.0

    def get_distance_cm(self):
        return 100.0

    def get_last_distance(self):
        return 100.0


class TestAutonomousV150:
    def test_fast_mode_switch_runs_only_new_mode(self, clean_state):
        ctrl = clean_state.autonomous
        clean_state.ultrasonic._distance = 100.0
        assert ctrl.start('keepDistance')[0]
        time.sleep(0.15)
        assert ctrl.start('radarScan')[0]
        assert wait_for(lambda: ctrl._current_mode == 'radarScan', 1.0)
        # старый режим не «воскрес»: после остановки активного режима нет
        ctrl.stop()
        assert wait_for(lambda: not ctrl.is_active(), 2.0)
        assert ctrl._ctx is None or not ctrl._ctx.alive

    def test_keep_distance_proportional(self, clean_state, fake_gpio):
        from config import MOTOR_A_EN
        ctrl = clean_state.autonomous
        reading = [30.0]                            # управляем «эхом» датчика
        clean_state.ultrasonic._measure = lambda: reading[0]
        ctrl.start('keepDistance')                  # цель 20 см, сейчас 30 -> вперёд
        assert wait_for(
            lambda: fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle > 0, 2.0)
        reading[0] = 20.0                           # на цели -> стоим
        assert wait_for(
            lambda: fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle == 0, 3.0)
        reading[0] = 10.0                           # слишком близко -> назад
        assert wait_for(
            lambda: fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle > 0, 3.0)
        ctrl.stop()

    def test_unknown_distance_means_stop(self, clean_state, fake_gpio):
        from config import MOTOR_A_EN
        ctrl = clean_state.autonomous

        def _broken():
            raise OSError('sensor gone')
        clean_state.ultrasonic._measure = _broken
        clean_state.ultrasonic._distance = 0.0       # данных нет
        ctrl.start('automatic')
        time.sleep(0.5)
        assert fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle == 0   # слепыми не едем
        ctrl.stop()

    def test_min_speed_floor(self, clean_state):
        from config import AUTO_MIN_SPEED
        ctrl = clean_state.autonomous
        assert ctrl._spd(5) >= AUTO_MIN_SPEED
        assert ctrl._spd(0) == 0

    def test_evade_turns_to_freer_side(self, monkeypatch):
        """v1.6.2: развороты объезда — ДУГИ (Аккерман). Направление «осмотра»
        видно по вывороту руля: осмотр вправо (30°), осмотр влево (150°),
        довод до свободной стороны (влево — 150)."""
        import picarpro.drive as dr
        import picarpro.functions.autonomous as au
        monkeypatch.setattr(au, 'AUTO_EVADE_BACKUP_S', 0.0)
        monkeypatch.setattr(dr, 'AUTO_ARC_RATE_DPS', 100000.0)  # дуги мгновенны

        class _RecServos:
            def __init__(self):
                self.calls = []

            def set_angle(self, sid, angle):
                self.calls.append((sid, angle))
                return True

        rec = _RecMotors()
        rec_servo = _RecServos()
        # справа 30 см, слева 150 см -> уходим влево
        ctrl = au.AutonomousController(rec, rec_servo, _StubUltra([30.0, 150.0]))
        ctx = au._RunCtx('automatic')
        ctrl._evade(ctx, use_gyro=False)
        steer = [a for sid, a in rec_servo.calls if sid == 0 and a != 90]
        assert steer[0] == 30 and steer[1] == 150      # осмотр: вправо, влево
        assert steer.count(150) >= 2                   # довод в свободную (лево)
        # дуги — это движение вперёд, а не spin
        assert any(c[0] == 'move' and c[2] == 'forward' for c in rec.calls)
        assert not any(c[0] == 'spin' for c in rec.calls)
        ctrl.shutdown()

    def test_track_hand_shake_keeps_tracking(self, clean_state):
        """v1.6.5: «встряхивание» руки больше не выключает режим — слежение
        продолжается, наклон камеры реагирует на руку вверх/вниз."""
        from tests.test_autonomous import _FakeCam
        ctrl = clean_state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        assert ctrl.start('trackHand')[0]
        assert wait_for(lambda: fc.cv_thread.on_hand_found is not None, 2.0)
        cb = fc.cv_thread.on_hand_found
        for i in range(12):                        # рука «мотается» влево-вправо
            x = 80 if i % 2 else 560
            assert cb([x, 240], 5000) is False     # колбэк никогда не просит стоп
            time.sleep(0.02)
        assert ctrl.is_active()                    # режим НЕ выключился
        # рука в верхней части кадра -> наклон уходит вниз от 90
        for _ in range(12):
            cb([320, 60], 5000)
        tilt = clean_state.servos.get_angle(2)
        assert tilt < 90
        # рука в нижней части кадра -> наклон уходит вверх
        for _ in range(12):
            cb([320, 420], 5000)
        assert clean_state.servos.get_angle(2) > tilt

    def test_rebind_keeps_single_thread(self, clean_state):
        ctrl = clean_state.autonomous
        before = threading.active_count()
        for _ in range(5):
            ctrl.rebind(clean_state.motors, clean_state.servos,
                        clean_state.ultrasonic, clean_state.mpu6050)
        assert threading.active_count() == before     # нет осиротевших потоков

    def test_recover_keeps_camera_binding(self, clean_state):
        from tests.test_autonomous import _FakeCam
        ctrl = clean_state.autonomous
        fc = _FakeCam()
        ctrl.set_camera(fc)
        clean_state._recreate_dependents()
        assert clean_state.autonomous is ctrl and ctrl._camera is fc


# ---------------------------------------------------------------------------
# Курс по гироскопу
# ---------------------------------------------------------------------------
class _FakeMpu:
    initialized = True

    def __init__(self, gz):
        self.gz, self.t = gz, 0.0
        self.fast = set()

    def request_fast(self, o): self.fast.add(o)
    def release_fast(self, o): self.fast.discard(o)

    def get_data(self):
        self.t += 0.1
        return {'gyro': {'z': self.gz}, 'ts': self.t}


class TestHeading:
    def test_integrates_angle(self):
        from picarpro.utils.heading import HeadingTracker
        mpu = _FakeMpu(gz=90.0)                  # 90 °/с
        h = HeadingTracker(mpu)
        h.begin()
        yaw = 0
        for _ in range(11):
            yaw = h.update()
        assert yaw == pytest.approx(90.0, abs=10.0)   # ~1 с вращения
        assert 'heading' in mpu.fast
        h.end()
        assert 'heading' not in mpu.fast

    def test_bias_calibration(self):
        from picarpro.utils.heading import HeadingTracker
        mpu = _FakeMpu(gz=2.0)                   # дрейф нуля 2 °/с
        h = HeadingTracker(mpu)
        assert h.calibrate(lambda t: False, samples=6)
        h.begin()
        for _ in range(20):
            yaw = h.update()
        assert yaw < 1.0                          # дрейф вычтен


# ---------------------------------------------------------------------------
# Арм-пушер: единицы измерения
# ---------------------------------------------------------------------------
class TestPusherUnits:
    def test_pushes_when_obstacle_within_30cm(self, clean_state):
        from picarpro.functions.arm_push import ObstaclePusher
        clean_state.ultrasonic._distance = 25.0       # 25 см = 0.25 м <= 0.30 м
        p = ObstaclePusher(clean_state)
        ok, _ = p.start()
        assert ok
        assert wait_for(lambda: p.pushes_done >= 1, 8.0)
        p.stop()

    def test_far_obstacle_no_push(self, clean_state):
        from picarpro.functions.arm_push import ObstaclePusher
        clean_state.ultrasonic._distance = 150.0
        p = ObstaclePusher(clean_state)
        p.start()
        time.sleep(0.6)
        assert p.pushes_done == 0
        p.stop()

    def test_restart_does_not_leave_two_loops(self, clean_state):
        from picarpro.functions.arm_push import ObstaclePusher
        clean_state.ultrasonic._distance = 150.0
        p = ObstaclePusher(clean_state)
        p.start(); p.stop(); p.start()
        time.sleep(0.5)
        alive = [t for t in threading.enumerate() if t.is_alive()
                 and getattr(t, '_target', None) == p._loop]
        assert len(alive) == 1
        p.stop()


# ---------------------------------------------------------------------------
# Голос: берём последнюю строку
# ---------------------------------------------------------------------------
class TestVoiceParsing:
    def test_last_word_wins(self):
        from picarpro.functions.voice_command import VoiceCommandController as V
        assert V._match('look left. stop') == 'stop'
        assert V._match('camera up') == 'camUp'
        assert V._match('nothing') is None

    def test_repeated_command_executes_again(self, tmp_path, monkeypatch):
        import picarpro.functions.voice_command as vmod
        out = tmp_path / 'v.txt'
        monkeypatch.setattr(vmod, 'VOICE_OUTPUT_FILE', str(out))
        ran = []
        vc = vmod.VoiceCommandController.__new__(vmod.VoiceCommandController)
        vc._last_command, vc._last_size = '', 0
        vc._execute_command = ran.append
        out.write_text('stop\n')
        vc._read_and_execute()
        out.write_text('stop\nlook left\n')
        vc._read_and_execute()
        out.write_text('stop\nlook left\nstop\n')
        vc._read_and_execute()
        assert ran == ['stop', 'lookLeft', 'stop']


# ---------------------------------------------------------------------------
# NavTCP: QUIT закрывает соединение
# ---------------------------------------------------------------------------
class TestNavQuit:
    def test_quit_closes(self, clean_state):
        from picarpro.routes.nav_server import NavTcpServer
        srv = NavTcpServer(clean_state, port=0)
        srv.start()
        port = srv._httpd.server_address[1]
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=3)
            s.sendall(b'QUIT\n')
            assert s.recv(16).strip() == b'BYE'
            s.settimeout(2)
            assert s.recv(16) == b''          # сервер закрыл соединение
            s.close()
        finally:
            srv.shutdown()


# ---------------------------------------------------------------------------
# Камера: новые кадры, сон, переворот
# ---------------------------------------------------------------------------
class TestCameraV150:
    @pytest.fixture
    def cam(self, monkeypatch):
        import picarpro.camera.camera_opencv as cm
        monkeypatch.setattr(cm.Camera, '_instance', None)
        c = cm.Camera()
        yield c
        c.shutdown()

    def test_wait_frame_returns_only_new(self, cam):
        cam.stream_open()
        cam.start()
        fid, frame = cam.wait_frame(0, timeout=2.0)
        assert frame is not None
        fid2, frame2 = cam.wait_frame(fid, timeout=2.0)
        assert fid2 > fid                    # новый кадр, а не повтор старого
        cam.stream_close()

    def test_no_jpeg_without_viewer(self, cam):
        cam.set_cv_mode('findColor')         # CV включён, зрителей нет
        cam.start()
        time.sleep(0.5)
        assert cam._frame is None            # JPEG не кодируется впустую
        assert cam._raw_id > 0

    def test_camera_sleeps_and_wakes(self, cam, monkeypatch):
        import picarpro.camera.camera_opencv as cm
        monkeypatch.setattr(cm, 'CAMERA_SLEEP_AFTER_S', 0.2)
        from tests.fakes.all_fakes import FakePicamera2
        cam.start()
        assert wait_for(lambda: FakePicamera2.instance is not None
                        and FakePicamera2.instance.started, 2.0)
        assert wait_for(lambda: cam._asleep, 3.0)
        assert FakePicamera2.instance.started is False     # CSI-поток остановлен
        cam.stream_open()                                    # спрос -> просыпается
        assert wait_for(lambda: FakePicamera2.instance.started, 3.0)
        fid, frame = cam.wait_frame(0, timeout=3.0)
        assert frame is not None
        cam.stream_close()

    def test_flip_uses_transform_not_set_control(self, monkeypatch):
        import picarpro.camera.camera_opencv as cm
        import sys, types
        lib = types.ModuleType('libcamera')
        lib.Transform = lambda hflip=0, vflip=0: ('T', hflip, vflip)
        monkeypatch.setitem(sys.modules, 'libcamera', lib)
        monkeypatch.setattr(cm, 'CAMERA_FLIP_HORIZONTAL', True)
        monkeypatch.setattr(cm.Camera, '_instance', None)
        c = cm.Camera()
        try:
            c._init_camera()
            from tests.fakes.all_fakes import FakePicamera2
            assert c._source == 'picamera2'       # инициализация не упала
            assert FakePicamera2.instance.config['transform'] == ('T', 1, 0)
        finally:
            c.shutdown()

    def test_first_diag_not_suppressed_early_after_boot(self, monkeypatch):
        import picarpro.camera.camera_opencv as cm
        monkeypatch.setattr(cm.time, 'monotonic', lambda: 5.0)   # «5 с после загрузки»
        cm.Camera._last_diag_ts = 0.0
        calls = []
        monkeypatch.setattr(cm.Camera, '_collect_diagnostics',
                            classmethod(lambda cls: calls.append(1) or ''))
        cm.Camera._log_init_failure(RuntimeError('x'))
        assert calls, 'первая диагностика подавлена (monotonic < интервала)'


# ---------------------------------------------------------------------------
# WS2812
# ---------------------------------------------------------------------------
class TestLedsV150:
    def test_spi_table_matches_encoder(self):
        from picarpro.hardware.leds_ws2812 import _SPI_TABLE, _byte_to_spi
        assert all(_SPI_TABLE[i] == _byte_to_spi(i) for i in range(256))

    def test_no_frames_after_off(self, clean_state):
        from tests.fakes.all_fakes import FakeSpiDev
        leds = clean_state.leds
        leds.set_mode('rainbow')
        time.sleep(0.2)
        leds.set_mode('off')
        time.sleep(0.1)
        spi = FakeSpiDev.instances[-1]
        n = len(spi.written)
        time.sleep(0.3)
        assert len(spi.written) == n              # «хвост» анимации не светит

    def test_power_scale_dims_output(self, clean_state):
        from tests.fakes.all_fakes import FakeSpiDev
        leds = clean_state.leds
        leds.set_mode('solid', (255, 255, 255))
        spi = FakeSpiDev.instances[-1]
        full = sum(spi.written[-1])
        leds.set_power_scale(0.3)
        assert sum(spi.written[-1]) != full


# ---------------------------------------------------------------------------
# Менеджер питания
# ---------------------------------------------------------------------------
class TestPowerManager:
    def test_idle_releases_steering_but_not_when_moving(self, clean_state, monkeypatch):
        import picarpro.power as pw
        from config import SERVO_STEERING
        monkeypatch.setattr(pw, 'SERVO_IDLE_RELEASE_S', 5.0)
        st = clean_state
        st.servos.set_angle(SERVO_STEERING, 100)
        assert st.servos.is_held(SERVO_STEERING)
        pm = st.power
        st.motors.spin(40, 'left')                        # едем -> не отпускать
        pm.tick(now=time.monotonic() + 100)
        assert st.servos.is_held(SERVO_STEERING)
        st.motors.stop()
        pm.tick(now=time.monotonic() + 100)               # простой -> отпустить
        assert not st.servos.is_held(SERVO_STEERING)
        st.servos.set_angle(SERVO_STEERING, 90)           # команда снова держит
        assert st.servos.is_held(SERVO_STEERING)

    def test_low_battery_caps_speed_and_dims(self, clean_state):
        st = clean_state

        class _V:
            has_adc = False
            def __init__(self, low): self.low = low
            def get_status(self): return {'low': self.low}
        st.voltage = _V(True)
        st.power.tick()
        assert st.motors.max_speed < 100
        assert st.leds._power_scale < 1.0
        assert st.get_status()['low_power'] is True
        st.voltage = _V(False)
        st.power.tick()
        assert st.motors.max_speed == 100
        assert st.leds._power_scale == 1.0


# ---------------------------------------------------------------------------
# Отключение последнего клиента останавливает ручное движение
# ---------------------------------------------------------------------------
class TestClientGone:
    def test_motors_stop_when_last_client_leaves(self, clean_state):
        st = clean_state
        st.motors.move(50, 'forward', 'no')
        assert st.motors.is_moving
        assert st.on_last_client_gone() is True
        assert not st.motors.is_moving

    def test_autonomous_not_interrupted(self, clean_state):
        st = clean_state
        st.ultrasonic._distance = 100.0
        st.autonomous.start('keepDistance')
        time.sleep(0.3)
        assert st.on_last_client_gone() is False
        st.autonomous.stop()


# ---------------------------------------------------------------------------
# system_info: нет ложной тревоги из-за напряжения ядра
# ---------------------------------------------------------------------------
class TestSystemInfo:
    def test_parse_throttled(self):
        from picarpro.utils.system_info import _parse_hex
        assert _parse_hex('throttled=0x50005') & 1
        assert not _parse_hex('0x50000') & 1

    def test_cpu_usage_no_blocking_sleep(self):
        from picarpro.utils.system_info import SystemInfo
        t0 = time.monotonic()
        SystemInfo.get_cpu_usage()
        assert time.monotonic() - t0 < 0.1


# ---------------------------------------------------------------------------
# MPU6050: опрос по требованию
# ---------------------------------------------------------------------------
class TestMpuFast:
    def test_fast_request_set(self, clean_state):
        m = clean_state.mpu6050
        m.request_fast('x')
        assert 'x' in m._fast_users
        m.release_fast('x')
        assert not m._fast_users
        assert 'ts' in (m.get_data() or {'ts': 0})
