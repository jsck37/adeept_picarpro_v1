"""Автономные режимы (v1.6.10, переписаны).

Режимы:
  radarScan   — живой радар: HC-SR04 на панораме камеры качается по сектору,
                точки по углам (медиана пингов, возраст, «нет эха»);
  mazeExit    — поиск выхода из лабиринта по радару: остановка -> скан ->
                правило левой/правой руки -> шаг/поворот; выход = простор в скане
                или чёрная полоса под ИК-датчиками;
  trackLine   — езда по линии на ИК-датчиках (3 шт.), память стороны ухода
                линии, поиск дугой, предохранитель по ультразвуку;
  trackLineCV — езда по линии камерой (чёрная на белом) + ИК как запасной канал;
  trackHand   — камера следит за самой передней рукой (YOLO-pose), колёса стоят;
  findColor   — езда за цветным объектом;
  watchDog    — охрана: сирена при движении в кадре.

Удалены как нерабочие/ненужные: automatic (Auto Drive), keepDistance
(Distance), obstacleGrab (Grab), mazeU (Maze U).

Датчики: ультразвук (с фильтром и предохранителем), ИК-датчики линии,
гироскоп MPU6050 (повороты по углу, HeadingTracker), камера.
"""
import threading
import time

from picarpro.logger import logger
from picarpro.utils.filters import KalmanFilter1D, PID
from picarpro.utils.heading import HeadingTracker
from picarpro.drive import arc_turn
from picarpro.functions.radar import Radar, MAX_RANGE_CM
from config import (
    SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT,
    LINE_LEFT_PIN, LINE_MIDDLE_PIN, LINE_RIGHT_PIN, LINE_ACTIVE_LOW,
    LINE_IR_SPEED, LINE_LOST_SEARCH_S, LINE_LOST_STOP_S, GUARD_PAN_TOL_DEG,
    CV_LINE_FOLLOW_SPEED, CV_LINE_FOLLOW_STEER_GAIN,
    CAMERA_RESOLUTION, CAM_FOV_DEG,
    CAM_PAN_TRACK_DIR, CAM_TILT_TRACK_DIR,
    FINDCOLOR_TOLERANCE, FINDCOLOR_SPEED, FINDCOLOR_STEER_RANGE,
    AUTO_MIN_SPEED, AUTO_SPEED_SLEW_PER_S, AUTO_EVADE_SPIN_SPEED,
    AUTO_OBSTACLE_STOP_CM, AUTO_OBSTACLE_SLOW_CM, AUTO_SEARCH_TIMEOUT_S,
    HAND_TRACK_GAIN, HAND_TRACK_DEADZONE_DEG, HAND_TRACK_MAX_STEP_DEG,
    HAND_TRACK_EMA, HAND_LOST_HOLD_S,
    MAZE_HAND, MAZE_SPEED, MAZE_STEP_S, MAZE_STOP_CM, MAZE_OPEN_CM,
    MAZE_FRONT_CLEAR_CM, MAZE_EXIT_CM, MAZE_EXIT_FRACTION, MAZE_EXIT_CONFIRM,
    MAZE_BACKUP_S, MAZE_TIMEOUT_S, MAZE_FINISH_ON_LINE, MAZE_TURN_SNAP_DEG,
    MAZE_SCAN_STEP_DEG, MAZE_FRONT_HALF_DEG, MAZE_SIDE_MIN_DEG,
    MAZE_SIDE_MAX_DEG, MAZE_STEER_RANGE, RADAR_SECTOR_DEG,
)

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None

MODES_WITH_ULTRA = ('radarScan', 'mazeExit')
MODES_WITH_CAMERA = ('trackLineCV', 'trackHand', 'findColor', 'watchDog')
# ультразвук — необязательный предохранитель (стоп перед препятствием)
MODES_ULTRA_GUARD = ('trackLine', 'trackLineCV', 'findColor')
ALL_MODES = MODES_WITH_ULTRA + ('trackLine',) + MODES_WITH_CAMERA

_INNER_MIN = 0.3        # внутренний борт при дуге — не слабее 30 %


def _inner(amount):
    """Доля мощности внутреннего борта для «крутизны» поворота 0..1:
    0 — прямо (1.0), 1 — самая крутая дуга (0.3)."""
    return max(_INNER_MIN, 1.0 - 0.9 * min(1.0, abs(amount)))


def _steer_angle(offset):
    """offset -1..1 (влево < 0) -> угол руля. Влево = больше 90 (как в UI)."""
    return max(30, min(150, 90 - int(offset * 60)))


class _RunCtx:
    """Один запуск режима. Свой Event остановки у каждого запуска: старый
    цикл не «оживает», если новый режим запущен сразу после остановки."""

    def __init__(self, mode):
        self.mode = mode
        self.stop = threading.Event()

    @property
    def alive(self):
        return not self.stop.is_set()

    def wait(self, seconds):
        """Пауза, прерываемая остановкой. True — режим остановлен."""
        return self.stop.wait(seconds)


