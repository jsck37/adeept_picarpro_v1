import threading
import time

from picarpro.logger import logger
from picarpro.utils.filters import KalmanFilter1D, PID
from picarpro.utils.heading import HeadingTracker
from picarpro.drive import arc_turn
from config import (
    SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT,
    LINE_LEFT_PIN, LINE_MIDDLE_PIN, LINE_RIGHT_PIN, LINE_ACTIVE_LOW,
    CV_LINE_FOLLOW_SPEED, CV_LINE_FOLLOW_STEER_GAIN,
    CAMERA_RESOLUTION, CAM_FOV_DEG,
    CAM_PAN_TRACK_DIR, CAM_TILT_TRACK_DIR,
    FINDCOLOR_TOLERANCE, FINDCOLOR_SPEED, FINDCOLOR_STEER_RANGE,
    ULTRASONIC_SCAN_SERVO, ULTRASONIC_MAX_DISTANCE,
    AUTO_MIN_SPEED, AUTO_SPEED_SLEW_PER_S, AUTO_CRUISE_SPEED, AUTO_SLOW_SPEED,
    AUTO_OBSTACLE_STOP_CM, AUTO_OBSTACLE_SLOW_CM, AUTO_EVADE_BACKUP_S,
    AUTO_EVADE_SPIN_SPEED, AUTO_EVADE_SCAN_DEG, AUTO_EVADE_TURN_DEG,
    AUTO_SEARCH_TIMEOUT_S,
    KEEP_DISTANCE_TARGET_CM, KEEP_DISTANCE_TOLERANCE_CM,
    KEEP_DISTANCE_MAX_SPEED, KEEP_DISTANCE_KP,
    OBSTACLE_GRAB_STOP_CM, OBSTACLE_GRAB_SPEED, OBSTACLE_GRAB_STEER_TOL,
    OBSTACLE_GRAB_SEARCH_TIMEOUT_S, OBSTACLE_GRAB_RELOCATE_TURN_DEG,
    OBSTACLE_GRAB_RELOCATE_DRIVE_S, OBSTACLE_GRAB_RETURN_TURN_DEG,
    OBSTACLE_GRAB_RETURN_DRIVE_S, OBSTACLE_GRAB_CLASSES,
    MAZE_WALL_STOP_CM, MAZE_SPEED, MAZE_TURN_DIR, MAZE_TURNS,
    MAZE_BACKUP_S, MAZE_EXIT_FREE_CM, MAZE_EXIT_FREE_S, MAZE_TIMEOUT_S,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED, CRANE_GRIP_LOW, CRANE_GRIP_MID,
    SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
)

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None

MODES_WITH_ULTRA = ('radarScan', 'automatic', 'keepDistance',
                    'obstacleGrab', 'mazeU')
MODES_WITH_CAMERA = ('trackLineCV', 'trackHand', 'findColor', 'watchDog')
ALL_MODES = MODES_WITH_ULTRA + ('trackLine',) + MODES_WITH_CAMERA

_MAX_RANGE_CM = ULTRASONIC_MAX_DISTANCE * 100
_INNER_MIN = 0.3        # внутренний борт при дуге — не слабее 30 %


def _inner(amount):
    """Доля мощности внутреннего борта для «крутизны» поворота 0..1:
    0 — прямо (1.0), 1 — самая крутая дуга (0.3)."""
    return max(_INNER_MIN, 1.0 - 0.9 * min(1.0, abs(amount)))


