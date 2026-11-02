import threading, time
from picarpro.logger import logger
from config import (
    PCA9685_SERVO_ADDR, PCA9685_SERVO_FREQ, I2C_BUS,
    SERVO_COUNT, SERVO_MIN_PULSE, SERVO_MAX_PULSE, SERVO_INIT_ANGLE,
    SERVO_INIT_ANGLES, SERVO_LIMITS, SERVO_MOVE_ON_BOOT,
    CRANE_ARM_OPEN, CRANE_GRIP_HIGH,
)

try:
    import busio
    from adafruit_pca9685 import PCA9685
    from adafruit_motor import servo as adafruit_servo
    _HAS_PCA = True
except ImportError:
    _HAS_PCA = False

# Лимиты серво задаются ТОЛЬКО в config.py (SERVO_LIMITS) — v1.3.0.
# Файл калибровки servo_cal.json упразднён: редактирование лимитов
# из веб-интерфейса удалено, чтобы их нельзя было случайно изменить.

# Троттлинг диагностического лога при недоступном PCA9685 (v1.4.0):
# ретраи идут каждые HW_RETRY_INTERVAL_S, но подсказка печатается
# раз в SERVO_DIAG_INTERVAL_S — иначе журнал заливается.
SERVO_DIAG_INTERVAL_S = 300.0


