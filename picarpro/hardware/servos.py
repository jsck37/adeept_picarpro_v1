import threading, time
from picarpro.logger import logger
from config import (
    I2C_BUS, PCA9685_SERVO_ADDR, PCA9685_SERVO_FREQ,
    SERVO_COUNT, SERVO_MIN_PULSE, SERVO_MAX_PULSE, SERVO_INIT_ANGLE,
    SERVO_INIT_ANGLES, SERVO_LIMITS, SERVO_MOVE_ON_BOOT,
    SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
    CRANE_ARM_OPEN, CRANE_GRIP_HIGH,
)

try:
    from smbus2 import SMBus
    _HAS_SMBUS = True
except ImportError:
    _HAS_SMBUS = False
    SMBus = None

# Лимиты серво задаются ТОЛЬКО в config.py (SERVO_LIMITS) — v1.3.0.
# Файл калибровки servo_cal.json упразднён: редактирование лимитов
# из веб-интерфейса удалено, чтобы их нельзя было случайно изменить.

# Троттлинг диагностического лога при недоступном PCA9685 (v1.4.0):
# ретраи идут каждые HW_RETRY_INTERVAL_S, но подсказка печатается
# раз в SERVO_DIAG_INTERVAL_S — иначе журнал заливается.
SERVO_DIAG_INTERVAL_S = 300.0

# ---------------------------------------------------------------------------
# Драйвер PCA9685 поверх smbus2 (v1.4.1).
#
# Раньше здесь был стек adafruit_pca9685 -> Adafruit-Blinka -> busio.
# На Debian Trixie свежий Blinka при импорте требует отдельный пакет
# платформы «adafruit_blinka_raspberry_pi_pwm» — без него import busio
# падает («The platform library ... was not found»), ServoController не
# создавался и панель серво в UI оставалась «Module not found».
# PCA9685 — обычная I2C-микросхема: для управления серво достаточно
# шести регистров smbus2, весь Blinka-стек (board/busio/adafruit_*)
# больше не нужен.
# ---------------------------------------------------------------------------
_PCA_MODE1 = 0x00
_PCA_MODE2 = 0x01
_PCA_LED0_ON_L = 0x06        # на канал 4 регистра: ON_L, ON_H, OFF_L, OFF_H
_PCA_ALL_LED_OFF_H = 0xFD    # бит 0x10 = full-off всех 16 каналов
_PCA_PRE_SCALE = 0xFE
_PCA_MODE1_SLEEP = 0x10
_PCA_MODE1_AI = 0x20         # автоинкремент адреса регистра
_PCA_MODE1_RESTART = 0x80
_PCA_MODE2_OUTDRV = 0x04     # totem-pole выход (как в adafruit_pca9685)
_PCA_OSC_HZ = 25_000_000.0   # внутренний генератор PCA9685
_PCA_STEPS = 4096            # разрешение одного PWM-периода


class PCA9685:
    """Минимальный драйвер PCA9685 через smbus2.

    Семантика частоты/каналов повторяет adafruit_pca9685, чтобы углы
    (SERVO_MIN_PULSE..SERVO_MAX_PULSE мкс) остались прежними.
    """

    def __init__(self, bus=I2C_BUS, address=PCA9685_SERVO_ADDR,
                 frequency=PCA9685_SERVO_FREQ):
        self.address = address
        self.frequency = frequency
        self._smbus = SMBus(bus)
        try:
            self._write_u8(_PCA_MODE2, _PCA_MODE2_OUTDRV)
            self._write_u8(_PCA_MODE1, _PCA_MODE1_AI)
            self.set_frequency(frequency)
        except Exception:
            self.close()
            raise

    def _write_u8(self, reg, value):
        self._smbus.write_byte_data(self.address, reg, int(value) & 0xFF)

    def _read_u8(self, reg):
        return self._smbus.read_byte_data(self.address, reg)

    def set_frequency(self, frequency):
        """Смена частоты PWM: SLEEP -> PRE_SCALE -> wake + RESTART
        (datasheet NXP PCA9685, п. 7.3.1). 50 Гц -> prescale 121."""
        frequency = max(24.0, min(1526.0, float(frequency)))
        prescale = round(_PCA_OSC_HZ / (_PCA_STEPS * frequency)) - 1
        prescale = max(3, min(255, int(prescale)))
        old_mode1 = self._read_u8(_PCA_MODE1)
        self._write_u8(_PCA_MODE1,
                       (old_mode1 & ~_PCA_MODE1_RESTART) | _PCA_MODE1_SLEEP)
        self._write_u8(_PCA_PRE_SCALE, prescale)
        self._write_u8(_PCA_MODE1,
                       (old_mode1 & ~_PCA_MODE1_SLEEP) | _PCA_MODE1_RESTART)
        time.sleep(0.005)

    def set_channel_pulse(self, channel, on_counts, off_counts):
        """Установить ON/OFF-счётчики канала (0..4095) одной блочной
        записью — один I2C-транзакция на команду серво."""
        channel = max(0, min(15, int(channel)))
        on_counts = max(0, min(4095, int(on_counts)))
        off_counts = max(0, min(4095, int(off_counts)))
        reg = _PCA_LED0_ON_L + 4 * channel
        self._smbus.write_i2c_block_data(
            self.address, reg,
            [on_counts & 0xFF, (on_counts >> 8) & 0x0F,
             off_counts & 0xFF, (off_counts >> 8) & 0x0F])

    def all_channels_off(self):
        """Full-off всех каналов (сервы перестают получать импульсы
        и не потребляют ток — как при ленивом старте v1.3.2)."""
        self._write_u8(_PCA_ALL_LED_OFF_H, 0x10)

    def close(self):
        try:
            self._smbus.close()
        except Exception:
            pass