class _RunCtx:
    """Один запуск режима. Свой Event остановки у каждого запуска: старый
    цикл не «оживает», если новый режим запущен сразу после остановки
    (раньше общий флаг _active снова становился True, и прежний режим
    продолжал работать параллельно с новым названием)."""

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
    """Автономные режимы: порт Functions.py (v1/v2) в потоковой архитектуре
    jsck37 + режимы findColor/watchDog.

    v1.5.0 — маневренность и надёжность:
    * автоматический режим: плавное замедление перед препятствием, отъезд
      назад и осмотр РАЗВОРОТАМИ НА МЕСТЕ (точно по углу при наличии
      гироскопа), выбор свободной стороны; тупик — разворот на ~150°;
    * keepDistance: пропорциональный регулятор вместо «вперёд/назад/стоп»;
    * плавный старт и нижняя граница скорости (моторы не трогаются с места
      на скважности < ~25 %), крутизна дуги растёт с отклонением (раньше
      была ИНВЕРТИРОВАНА: чем дальше линия/цель, тем слабее поворот);
    * исправлены гонка при быстрой смене режима, повторный запуск trackHand
      после «встряхивания», расчёт центра кадра по высоте в trackHand,
      вечное панорамирование findColor;
    * ультразвук включается на частый опрос только на время режима.
    """

    def __init__(self, motors, servos, ultrasonic, mpu6050=None):
        self.motors = motors
        self.servos = servos
        self.ultrasonic = ultrasonic
        self._heading = HeadingTracker(mpu6050)
        self._running = True
        self._active = False
        self._flag = threading.Event()
        self._lock = threading.RLock()
        self._motor_lock = threading.Lock()
        self._ctx = None
        self._current_mode = 'none'
        self._radar_data = []
        self._radar_points = {}
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

    def _ultra_ok(self):
        return bool(self.ultrasonic and self.ultrasonic._initialized)

    # ---- жизненный цикл режима -----------------------------------------------
    def _run(self):
        handlers = {
            'radarScan': self._radar_scan,
            'automatic': self._automatic,
            'trackLine': self._track_line,
            'trackLineCV': self._track_line_cv,
            'keepDistance': self._keep_distance,
            'trackHand': self._track_hand,
            'findColor': self._find_color,
            'watchDog': self._watch_dog,
            'obstacleGrab': self._obstacle_grab,
            'mazeU': self._maze_u,
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
                uses_ultra = ctx.mode in MODES_WITH_ULTRA and self._ultra_ok()
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

    def get_motion_count(self):
        return self._motion_count

    def shutdown(self):
        self.stop()
        self._running = False
        self._flag.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        logger.info('[Auto] shutdown')

    # ---- датчики ----------------------------------------------------------------
    def _ir_line(self, pin):
        """Семантика «линия обнаружена» для одного датчика (v1.6.2).

        Раньше get_ir_values отдавал СЫРОЙ GPIO (1 над белым полом), а UI
        трактовал 1 как LINE — виджет «IR Line Sensors» показывал LINE на
        голом полу («инвертированы»). Теперь оба читателя (_read_ir и
        get_ir_values) возвращают одинаковую семантику: True = линия.
        Полярность модуля — LINE_ACTIVE_LOW в config.py.
        """
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
        """(left, middle, right) — True = датчик НАД ЛИНИЕЙ (v1.6.2).

        Раньше возвращал сырые уровни GPIO, из-за чего UI и логика
        следования трактовали датчики в противоположных конвенциях.
        None* — модуль недоступен.
        """
        if not self._ir_available:
            return None, None, None
        return self._read_ir()

    def _safe_distance(self):
        """Дистанция для принятия решений, см, или None (нет данных).

        Фильтр Калмана гасит шум, но он инерционен (~1 с) — при быстром
        приближении к стене он «не успевает». Поэтому «быстрая атака,
        медленный спад»: берём МИНИМУМ из сырого и сглаженного значения.
        """
        raw = self.ultrasonic.get_distance_cm()
        if raw is None:
            return None
        smooth = self._distance_filter.update(raw)
        return min(raw, smooth)

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

    def _arc(self, ctx, speed, side, amount):
        """Дуга вперёд: side 'left'|'right', amount 0..1 — крутизна."""
        inner = _inner(amount)
        left, right = (inner, 1.0) if side == 'left' else (1.0, inner)
        return self._send(ctx, self.motors.move_differential,
                          self._spd(speed), 'forward', left, right)

    def _spin(self, ctx, speed, side):
        return self._send(ctx, self.motors.spin, self._spd(speed), side)

    # ---- разворот на угол -----------------------------------------------------------
    def _turn(self, ctx, degrees, use_gyro):
        """Развернуться на degrees (>0 вправо, <0 влево) — ДУГОЙ (v1.6.2).

        Раньше — spin бортами в противоположные стороны: на Аккерман-
        шасси машина не вращалась (передние колёса скребут), и объезд
        препятствий/миссии «не могли повернуться». Теперь общий с
        миссиями дуговый разворот (drive.arc_turn): руль до упора +
        ход вперёд, угол закрывается по гироскопу или по времени."""
        need = abs(degrees)
        if need < 3 or not ctx.alive:
            return
        self._halt(ctx)
        heading = self._heading if use_gyro else None
        arc_turn(self.motors, self.servos, degrees,
                 alive=ctx.alive, wait=ctx.wait, heading=heading)

    def _look(self):
        d = self.ultrasonic.measure_now()
        return _MAX_RANGE_CM if d is None else d

    # ---- радар ------------------------------------------------------------------------
    # v1.6.2: скан НЕ останавливается после одного прохода — качает датчик
    # туда-сюда, пока режим не остановят, и обновляет точки по углам.
    # Точки живут в _radar_points (угол -> дистанция), в статус уходит
    # отсортированный список — веб-канва рисует живой радар с точками.
    RADAR_STEP_DEG = 5
    RADAR_PAUSE_S = 0.12
    RADAR_SWEEP_PAUSE_S = 0.3

    def _radar_scan(self, ctx):
        if not self._ultra_ok():
            logger.warning('[Radar] HC-SR04 не инициализирован — скан невозможен')
            return
        self._radar_points = {}
        up = True
        try:
            while ctx.alive:
                rng = range(-60, 61, self.RADAR_STEP_DEG) if up \
                    else range(60, -61, -self.RADAR_STEP_DEG)
                up = not up
                for angle_off in rng:
                    if not ctx.alive:
                        break
                    self.servos.move_angle(ULTRASONIC_SCAN_SERVO, angle_off)
                    if ctx.wait(self.RADAR_PAUSE_S):   # серво доехало, эхо успокоилось
                        break
                    d = self.ultrasonic.measure_now()
                    if d is None:
                        d = self.ultrasonic.get_last_distance()
                    if not d or d <= 0:
                        # v1.6.5: «нет эха / нет данных» = свободно до 2 м.
                        # Раньше в точку уходил None/0 и веб-канва её
                        # пропускала — радар выглядел пустым («объектов нет»),
                        # хотя скан шёл. Теперь каждый угол даёт точку:
                        # препятствие — реальная дистанция, свободно — 200 см.
                        d = _MAX_RANGE_CM
                    self._radar_points[angle_off] = d
                    self._radar_data = [
                        {'angle': a, 'distance': self._radar_points[a]}
                        for a in sorted(self._radar_points)]
                if ctx.alive:
                    ctx.wait(self.RADAR_SWEEP_PAUSE_S)
        finally:
            self.servos.move_angle(ULTRASONIC_SCAN_SERVO, 0)

    # ---- автоматический объезд препятствий ----------------------------------------------
    def _automatic(self, ctx):
        if not self._ultra_ok():
            return
        self._distance_filter.reset()
        use_gyro = False
        if self._heading.available:
            self._heading.begin()
            use_gyro = self._heading.calibrate(ctx.wait)
            if not use_gyro:
                logger.info('[Auto] gyro calibration failed — timed turns')
        try:
            while ctx.alive:
                d = self._safe_distance()
                if d is None:                   # нет данных датчика — безопасность
                    self._halt(ctx)
                    ctx.wait(0.2)
                    continue
                if d <= AUTO_OBSTACLE_STOP_CM:
                    self._evade(ctx, use_gyro)
                    continue
                if d >= AUTO_OBSTACLE_SLOW_CM:
                    target = AUTO_CRUISE_SPEED
                else:   # плавное замедление между порогами
                    k = (d - AUTO_OBSTACLE_STOP_CM) / float(
                        AUTO_OBSTACLE_SLOW_CM - AUTO_OBSTACLE_STOP_CM)
                    target = AUTO_SLOW_SPEED + k * (AUTO_CRUISE_SPEED
                                                    - AUTO_SLOW_SPEED)
                self._forward(ctx, target)
                ctx.wait(0.1)
        finally:
            if self._heading.available:
                self._heading.end()

    def _evade(self, ctx, use_gyro):
        """Стоп -> назад -> осмотр разворотами -> поворот в свободную сторону.

        Датчик жёстко стоит на корпусе, поэтому «смотрим» поворотом самого
        робота (раньше качали рулевое серво, а датчик при этом оставался
        смотреть прямо — левый и правый замеры совпадали).
        """
        self._halt(ctx)
        if ctx.wait(0.15):
            return
        self._send(ctx, self.motors.move, self._spd(AUTO_SLOW_SPEED),
                   'backward', 'no', 0.5)
        ctx.wait(AUTO_EVADE_BACKUP_S)
        self._halt(ctx)
        scan = AUTO_EVADE_SCAN_DEG
        self._turn(ctx, +scan, use_gyro)
        d_right = self._look()
        self._turn(ctx, -2 * scan, use_gyro)    # теперь смотрим влево
        d_left = self._look()
        go_left = d_left >= d_right
        if max(d_left, d_right) <= AUTO_OBSTACLE_STOP_CM * 1.5:
            turn_to = 150.0                      # тупик — разворот почти назад
        else:
            turn_to = float(AUTO_EVADE_TURN_DEG)
        # сейчас курс = -scan (влево от исходного); целевой = ∓turn_to
        target = -turn_to if go_left else +turn_to
        self._turn(ctx, target - (-scan), use_gyro)
        self._distance_filter.reset()
        logger.info(f'[Auto] evade: L={d_left:.0f} R={d_right:.0f} -> '
                    f'{"left" if go_left else "right"} {turn_to:.0f}°')

    # ---- следование по линии (ИК-датчики) -------------------------------------------------
    def _track_line(self, ctx):
        if not self._ir_available:
            logger.warning('[Auto] IR sensors not available')
            return
        speed = 35
        while ctx.alive:
            left, mid, right = self._read_ir()
            if mid:
                if left and not right:
                    self._arc(ctx, speed, 'left', 0.5)
                elif right and not left:
                    self._arc(ctx, speed, 'right', 0.5)
                else:
                    self._forward(ctx, speed)
            elif left and not right:
                self._arc(ctx, speed - 10, 'left', 0.5)
            elif right and not left:
                self._arc(ctx, speed - 10, 'right', 0.5)
            else:
                self._forward(ctx, 15)          # линия потеряна — ползём
            ctx.wait(0.05)

    # ---- следование по линии (камера + ИК) --------------------------------------------------
    def _track_line_cv(self, ctx):
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
                # ждём НОВЫЙ кадр, а не молотим одни и те же данные с частотой 33 Гц
                got = new_frame.wait(0.25)
                new_frame.clear()
                if not ctx.alive:
                    break
                ir_l, ir_m, ir_r = self._read_ir()
                ir_off = 0.0
                if ir_ok and ir_l != ir_r:
                    ir_off = -1.0 if ir_l else 1.0
                if got and state['found']:
                    p1, p2 = state['pos']
                    line_c = (p1 + p2) / 2.0 if (p1 > 0 and p2 > 0) else (p1 or p2)
                    raw = (line_c - centre_x) / centre_x
                    lost = 0
                    smooth = 0.5 * raw + 0.5 * smooth
                    last_offset = smooth
                    fused = pid.update(smooth)
                    if ir_off:
                        fused = max(-1.0, min(1.0, fused + ir_off * 0.25))
                    self._set_steer(ctx, max(30, min(150,
                                    90 - int(fused * 60 * gain))))
                    # чем круче поворот, тем медленнее едем
                    s = max(AUTO_MIN_SPEED, speed * (1.0 - 0.5 * abs(fused)))
                    if abs(fused) > 0.25:
                        self._arc(ctx, s, 'left' if fused < 0 else 'right',
                                  abs(fused))
                    else:
                        self._forward(ctx, s)
                else:
                    lost += 1
                    pid.reset()
                    if ir_ok and ir_l != ir_r:
                        side = 'left' if ir_l else 'right'
                        self._arc(ctx, AUTO_MIN_SPEED, side, 0.7)
                        self._set_steer(ctx, 120 if ir_l else 60)
                    elif lost < 8:   # ~1 с: ещё помним, куда уходила линия
                        self._set_steer(ctx, max(30, min(150,
                                        90 - int(last_offset * 60 * gain))))
                        side = 'left' if last_offset < 0 else 'right'
                        if abs(last_offset) > 0.2:
                            self._arc(ctx, AUTO_MIN_SPEED, side, 0.6)
                        else:
                            self._forward(ctx, AUTO_MIN_SPEED)
                    else:            # линии нет давно — не едем вслепую
                        self._halt(ctx)
                        self._set_steer(ctx, 90)
        finally:
            cam.cv_thread.on_line_found = None
            cam.set_cv_mode('none')

    # ---- удержание дистанции (П-регулятор) ----------------------------------------------------
    def _keep_distance(self, ctx):
        if not self._ultra_ok():
            return
        self._distance_filter.reset()
        moving = False
        while ctx.alive:
            d = self._safe_distance()
            if d is None:
                self._halt(ctx)
                moving = False
                ctx.wait(0.2)
                continue
            err = d - KEEP_DISTANCE_TARGET_CM       # >0 — далеко, едем вперёд
            band = KEEP_DISTANCE_TOLERANCE_CM * (0.5 if moving else 1.0)
            if abs(err) <= band:                    # гистерезис против дребезга
                self._halt(ctx)
                moving = False
            else:
                moving = True
                v = min(KEEP_DISTANCE_MAX_SPEED, KEEP_DISTANCE_KP * abs(err))
                if err > 0:
                    self._forward(ctx, v)
                else:
                    self._send(ctx, self.motors.move, self._spd(v),
                               'backward', 'no', 0.5)
            ctx.wait(0.1)

    # ---- слежение за рукой ------------------------------------------------------------------------
    def _track_hand(self, ctx):
        from picarpro.camera.camera_opencv import CV_HAND
        if not self._camera:
            return
        cam = self._camera
        cam.set_cv_mode(CV_HAND)
        # v1.6.5: ax/ay — аккумуляторы дробных шагов. Раньше int() на шаге
        # съедал малые отклонения (при |sy| < ~0.29 кадра шаг был 0) и наклон
        # камеры ВВЕРХ/ВНИЗ почти не двигался — выглядело как «следит только
        # вправо/влево». Отключение по «встряске» убрано: режим идёт, пока
        # владелец не остановит его сам.
        s = {'pan': 90, 'tilt': 90, 'sx': 0.0, 'sy': 0.0, 'ax': 0.0, 'ay': 0.0}
        self.servos.set_angle(SERVO_CAM_PAN, 90)
        self.servos.set_angle(SERVO_CAM_TILT, 90)
        self._halt(ctx)
        PAN_STEP, TILT_STEP, DEADZONE, A = 4, 3, 0.06, 0.5
        PAN_DIR, TILT_DIR = CAM_PAN_TRACK_DIR, CAM_TILT_TRACK_DIR
        cx = CAMERA_RESOLUTION[0] / 2.0
        cy = CAMERA_RESOLUTION[1] / 2.0     # раньше брали ширину*0.75 — случайно верно для 4:3

        def on_hand(pos, area):
            if area == 0 or not ctx.alive:
                return False
            s['sx'] = A * ((pos[0] - cx) / cx) + (1 - A) * s['sx']
            s['sy'] = A * ((pos[1] - cy) / cy) + (1 - A) * s['sy']
            if abs(s['sx']) > DEADZONE:
                # минус — направление панорамы как в v1.6.4 (рука правее
                # центра => pan уменьшается); DIR-флаг меняет при зеркальном
                # креплении серво
                s['ax'] += -s['sx'] * PAN_STEP * (1 + abs(s['sx'])) * PAN_DIR
                step = int(s['ax'])
                if step:
                    s['ax'] -= step
                    s['pan'] = max(0, min(180, s['pan'] + step))
            if abs(s['sy']) > DEADZONE:
                s['ay'] += s['sy'] * TILT_STEP * (1 + abs(s['sy'])) * TILT_DIR
                step = int(s['ay'])
                if step:
                    s['ay'] -= step
                    s['tilt'] = max(0, min(180, s['tilt'] + step))
            self.servos.set_angle(SERVO_CAM_PAN, s['pan'])
            self.servos.set_angle(SERVO_CAM_TILT, s['tilt'])
            return False

        cam.cv_thread.on_hand_found = on_hand
        try:
            ctx.stop.wait()                  # ни одного пробуждения до остановки
        finally:
            cam.cv_thread.on_hand_found = None
            cam.set_cv_mode('none')
            self._halt(ctx)
            for sid in (SERVO_STEERING, SERVO_CAM_PAN, SERVO_CAM_TILT):
                self.servos.set_angle(sid, 90)

    # ---- слежение за цветом ------------------------------------------------------------------------
    def _find_color(self, ctx):
        """Следование за цветным объектом — порт findColor из v1/v2.

        Улучшения: поворот на месте при большом угловом рассогласовании,
        учёт текущего угла панорамы камеры (объект, найденный при повёрнутой
        камере, виден в кадре не там, куда надо ехать), качание панорамы
        «туда-обратно», завершение по AUTO_SEARCH_TIMEOUT_S.
        """
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
                    if abs(norm) > 0.6:         # цель далеко сбоку — крутимся на месте
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

    # ---- охрана ----------------------------------------------------------------------------------------------
    def _watch_dog(self, ctx):
        """Охрана — порт Camera_WatchDog из v1/v2: при движении в кадре
        зуммер/сирена через назначенный callback."""
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

    # ---- захват и перенос препятствия (v1.6.7) --------------------------------
    def _yolo_objects(self):
        """Объекты YOLO-детектора камеры (уже отсортированы по площади),
        отфильтрованные по OBSTACLE_GRAB_CLASSES. Пусто, если камера/YOLO
        недоступны или ничего не найдено."""
        cam = self._camera
        if not cam:
            return []
        y = getattr(cam.cv_thread, 'yolo', None)
        if not (y and y.loaded):
            return []
        if y._objects_lock is not None:
            with y._objects_lock:
                objs = list(y.objects)
        else:
            objs = list(y.objects)
        if OBSTACLE_GRAB_CLASSES:
            objs = [o for o in objs if o['label'] in OBSTACLE_GRAB_CLASSES]
        return objs

    def _crane_sequence(self, ctx, steps):
        """Последовательность движений клешни: [(servo, angle, steps, delay)].
        Любое исключение серво не роняет режим."""
        try:
            for sid, angle, n, delay in steps:
                self.servos.smooth_move(sid, angle, steps=n, delay=delay)
                if ctx.wait(0.1):
                    return False
        except Exception as e:
            logger.warning(f'[Grab] crane error: {e}')
            return False
        return True

    def _obstacle_grab(self, ctx):
        """«Препятствие» (v1.6.7): подъезд к препятствию, захват клешнёй,
        перенос в сторону и возврат. Цель — крупнейший YOLO-объект в кадре
        (доворот по центру объекта); если камера/YOLO не дают объектов —
        подъезд по ультразвуку «вслепую». Режим одноразовый: после переноса
        завершается."""
        if not (self._ultra_ok() and self.servos):
            logger.warning('[Grab] нужны ультразвук и серво')
            return
        from picarpro.camera.camera_opencv import CV_YOLO
        cam = self._camera
        if cam:
            cam.set_cv_mode(CV_YOLO)     # на видео видно, за чем едет робот
        self._distance_filter.reset()
        cx_frame = CAMERA_RESOLUTION[0] / 2.0
        st = 'search'
        try:
            while ctx.alive:
                if st == 'search':
                    objs = self._yolo_objects()
                    d = self._safe_distance()
                    if objs and cam:
                        err = (objs[0]['cx'] - cx_frame) / cx_frame
                        if abs(err) > OBSTACLE_GRAB_STEER_TOL:
                            self._set_steer(ctx, max(30, min(150,
                                            90 - int(err * 60))))
                            self._arc(ctx, OBSTACLE_GRAB_SPEED,
                                      'left' if err < 0 else 'right',
                                      min(1.0, abs(err)))
                        else:
                            self._set_steer(ctx, 90)
                            self._forward(ctx, OBSTACLE_GRAB_SPEED)
                    elif d is not None:
                        # цели нет в кадре — едем вперёд, ультразвук подстрахует
                        self._set_steer(ctx, 90)
                        self._forward(ctx, OBSTACLE_GRAB_SPEED)
                    else:
                        self._halt(ctx)
                    if d is not None and d <= OBSTACLE_GRAB_STOP_CM:
                        self._halt(ctx)
                        logger.info(f'[Grab] target at {d:.0f} см — захват')
                        st = 'grab'
                    if ctx.wait(0.12):
                        break
                elif st == 'grab':
                    self._crane_sequence(ctx, [
                        (SERVO_CRANE_GRIP, CRANE_GRIP_LOW, 8, 0.03),
                        (SERVO_CRANE_ARM, CRANE_ARM_CLOSED, 14, 0.05),
                        (SERVO_CRANE_GRIP, CRANE_GRIP_MID, 8, 0.03),
                    ])
                    st = 'relocate'
                elif st == 'relocate':
                    logger.info('[Grab] relocating obstacle')
                    self._turn(ctx, OBSTACLE_GRAB_RELOCATE_TURN_DEG,
                               self._heading.available)
                    for _ in range(int(OBSTACLE_GRAB_RELOCATE_DRIVE_S / 0.1)):
                        if not self._forward(ctx, OBSTACLE_GRAB_SPEED) \
                                or ctx.wait(0.1):
                            break
                    self._halt(ctx)
                    self._crane_sequence(ctx, [
                        (SERVO_CRANE_GRIP, CRANE_GRIP_LOW, 8, 0.03),
                        (SERVO_CRANE_ARM, CRANE_ARM_OPEN, 14, 0.05),
                        (SERVO_CRANE_GRIP, CRANE_GRIP_MID, 8, 0.03),
                    ])
                    st = 'return'
                elif st == 'return':
                    logger.info('[Grab] returning')
                    self._turn(ctx, OBSTACLE_GRAB_RETURN_TURN_DEG,
                               self._heading.available)
                    for _ in range(int(OBSTACLE_GRAB_RETURN_DRIVE_S / 0.1)):
                        if not self._forward(ctx, OBSTACLE_GRAB_SPEED) \
                                or ctx.wait(0.1):
                            break
                    self._halt(ctx)
                    logger.info('[Grab] done — obstacle relocated')
                    break
        finally:
            if cam:
                try:
                    cam.set_cv_mode('none')
                except Exception:
                    pass
            self._halt(ctx)
            if self.servos:
                try:
                    self.servos.set_angle(SERVO_CRANE_GRIP, CRANE_GRIP_MID)
                    self.servos.set_angle(SERVO_CRANE_ARM, CRANE_ARM_OPEN)
                except Exception:
                    pass

    # ---- U-образный лабиринт (v1.6.7) -----------------------------------------
    def _maze_u(self, ctx):
        """Проход лёгкого U-образного лабиринта по ультразвуку: коридор до
        стены -> поворот на 90° -> второй коридор -> поворот на 90° ->
        выходной коридор (суммарный разворот 180°). Направление поворотов —
        MAZE_TURN_DIR, число поворотов — MAZE_TURNS. Камера (если есть)
        включается только для наблюдения, на управление не влияет.
        Выход: после всех поворотов впереди свободно MAZE_EXIT_FREE_S секунд
        (или сработал предохранитель MAZE_TIMEOUT_S)."""
        if not self._ultra_ok():
            logger.warning('[Maze] HC-SR04 не инициализирован')
            return
        cam = self._camera
        if cam:
            try:
                cam.set_cv_mode('none')
            except Exception:
                pass
        self._distance_filter.reset()
        use_gyro = False
        if self._heading.available:
            self._heading.begin()
            use_gyro = self._heading.calibrate(ctx.wait)
        turn_deg = 90.0 if MAZE_TURN_DIR == 'right' else -90.0
        try:
            turns_done = 0
            free_since = None
            deadline = time.monotonic() + MAZE_TIMEOUT_S
            while ctx.alive and time.monotonic() < deadline:
                d = self._safe_distance()
                if d is None:                # нет данных — стоим (безопасность)
                    self._halt(ctx)
                    ctx.wait(0.2)
                    continue
                wall = d <= MAZE_WALL_STOP_CM
                if turns_done < MAZE_TURNS and wall:
                    self._halt(ctx)
                    self._send(ctx, self.motors.move,
                               self._spd(AUTO_SLOW_SPEED),
                               'backward', 'no', 0.5)
                    ctx.wait(MAZE_BACKUP_S)
                    self._halt(ctx)
                    self._turn(ctx, turn_deg, use_gyro)
                    turns_done += 1
                    self._distance_filter.reset()
                    free_since = None
                    logger.info(f'[Maze] turn {turns_done}/{MAZE_TURNS} '
                                f'({MAZE_TURN_DIR})')
                elif turns_done >= MAZE_TURNS:
                    if wall:
                        # стен больше, чем ожидалось — поворачиваем дальше
                        # (страховка на случай «не-U» конфигурации)
                        self._turn(ctx, turn_deg, use_gyro)
                        turns_done += 1
                        self._distance_filter.reset()
                        free_since = None
                        continue
                    if d >= MAZE_EXIT_FREE_CM:
                        free_since = free_since or time.monotonic()
                        if time.monotonic() - free_since >= MAZE_EXIT_FREE_S:
                            logger.info('[Maze] exit reached')
                            break
                    else:
                        free_since = None
                    self._forward(ctx, MAZE_SPEED)
                else:
                    self._forward(ctx, MAZE_SPEED)
                ctx.wait(0.1)
            if ctx.alive and time.monotonic() >= deadline:
                logger.warning('[Maze] timeout — stopping')
        finally:
            if self._heading.available:
                self._heading.end()
            self._halt(ctx)
            self._set_steer(ctx, 90)