class AutonomousController:
    """Автономные режимы в потоковой архитектуре: один рабочий поток, один
    активный режим, остановка — через Event у каждого запуска."""

    def __init__(self, motors, servos, ultrasonic, mpu6050=None):
        self.motors = motors
        self.servos = servos
        self.ultrasonic = ultrasonic
        self._heading = HeadingTracker(mpu6050)
        self._radar = Radar(servos, ultrasonic)
        self._running = True
        self._active = False
        self._flag = threading.Event()
        self._lock = threading.RLock()
        self._motor_lock = threading.Lock()
        self._ctx = None
        self._current_mode = 'none'
        self._radar_data = []
        self._info = {}                   # состояние режима для UI (get_auto_info)
        self._speed_now = 0.0
        self._speed_ts = time.monotonic()
        self._ir_left_pin = LINE_LEFT_PIN
        self._ir_mid_pin = LINE_MIDDLE_PIN
        self._ir_right_pin = LINE_RIGHT_PIN
        self._ir_available = False
        if _HAS_GPIO:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
                GPIO.setup(self._ir_left_pin, GPIO.IN)
                GPIO.setup(self._ir_mid_pin, GPIO.IN)
                GPIO.setup(self._ir_right_pin, GPIO.IN)
                self._ir_available = True
                logger.info(f'[Auto] IR sensors OK — L{LINE_LEFT_PIN} '
                            f'M{LINE_MIDDLE_PIN} R{LINE_RIGHT_PIN}')
            except Exception as e:
                logger.error(f'[Auto] IR init failed: {e}')
        self._camera = None
        self._distance_filter = KalmanFilter1D(
            process_variance=1e-2, measurement_variance=1.0)
        self._motion_count = 0
        self._on_alarm = None  # callback(pixels) — назначается из state
        # поток запускается ПОСЛЕДНИМ, когда все поля уже инициализированы
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---- конфигурация ---------------------------------------------------------
    def set_camera(self, camera):
        self._camera = camera

    def set_alarm_callback(self, cb):
        self._on_alarm = cb

    def rebind(self, motors, servos, ultrasonic, mpu6050=None):
        """Подставить новые экземпляры железа (после восстановления связи)
        без создания второго контроллера и осиротевшего потока."""
        self.stop()
        with self._lock:
            self.motors = motors
            self.servos = servos
            self.ultrasonic = ultrasonic
            self._heading = HeadingTracker(mpu6050)
            self._radar = Radar(servos, ultrasonic)

    def _ultra_ok(self):
        return bool(self.ultrasonic and self.ultrasonic._initialized)

    # ---- жизненный цикл режима -----------------------------------------------
    def _run(self):
        handlers = {
            'radarScan': self._radar_scan,
            'mazeExit': self._maze_exit,
            'trackLine': self._track_line,
            'trackLineCV': self._track_line_cv,
            'trackHand': self._track_hand,
            'findColor': self._find_color,
            'watchDog': self._watch_dog,
        }
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            with self._lock:
                ctx = self._ctx
                self._flag.clear()
            if ctx is None or not ctx.alive:
                continue
            try:
                self._set_steer(ctx, 90)       # руль мог быть «отпущен» в простое
                uses_ultra = ((ctx.mode in MODES_WITH_ULTRA
                               or ctx.mode in MODES_ULTRA_GUARD)
                              and self._ultra_ok())
                if uses_ultra:
                    self.ultrasonic.acquire()
                try:
                    handlers[ctx.mode](ctx)
                finally:
                    if uses_ultra:
                        self.ultrasonic.release()
            except Exception as e:
                logger.error(f'[Auto] error in {ctx.mode}: {e}')
            finally:
                self._finish(ctx)

    def _finish(self, ctx):
        """Режим завершился (сам или по stop()): остановить моторы и
        сбросить состояние — но только если за это время не запущен новый."""
        with self._lock:
            with self._motor_lock:
                ctx.stop.set()
            if self._ctx is ctx:
                if self.motors:
                    self.motors.stop()
                self._ctx = None
                self._active = False
                self._current_mode = 'none'
                self._speed_now = 0.0
                self._info = {}
        logger.info(f'[Auto] finished: {ctx.mode}')

    def start(self, mode):
        if mode not in ALL_MODES:
            return False, f'Unknown mode: {mode}'
        if mode in MODES_WITH_ULTRA and not self._ultra_ok():
            logger.warning(f'[Auto] cannot start {mode}: ultrasonic not available')
            return False, 'Ultrasonic sensor not available'
        if mode in MODES_WITH_CAMERA and not self._camera:
            logger.warning(f'[Auto] cannot start {mode}: camera not available')
            return False, 'Camera not available'
        with self._lock:
            old = self._ctx
            if old is not None:
                with self._motor_lock:
                    old.stop.set()
                    if self.motors:
                        self.motors.stop()
            ctx = _RunCtx(mode)
            self._ctx = ctx
            self._current_mode = mode
            self._active = True
            self._speed_now = 0.0
            self._info = {'mode': mode, 'state': 'starting'}
            self._flag.set()
        logger.info(f'[Auto] started: {mode}')
        return True, f'Started: {mode}'

    def stop(self):
        with self._lock:
            ctx = self._ctx
            with self._motor_lock:
                if ctx is not None:
                    ctx.stop.set()
                if self.motors:
                    self.motors.stop()
            self._active = False
            self._current_mode = 'none'
            self._speed_now = 0.0

    def is_active(self):
        return self._active

    def get_radar_data(self):
        return self._radar_data

    def get_auto_info(self):
        """Состояние текущего режима для UI (лабиринт: шаг/решение/угол)."""
        info = dict(self._info)
        info['radar_angle'] = self._radar.angle
        return info

    def get_motion_count(self):
        return self._motion_count

    def shutdown(self):
        self.stop()
        self._running = False
        self._flag.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        logger.info('[Auto] shutdown')

    def _set_info(self, **kw):
        self._info.update(kw)

    # ---- датчики ----------------------------------------------------------------
    def _ir_line(self, pin):
        """Семантика «линия обнаружена» для одного датчика: True = линия.
        Полярность модуля — LINE_ACTIVE_LOW в config.py."""
        if not self._ir_available:
            return False
        try:
            raw = GPIO.input(pin)
        except Exception:
            return False
        return (raw == 0) if LINE_ACTIVE_LOW else (raw == 1)

    def _read_ir(self):
        if not self._ir_available:
            return False, False, False
        return (self._ir_line(self._ir_left_pin),
                self._ir_line(self._ir_mid_pin),
                self._ir_line(self._ir_right_pin))

    def get_ir_values(self):
        """(left, middle, right) — True = датчик НАД ЛИНИЕЙ; None* — модуля нет."""
        if not self._ir_available:
            return None, None, None
        return self._read_ir()

    def _safe_distance(self):
        """Дистанция для решений, см, или None (нет данных). «Быстрая атака,
        медленный спад»: минимум из сырого и сглаженного значения."""
        raw = self.ultrasonic.get_distance_cm() if self.ultrasonic else None
        if raw is None:
            return None
        smooth = self._distance_filter.update(raw)
        return min(raw, smooth)

    def _front_blocked(self, ctx, stop_cm=AUTO_OBSTACLE_STOP_CM):
        """Предохранитель: впереди препятствие ближе stop_cm. Работает, только
        если датчик смотрит вперёд (панорама камеры близка к центру: он стоит на ней)."""
        if not self._ultra_ok() or not ctx.alive:
            return False
        try:
            if abs(self.servos.get_angle(SERVO_CAM_PAN) - 90) > GUARD_PAN_TOL_DEG:
                return False
        except Exception:
            return False
        d = self._safe_distance()
        return d is not None and d < stop_cm

    # ---- команды движения (всё идёт через _motor_lock и проверку ctx) -------------
    def _send(self, ctx, fn, *args):
        with self._motor_lock:
            if not ctx.alive or not self.motors:
                return False
            fn(*args)
            return True

    def _set_steer(self, ctx, angle):
        if ctx.alive and self.servos:
            self.servos.set_angle(SERVO_STEERING, angle)

    def _spd(self, target):
        """Плавный старт + нижняя граница скорости для автономных режимов."""
        now = time.monotonic()
        dt = min(0.3, now - self._speed_ts)
        self._speed_ts = now
        if target <= 0:
            self._speed_now = 0.0
            return 0
        target = max(AUTO_MIN_SPEED, min(100, target))
        if target > self._speed_now:
            self._speed_now = min(target, self._speed_now
                                  + AUTO_SPEED_SLEW_PER_S * dt)
        else:
            self._speed_now = target           # тормозить можно сразу
        return int(max(AUTO_MIN_SPEED, self._speed_now))

    def _halt(self, ctx):
        self._speed_now = 0.0
        with self._motor_lock:
            if self.motors:
                self.motors.stop()

    def _forward(self, ctx, speed):
        return self._send(ctx, self.motors.move, self._spd(speed),
                          'forward', 'no', 0.5)

    def _reverse(self, ctx, speed):
        return self._send(ctx, self.motors.move, self._spd(speed),
                          'backward', 'no', 0.5)

    def _arc(self, ctx, speed, side, amount):
        """Дуга вперёд: side 'left'|'right', amount 0..1 — крутизна."""
        inner = _inner(amount)
        left, right = (inner, 1.0) if side == 'left' else (1.0, inner)
        return self._send(ctx, self.motors.move_differential,
                          self._spd(speed), 'forward', left, right)

    def _spin(self, ctx, speed, side):
        return self._send(ctx, self.motors.spin, self._spd(speed), side)

    def _turn(self, ctx, degrees, use_gyro):
        """Развернуться на degrees (>0 вправо, <0 влево) ДУГОЙ: шасси Аккерман,
        на месте не вращается (drive.arc_turn: руль до упора + ход вперёд, угол
        закрывается по гироскопу или по времени)."""
        if abs(degrees) < 3 or not ctx.alive:
            return
        self._halt(ctx)
        heading = self._heading if use_gyro else None
        arc_turn(self.motors, self.servos, degrees,
                 alive=ctx.alive, wait=ctx.wait, heading=heading)

    # =======================================================================
    # РАДАР
    # =======================================================================
    def _publish_radar(self):
        self._radar_data = self._radar.snapshot()

    def _radar_scan(self, ctx):
        """Живой радар: проходы туда-сюда по сектору до остановки режима."""
        if not self._ultra_ok():
            logger.warning('[Radar] HC-SR04 не инициализирован — скан невозможен')
            return
        r = self._radar
        r.reset()
        self._radar_data = []
        r.hold()
        ascending = True
        try:
            while ctx.alive:
                r.sweep(ctx.wait, lambda: ctx.alive, ascending=ascending,
                        on_point=lambda a: self._publish_radar())
                ascending = not ascending
                nearest = r.sector_min(-RADAR_SECTOR_DEG, RADAR_SECTOR_DEG,
                                       include_clear=False)
                self._set_info(state='scan', nearest_cm=nearest)
        finally:
            r.unhold()
            r.home()

    # =======================================================================
    # ЛАБИРИНТ (поиск выхода по радару)
    # =======================================================================
    def _maze_scan(self, ctx, ascending):
        """Быстрый скан для решения. True — скан завершён."""
        ok = self._radar.sweep(ctx.wait, lambda: ctx.alive, ascending=ascending,
                               step=MAZE_SCAN_STEP_DEG,
                               on_point=lambda a: self._publish_radar())
        return ok

    def _maze_decide(self):
        """Решение по карте радара -> (действие, угол). Углы радара: влево > 0.

        Правило стены: сначала проём со стороны ведущей руки, затем прямо,
        затем проём с другой стороны, иначе тупик. Возвращает также данные для UI.
        """
        r = self._radar
        left_hand = (MAZE_HAND != 'right')
        sgn = 1 if left_hand else -1                 # знак угла ведущей стороны
        side_lo, side_hi = sorted((sgn * MAZE_SIDE_MIN_DEG, sgn * MAZE_SIDE_MAX_DEG))
        opp_lo, opp_hi = sorted((-sgn * MAZE_SIDE_MIN_DEG, -sgn * MAZE_SIDE_MAX_DEG))
        front = r.sector_min(-MAZE_FRONT_HALF_DEG, MAZE_FRONT_HALF_DEG)
        primary = r.sector_min(side_lo, side_hi, include_clear=True)
        secondary = r.sector_min(opp_lo, opp_hi, include_clear=True)
        front_v = MAZE_STOP_CM if front is None else front
        info = {'front_cm': front, 'primary_cm': primary, 'secondary_cm': secondary}
        # дальняя точка сектора — куда поворачивать
        def best_angle(lo, hi):
            best, best_d = None, -1
            for p in r.snapshot():
                if lo - 1e-6 <= p['angle'] <= hi + 1e-6 and p['distance'] > best_d:
                    best, best_d = p['angle'], p['distance']
            return best
        if primary is not None and primary >= MAZE_OPEN_CM:
            return 'turn_primary', best_angle(side_lo, side_hi), info
        if front_v >= MAZE_FRONT_CLEAR_CM:
            return 'forward', best_angle(-30, 30), info
        if secondary is not None and secondary >= MAZE_OPEN_CM:
            return 'turn_secondary', best_angle(opp_lo, opp_hi), info
        return 'dead_end', None, info

    def _maze_step(self, ctx, steer_deg):
        """Шаг вперёд MAZE_STEP_S с контролем препятствия фронтальным пингом.
        Возвращает 'ok' | 'blocked' | 'line' | 'stopped'."""
        r = self._radar
        r.point_at(0, ctx.wait)           # датчик вперёд (+ пауза на доезд серво)
        if not ctx.alive:
            return 'stopped'
        # руль доворачивает к свободному направлению (угол радара: влево > 0)
        if steer_deg is not None and abs(steer_deg) <= MAZE_TURN_SNAP_DEG:
            off = max(-1.0, min(1.0, steer_deg / float(MAZE_TURN_SNAP_DEG)))
            self._set_steer(ctx, max(30, min(150, 90 + int(off * MAZE_STEER_RANGE))))
        else:
            self._set_steer(ctx, 90)
        t_end = time.monotonic() + MAZE_STEP_S
        result = 'ok'
        while ctx.alive and time.monotonic() < t_end:
            d = self.ultrasonic.measure_now(attempts=1)
            if d is not None and d < MAZE_STOP_CM:
                result = 'blocked'
                break
            if MAZE_FINISH_ON_LINE and self._ir_available:
                l, m, rr = self._read_ir()
                if l and m and rr:
                    result = 'line'
                    break
            self._forward(ctx, MAZE_SPEED)
            ctx.wait(0.05)
        self._halt(ctx)
        self._set_steer(ctx, 90)
        return result if ctx.alive else 'stopped'

    def _maze_exit(self, ctx):
        if not self._ultra_ok():
            logger.warning('[Maze] HC-SR04 не инициализирован — режим недоступен')
            return
        r = self._radar
        r.reset()
        self._radar_data = []
        r.hold()
        use_gyro = self._heading.available
        if use_gyro:
            self._heading.calibrate(ctx.wait)
        left_hand = (MAZE_HAND != 'right')
        t0 = time.monotonic()
        ascending = True
        exit_hits = 0
        dead_ends = 0
        steps = 0
        try:
            while ctx.alive and time.monotonic() - t0 < MAZE_TIMEOUT_S:
                self._halt(ctx)
                self._set_info(state='scan', steps=steps)
                if not self._maze_scan(ctx, ascending):
                    break
                ascending = not ascending

                # --- выход виден? (простор в большей части скана) ---
                frac = r.clear_fraction(MAZE_EXIT_CM)
                exit_hits = exit_hits + 1 if frac >= MAZE_EXIT_FRACTION else 0
                self._set_info(open_fraction=round(frac, 2), exit_hits=exit_hits)
                if exit_hits >= MAZE_EXIT_CONFIRM:
                    logger.info('[Maze] выход найден (простор в скане)')
                    self._set_info(state='exit')
                    break

                action, angle, info = self._maze_decide()
                self._set_info(state=action, target_angle=angle, **info)
                logger.info(f'[Maze] {action} angle={angle} {info}')

                if action == 'dead_end':
                    dead_ends += 1
                    self._set_info(dead_ends=dead_ends)
                    self._reverse(ctx, MAZE_SPEED)
                    ctx.wait(MAZE_BACKUP_S)
                    self._halt(ctx)
                    # дугой вперёд на 90° дважды = разворот (вторая дуга — в ту же сторону)
                    side = 90 if left_hand else -90
                    self._turn(ctx, -side, use_gyro)
                    self._turn(ctx, -side, use_gyro)
                    continue

                if action in ('turn_primary', 'turn_secondary'):
                    # угол радара: влево > 0; _turn: вправо > 0
                    ang = angle if angle is not None else (
                        60 if (action == 'turn_primary') == left_hand else -60)
                    # сначала подъехать, чтобы проём оказался рядом, затем довернуть
                    res = self._maze_step(ctx, None)
                    if res in ('stopped',):
                        break
                    if res == 'line':
                        self._set_info(state='finish_line')
                        logger.info('[Maze] финишная линия')
                        break
                    self._turn(ctx, -ang, use_gyro)
                    steps += 1
                    continue

                # forward: идём к самому свободному направлению в ±30°
                res = self._maze_step(ctx, angle)
                steps += 1
                if res == 'line':
                    self._set_info(state='finish_line')
                    logger.info('[Maze] финишная линия')
                    break
                if res == 'blocked':
                    self._reverse(ctx, MAZE_SPEED)
                    ctx.wait(MAZE_BACKUP_S * 0.6)
                    self._halt(ctx)
            else:
                if ctx.alive:
                    logger.warning('[Maze] таймаут — выход не найден')
                    self._set_info(state='timeout')
        finally:
            self._halt(ctx)
            r.unhold()
            r.home()
            self._set_steer(ctx, 90)

    # =======================================================================
    # ЛИНИЯ
    # =======================================================================
    def _track_line(self, ctx):
        """Езда по линии на 3 ИК-датчиках.

        v1.6.10: помнит, в какую сторону ушла линия, при потере ищет её дугой
        в эту сторону LINE_LOST_SEARCH_S секунд, затем стоит; не едет вслепую
        (раньше «ползла вперёд» бесконечно); перед препятствием останавливается
        (если есть ультразвук).
        """
        if not self._ir_available:
            logger.warning('[Auto] IR sensors not available')
            return
        speed = LINE_IR_SPEED
        last_side = 0                       # -1 линия слева, +1 справа
        lost_since = None
        while ctx.alive:
            if self._front_blocked(ctx):
                self._halt(ctx)
                self._set_info(state='obstacle')
                ctx.wait(0.1)
                continue
            left, mid, right = self._read_ir()
            if left or mid or right:
                lost_since = None
                if mid and not left and not right:
                    self._set_steer(ctx, 90)
                    self._forward(ctx, speed)
                    self._set_info(state='follow')
                elif left and not right:
                    last_side = -1
                    amount = 0.45 if mid else 0.85
                    self._set_steer(ctx, _steer_angle(-amount))
                    self._arc(ctx, speed - (0 if mid else 8), 'left', amount)
                elif right and not left:
                    last_side = 1
                    amount = 0.45 if mid else 0.85
                    self._set_steer(ctx, _steer_angle(amount))
                    self._arc(ctx, speed - (0 if mid else 8), 'right', amount)
                else:                        # все три (перекрёсток) — прямо, тихо
                    self._set_steer(ctx, 90)
                    self._forward(ctx, AUTO_MIN_SPEED)
            else:
                now = time.monotonic()
                lost_since = lost_since or now
                gone = now - lost_since
                self._set_info(state='lost')
                if gone < LINE_LOST_SEARCH_S and last_side:
                    side = 'left' if last_side < 0 else 'right'
                    self._set_steer(ctx, _steer_angle(last_side * 0.9))
                    self._arc(ctx, AUTO_MIN_SPEED, side, 0.9)
                elif gone < LINE_LOST_STOP_S:
                    self._halt(ctx)
                    self._set_steer(ctx, 90)
                else:
                    logger.info('[Auto] trackLine: линия потеряна — стоп')
                    break
            ctx.wait(0.04)

    def _track_line_cv(self, ctx):
        """Езда по линии камерой (чёрная на белом) + ИК как запасной канал.

        Ошибка = взвешенная позиция линии по двум строкам кадра (ближняя 65 %,
        дальняя 35 % — упреждение на поворот), ПИД, скорость падает на крутых
        поворотах. Линия потеряна — помним сторону, ищем дугой, затем стоим.
        """
        from picarpro.camera.camera_opencv import CV_LINE
        if not self._camera:
            return
        cam = self._camera
        cam.set_cv_mode(CV_LINE)
        centre_x = CAMERA_RESOLUTION[0] / 2.0
        speed = CV_LINE_FOLLOW_SPEED
        gain = CV_LINE_FOLLOW_STEER_GAIN
        ir_ok = self._ir_available
        smooth, last_offset, lost = 0.0, 0.0, 0
        pid = PID(kp=1.0, kd=0.06, out_min=-1.0, out_max=1.0)
        state = {'pos': [0, 0], 'found': False}
        new_frame = threading.Event()

        def on_line(pos, angle):
            state['pos'] = pos
            state['found'] = pos[0] > 0 or pos[1] > 0
            new_frame.set()

        cam.cv_thread.on_line_found = on_line
        try:
            while ctx.alive:
                # ждём НОВЫЙ кадр, а не молотим одни и те же данные
                got = new_frame.wait(0.25)
                new_frame.clear()
                if not ctx.alive:
                    break
                if self._front_blocked(ctx):
                    self._halt(ctx)
                    self._set_info(state='obstacle')
                    continue
                ir_l, ir_m, ir_r = self._read_ir()
                ir_off = 0.0
                if ir_ok and ir_l != ir_r:
                    ir_off = -1.0 if ir_l else 1.0
                if got and state['found']:
                    p1, p2 = state['pos']          # p1 — ближняя строка, p2 — дальняя
                    if p1 > 0 and p2 > 0:
                        line_c = 0.65 * p1 + 0.35 * p2
                    else:
                        line_c = p1 or p2
                    raw = (line_c - centre_x) / centre_x
                    lost = 0
                    smooth = 0.5 * raw + 0.5 * smooth
                    last_offset = smooth
                    fused = pid.update(smooth)
                    if ir_off:
                        fused = max(-1.0, min(1.0, fused + ir_off * 0.25))
                    self._set_steer(ctx, max(30, min(150,
                                    90 - int(fused * 60 * gain))))
                    s = max(AUTO_MIN_SPEED, speed * (1.0 - 0.5 * abs(fused)))
                    self._set_info(state='follow', offset=round(fused, 2))
                    if abs(fused) > 0.25:
                        self._arc(ctx, s, 'left' if fused < 0 else 'right',
                                  abs(fused))
                    else:
                        self._forward(ctx, s)
                else:
                    lost += 1
                    pid.reset()
                    self._set_info(state='lost')
                    if ir_ok and ir_l != ir_r:
                        side = 'left' if ir_l else 'right'
                        self._arc(ctx, AUTO_MIN_SPEED, side, 0.7)
                        self._set_steer(ctx, 120 if ir_l else 60)
                    elif lost < 8:      # ~1 с: ещё помним, куда уходила линия
                        self._set_steer(ctx, max(30, min(150,
                                        90 - int(last_offset * 60 * gain))))
                        side = 'left' if last_offset < 0 else 'right'
                        if abs(last_offset) > 0.2:
                            self._arc(ctx, AUTO_MIN_SPEED, side, 0.6)
                        else:
                            self._forward(ctx, AUTO_MIN_SPEED)
                    else:               # линии нет давно — не едем вслепую
                        self._halt(ctx)
                        self._set_steer(ctx, 90)
        finally:
            cam.cv_thread.on_line_found = None
            cam.set_cv_mode('none')

    # =======================================================================
    # РУКА (YOLO-pose): следим ТОЛЬКО камерой
    # =======================================================================
    def _track_hand(self, ctx):
        """Камера (панорама + наклон) держит в центре кадра самую переднюю
        руку, найденную YOLO-pose. Колёса не двигаются.

        Угол ошибки = смещение цели от центра в пикселях, пересчитанное в
        градусы через FOV. Шаг = HAND_TRACK_GAIN * ошибка (P-регулятор с
        мёртвой зоной и ограничением шага); цель сглаживается EMA; при потере
        цели камера держит последнее положение HAND_LOST_HOLD_S секунд.
        Направления: рука правее центра -> pan уменьшается; рука НИЖЕ центра ->
        наклон уменьшается (как стрелки камеры в веб-панели: «вверх» = +).
        Если у вашей камеры серво зеркальны — CAM_*_TRACK_DIR = -1 в config.py.
        """
        from picarpro.camera.camera_opencv import CV_HAND
        if not self._camera:
            return
        cam = self._camera
        cam.set_cv_mode(CV_HAND)
        w, h = CAMERA_RESOLUTION
        half_fov_x = CAM_FOV_DEG / 2.0
        half_fov_y = CAM_FOV_DEG * h / float(w) / 2.0
        cx, cy = w / 2.0, h / 2.0
        lo_p, hi_p = self._servo_range(SERVO_CAM_PAN)
        lo_t, hi_t = self._servo_range(SERVO_CAM_TILT)
        st = {'pan': 90.0, 'tilt': 90.0, 'ex': None, 'ey': None, 'seen': 0.0}
        new_frame = threading.Event()
        data = {'pos': [0, 0], 'found': False}

        def on_hand(pos, scale):
            data['pos'] = pos
            data['found'] = scale > 0
            new_frame.set()

        self._halt(ctx)
        self.servos.set_angle(SERVO_CAM_PAN, 90)
        self.servos.set_angle(SERVO_CAM_TILT, max(lo_t, min(hi_t, 90)))
        st['tilt'] = float(max(lo_t, min(hi_t, 90)))
        cam.cv_thread.on_hand_found = on_hand
        try:
            while ctx.alive:
                new_frame.wait(0.3)
                new_frame.clear()
                if not ctx.alive:
                    break
                now = time.monotonic()
                if data['found']:
                    st['seen'] = now
                    x, y = data['pos']
                    a = HAND_TRACK_EMA
                    st['ex'] = x if st['ex'] is None else a * x + (1 - a) * st['ex']
                    st['ey'] = y if st['ey'] is None else a * y + (1 - a) * st['ey']
                    err_x = (st['ex'] - cx) / cx * half_fov_x     # ° вправо > 0
                    err_y = (st['ey'] - cy) / cy * half_fov_y     # ° вниз > 0
                    d_pan = d_tilt = 0.0
                    if abs(err_x) > HAND_TRACK_DEADZONE_DEG:
                        d_pan = -err_x * HAND_TRACK_GAIN * CAM_PAN_TRACK_DIR
                    if abs(err_y) > HAND_TRACK_DEADZONE_DEG:
                        d_tilt = -err_y * HAND_TRACK_GAIN * CAM_TILT_TRACK_DIR
                    m = HAND_TRACK_MAX_STEP_DEG
                    d_pan = max(-m, min(m, d_pan))
                    d_tilt = max(-m, min(m, d_tilt))
                    st['pan'] = max(lo_p, min(hi_p, st['pan'] + d_pan))
                    st['tilt'] = max(lo_t, min(hi_t, st['tilt'] + d_tilt))
                    self.servos.set_angle(SERVO_CAM_PAN, int(round(st['pan'])))
                    self.servos.set_angle(SERVO_CAM_TILT, int(round(st['tilt'])))
                    self._set_info(state='track', hand=[int(x), int(y)])
                else:
                    if st['seen'] and now - st['seen'] > HAND_LOST_HOLD_S:
                        st['ex'] = st['ey'] = None      # цель потеряна: сброс сглаживания
                    self._set_info(state='search', hand=None)
        finally:
            cam.cv_thread.on_hand_found = None
            cam.set_cv_mode('none')
            self._halt(ctx)
            for sid in (SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT):
                try:
                    self.servos.set_angle(sid, 90)
                except Exception:
                    pass

    def _servo_range(self, sid):
        """(min, max) серво из SERVO_LIMITS (если сервы нет — 0..180)."""
        try:
            lim = self.servos.get_limits(sid)
            return int(lim['min']), int(lim['max'])
        except Exception:
            return 0, 180

    # =======================================================================
    # ЦВЕТ и ОХРАНА
    # =======================================================================
    def _find_color(self, ctx):
        """Следование за цветным объектом: поворот на месте при большом угловом
        рассогласовании, учёт угла панорамы камеры, качание панорамы при потере,
        предохранитель по ультразвуку (когда панорама у центра), завершение по
        AUTO_SEARCH_TIMEOUT_S."""
        from picarpro.camera.camera_opencv import CV_FINDCOLOR
        cam = self._camera
        cam.set_cv_mode(CV_FINDCOLOR)
        half_fov = CAM_FOV_DEG / 2.0
        cx_frame = CAMERA_RESOLUTION[0] / 2.0
        tol = FINDCOLOR_TOLERANCE / 100.0
        st = {'pos': [0, 0], 'found': False}
        new_frame = threading.Event()

        def on_color(pos, area):
            st['pos'] = pos
            st['found'] = pos[0] > 0
            new_frame.set()

        cam.cv_thread.on_color_found = on_color
        sweep_dir = 1
        lost_since = None
        try:
            while ctx.alive:
                new_frame.wait(0.15)
                new_frame.clear()
                if not ctx.alive:
                    break
                if st['found']:
                    lost_since = None
                    if self._front_blocked(ctx, AUTO_OBSTACLE_SLOW_CM * 0.5):
                        self._halt(ctx)         # цель близко/препятствие — стоим
                        continue
                    pan = self.servos.get_angle(SERVO_CAM_PAN)
                    px_off = (st['pos'][0] - cx_frame) / cx_frame
                    # угол до цели относительно КУЗОВА, вправо > 0
                    err_deg = px_off * half_fov + (90 - pan)
                    norm = max(-1.0, min(1.0, err_deg / half_fov))
                    if abs(pan - 90) > 2:       # возвращаем камеру в центр
                        self.servos.set_angle(
                            SERVO_CAM_PAN, pan + max(-6, min(6, 90 - pan)))
                    steer = max(30, min(150, 90 - int(
                        norm * FINDCOLOR_STEER_RANGE * 2)))
                    if abs(norm) > 0.6:         # цель далеко сбоку — крутимся
                        self._set_steer(ctx, steer)
                        self._spin(ctx, AUTO_EVADE_SPIN_SPEED,
                                   'left' if norm < 0 else 'right')
                    elif abs(norm) > tol:
                        self._set_steer(ctx, steer)
                        self._arc(ctx, FINDCOLOR_SPEED,
                                  'left' if norm < 0 else 'right', abs(norm))
                    else:
                        self._set_steer(ctx, 90)
                        self._forward(ctx, FINDCOLOR_SPEED)
                else:
                    self._halt(ctx)
                    self._set_steer(ctx, 90)
                    now = time.monotonic()
                    lost_since = lost_since or now
                    if now - lost_since > AUTO_SEARCH_TIMEOUT_S:
                        logger.info('[Auto] findColor: target lost — stopping')
                        break
                    pan = self.servos.get_angle(SERVO_CAM_PAN) + 10 * sweep_dir
                    if pan >= 150 or pan <= 30:
                        sweep_dir = -sweep_dir
                        pan = max(30, min(150, pan))
                    self.servos.set_angle(SERVO_CAM_PAN, pan)
        finally:
            cam.cv_thread.on_color_found = None
            cam.set_cv_mode('none')
            self._halt(ctx)
            self.servos.set_angle(SERVO_STEERING, 90)

    def _watch_dog(self, ctx):
        """Охрана: при движении в кадре — сирена через назначенный callback."""
        from picarpro.camera.camera_opencv import CV_WATCHDOG
        cam = self._camera
        self._motion_count = 0
        cam.set_cv_mode(CV_WATCHDOG)

        def on_motion(pixels):
            self._motion_count += 1
            logger.warning(f'[Auto] watchdog: motion detected ({pixels}px)')
            if self._on_alarm:
                try:
                    self._on_alarm(pixels)
                except Exception:
                    pass

        cam.cv_thread.on_motion = on_motion
        try:
            ctx.stop.wait()                  # спим до остановки, без опроса
        finally:
            cam.cv_thread.on_motion = None
            cam.set_cv_mode('none')