class _ServoChannel:
    """Один канал PCA9685 с семантикой adafruit_motor.Servo:
    угол -> импульс min_pulse..max_pulse мкс -> счётчики PWM.
    Сохраняет прежнее соответствие углов (v1.1.0+), чтобы лимиты
    SERVO_LIMITS из config.py остались валидными."""

    def __init__(self, pca, channel, min_pulse, max_pulse, actuation_range):
        self._pca = pca
        self.channel = channel
        self.min_pulse = min_pulse
        self.max_pulse = max_pulse
        self.actuation_range = actuation_range
        self._angle = None

    @property
    def angle(self):
        return self._angle

    @angle.setter
    def angle(self, value):
        if value is None:
            self._angle = None
            self._pca.set_channel_pulse(self.channel, 0, 0)
            return
        if value < 0 or value > self.actuation_range:
            raise ValueError(
                f'angle {value} out of range 0..{self.actuation_range}')
        self._angle = float(value)
        pulse_us = self.min_pulse + (self.max_pulse - self.min_pulse) \
            * (float(value) / self.actuation_range)
        off = int(pulse_us * self._pca.frequency / 1_000_000 * _PCA_STEPS)
        self._pca.set_channel_pulse(self.channel, 0, off)


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
        if not _HAS_SMBUS:
            logger.warning('[Servos] smbus2 not available '
                           '(pip install smbus2)')
            return
        self._init_pca9685()

    def _init_pca9685(self):
        try:
            self._pca = PCA9685(bus=I2C_BUS, address=PCA9685_SERVO_ADDR,
                                frequency=PCA9685_SERVO_FREQ)
            time.sleep(0.05)
            for i in range(SERVO_COUNT):
                try:
                    lim = self._limits[i]
                    actuation_range = max(int(lim['max']), 180)
                    self._servos[i] = _ServoChannel(
                        self._pca, i,
                        min_pulse=SERVO_MIN_PULSE,
                        max_pulse=SERVO_MAX_PULSE,
                        actuation_range=actuation_range)
                    init_angle = SERVO_INIT_ANGLES.get(i)
                    if init_angle is None:
                        # каналы клешни — из config (v1.6.0: захват на 5,
                        # наклон на 4); раньше каналы были захардкожены
                        init_angle = (CRANE_ARM_OPEN
                                      if i == SERVO_CRANE_ARM else
                                      (CRANE_GRIP_HIGH
                                       if i == SERVO_CRANE_GRIP else
                                       SERVO_INIT_ANGLE))
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
            if self._pca is not None:
                self._pca.close()
                self._pca = None
            self._log_diag(e)

    @classmethod
    def _log_diag(cls, err):
        """Диагностика недоступного PCA9685 — не чаще SERVO_DIAG_INTERVAL_S
        (v1.4.0): причина «сервоприводы не отображаются» в UI — модуль
        не ответил на шине в момент старта; ретраи идут фоном, см.
        SharedState._hw_retry_loop."""
        now = time.monotonic()
        if cls._last_diag_ts and now - cls._last_diag_ts < SERVO_DIAG_INTERVAL_S:
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
        # серво неизвестно (при старте серво не двигали, v1.3.2).
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

    def release(self, sid):
        """Снять удержание серво (PWM выключается: нет тока и нагрева).

        Следующий set_angle запишет угол заново — положение после снятия
        считается неизвестным (_angles[sid] = None).
        """
        if (not self._pwm_initialized or not 0 <= sid < SERVO_COUNT
                or self._servos[sid] is None or self._angles[sid] is None):
            return False
        with self._lock:
            try:
                self._servos[sid].angle = None
                self._angles[sid] = None
                return True
            except Exception as e:
                logger.warning(f'[Servos] S{sid} release failed: {e}')
                return False

    def is_held(self, sid):
        """Серво сейчас получает импульсы (его двигали после загрузки/снятия)."""
        return 0 <= sid < SERVO_COUNT and self._angles[sid] is not None

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

    def shutdown(self):
        if self._pca is not None:
            try:
                self._pca.all_channels_off()
            except Exception:
                pass
            self._pca.close()
            self._pca = None
        self._pwm_initialized = False
        logger.info('[Servos] shutdown')