class ServoController:
    _last_diag_ts = 0.0   # классовое поле: общее для всех инстансов

    def __init__(self):
        self._pca = None
        self._servos = [None] * SERVO_COUNT
        # v1.3.2: _angles — ФИЗИЧЕСКИ записанный в PWM угол; None значит
        # «серво ещё не двигали с загрузки» (ленивая инициализация).
        self._angles = [None] * SERVO_COUNT
        self._init_angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._lock = threading.Lock()
        self._pwm_initialized = False
        self._limits = {i: dict(SERVO_LIMITS.get(i, {"min": 0, "max": 180}))
                        for i in range(SERVO_COUNT)}
        if not _HAS_PCA:
            logger.warning('[Servos] adafruit_pca9685 not available')
            return
        self._init_pca9685()

    def _init_pca9685(self):
        try:
            self._i2c = busio.I2C(3, 2)
            self._pca = PCA9685(self._i2c, address=PCA9685_SERVO_ADDR)
            self._pca.frequency = PCA9685_SERVO_FREQ
            time.sleep(0.05)
            for i in range(SERVO_COUNT):
                try:
                    lim = self._limits[i]
                    actuation_range = max(int(lim['max']), 180)
                    self._servos[i] = adafruit_servo.Servo(
                        self._pca.channels[i],
                        min_pulse=SERVO_MIN_PULSE, max_pulse=SERVO_MAX_PULSE,
                        actuation_range=actuation_range)
                    init_angle = SERVO_INIT_ANGLES.get(i)
                    if init_angle is None:
                        init_angle = CRANE_ARM_OPEN if i == 6 else (
                            CRANE_GRIP_HIGH if i == 5 else SERVO_INIT_ANGLE)
                    init_angle = self._clamp(i, init_angle)
                    self._init_angles[i] = init_angle
                    if SERVO_MOVE_ON_BOOT:
                        # Старое поведение — только для сборки качалок по
                        # мануалу: все серво выходят в нейтраль при загрузке.
                        self._servos[i].angle = init_angle
                        self._angles[i] = init_angle
                        time.sleep(0.03)
                    # Иначе (v1.3.2) PWM НЕ пишем: каналы PCA9685 остаются
                    # выключенными, серво не получают импульсов и не жрут
                    # ток — пиковый бросок в момент старта сервиса устранён
                    # (просадка питания = ребут Pi, см. мануал Adeept).
                    time.sleep(0.005)
                except Exception as e:
                    logger.warning(f'[Servos] S{i} init failed: {e}')
            self._pwm_initialized = True
            mode = ('boot neutral' if SERVO_MOVE_ON_BOOT
                    else 'lazy: no PWM at boot')
            logger.info(f'[Servos] PCA9685 OK '
                        f'({sum(s is not None for s in self._servos)}/'
                        f'{SERVO_COUNT} channels, {mode})')
        except Exception as e:
            self._log_diag(e)

    @classmethod
    def _log_diag(cls, err):
        """Диагностика недоступного PCA9685 — не чаще SERVO_DIAG_INTERVAL_S
        (v1.4.0): причина «сервоприводы не отображаются» в UI — модуль
        не ответил на шине в момент старта; ретраи идут фоном, см.
        SharedState._hw_retry_loop."""
        import time as _time
        now = _time.monotonic()
        if now - cls._last_diag_ts < SERVO_DIAG_INTERVAL_S:
            return
        cls._last_diag_ts = now
        logger.error(f'[Servos] PCA9685 init failed: {err}')
        logger.error(
            '[Servos] подсказки: i2cdetect -y 1 (PCA9685 должен быть на 0x40); '
            'если шина I2C «висит» — проверьте подключение MPU6050/датчиков; '
            'питание серво (V+) от батареи, не от 5V Pi. '
            'Модуль подхватится автоматически (авто-ретраи v1.4.0) — '
            'перезапуск сервиса не нужен.')

    def _clamp(self, sid, angle):
        lim = self._limits.get(sid, {'min': 0, 'max': 180})
        return max(lim['min'], min(lim['max'], angle))

    def set_angle(self, sid, angle):
        if (not self._pwm_initialized or sid < 0
                or sid >= SERVO_COUNT or self._servos[sid] is None):
            return False
        angle = self._clamp(sid, angle)
        cur = self._angles[sid]
        # Анти-джиттер (v1.3.0): повторная команда на тот же угол (в т.ч.
        # keepalive-повторы джойстика каждые 150 мс) не пишет PWM в
        # PCA9685 — лишние перезаписи регистров дают дрожание и нагрев.
        # Первый после загрузки угол пишется ВСЕГДА: физическое положение
        # серво неизвестно (при старте серво не двигались, v1.3.2).
        if cur is not None and abs(cur - angle) < 0.5:
            return True
        with self._lock:
            try:
                self._servos[sid].angle = angle
                self._angles[sid] = angle
                return True
            except Exception as e:
                logger.warning(f'[Servos] S{sid} set_angle({angle}) failed: {e}')
                return False

    def move_angle(self, sid, offset):
        if 0 <= sid < SERVO_COUNT:
            self.set_angle(sid, self._init_angles[sid] + offset)

    def smooth_move(self, sid, target, steps=10, delay=0.02):
        if not self._pwm_initialized or sid >= SERVO_COUNT:
            return
        target = self._clamp(sid, target)
        cur = self._angles[sid]
        if cur is None:
            # Позиция неизвестна (ленивый старт, v1.3.2) — один прямой ход
            # вместо интерполяции из вымышленной точки.
            self.set_angle(sid, target)
            return
        if abs(cur - target) < 1:
            return
        start = cur
        delta = (target - start) / steps
        for i in range(1, steps + 1):
            if not self.set_angle(sid, start + delta * i):
                break
            time.sleep(delay)

    def move_init(self):
        for i in range(SERVO_COUNT):
            if self._servos[i]:
                self.smooth_move(i, self._init_angles[i], steps=15, delay=0.02)

    def set_init_angle(self, sid, angle):
        if 0 <= sid < SERVO_COUNT:
            self._init_angles[sid] = self._clamp(sid, angle)

    def get_angle(self, sid):
        if not 0 <= sid < SERVO_COUNT:
            return 0
        # Пока серво не двигали (None) — возвращаем целевой init-угол
        return (self._angles[sid] if self._angles[sid] is not None
                else self._init_angles[sid])

    def get_angles(self):
        """Углы всех серво для статуса/UI: записанные, для ещё не
        двигавшихся — целевые init-углы (v1.3.2, ленивый старт)."""
        return [self.get_angle(i) for i in range(SERVO_COUNT)]

    def get_limits(self, sid=None):
        """Лимиты из config.py (только чтение; редактирования больше нет)."""
        if sid is not None:
            return self._limits.get(sid, {'min': 0, 'max': 180})
        return dict(self._limits)

    def stop_all(self):
        pass

    def shutdown(self):
        self.stop_all()
        if self._pwm_initialized and self._pca:
            try:
                self._pca.deinit()
            except Exception:
                pass
        logger.info('[Servos] shutdown')
