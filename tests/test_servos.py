"""Тесты ServoController (v1.4.1: PCA9685 напрямую через smbus2).

Фейк — регистровая модель PCA9685 на фейковой шине SMBus
(tests.fakes.all_fakes.make_pca9685_device): тесты читают регистры
LED0_ON/OFF каналов и журнал записей FakeSMBus.write_log.
"""
import pytest

from tests.fakes.all_fakes import (
    FakeSMBus, make_pca9685_device,
    PCA_MODE1, PCA_MODE2, PCA_LED0_ON_L, PCA_ALL_LED_OFF_H, PCA_PRE_SCALE,
)

ADDR = 0x40
# config.py: 50 Гц, импульсы 500..2400 мкс
PRESCALE_50HZ = round(25_000_000 / (4096 * 50)) - 1   # = 121 (datasheet NXP)


def _dev():
    return FakeSMBus.shared_devices[ADDR]


def chan_counts(channel):
    """(ON, OFF) счётчики канала из регистровой модели."""
    base = PCA_LED0_ON_L + 4 * channel
    dev = _dev()
    on = dev.get(base, 0) | (dev.get(base + 1, 0) << 8)
    off = dev.get(base + 2, 0) | (dev.get(base + 3, 0) << 8)
    return on, off


def chan_write_count(channel):
    """Сколько раз писали LED-регистры канала (тест анти-джиттера)."""
    lo = PCA_LED0_ON_L + 4 * channel
    return sum(1 for addr, reg, _ in FakeSMBus.write_log
               if addr == ADDR and lo <= reg <= lo + 3)


def led_write_count():
    """Записей в LED-регистры любого канала (тест ленивого старта)."""
    return sum(1 for addr, reg, _ in FakeSMBus.write_log
               if addr == ADDR and PCA_LED0_ON_L <= reg < PCA_ALL_LED_OFF_H)


@pytest.fixture
def servos():
    import picarpro.hardware.servos as mod
    from picarpro.hardware.servos import ServoController
    make_pca9685_device()
    s = ServoController()
    yield s
    s.shutdown()


class TestInit:
    def test_pca_registers_configured(self, servos):
        dev = _dev()
        assert dev.get(PCA_PRE_SCALE) == PRESCALE_50HZ      # 50 Гц
        assert dev.get(PCA_MODE2) == 0x04                   # totem-pole
        mode1 = dev.get(PCA_MODE1)
        # AI (автоинкремент — обязателен для блочной записи каналов)
        # + RESTART, без SLEEP (чип «разбужен»)
        assert mode1 & 0x20 and mode1 & 0x80 and not mode1 & 0x10
        assert servos._pwm_initialized

    def test_all_channels_created(self, servos):
        assert len(servos._servos) == 7
        assert all(s is not None for s in servos._servos)

    def test_init_angles_applied(self, servos):
        # v1.6.0: каналы клешни 4/5 (плечо=наклон, захват)
        from config import SERVO_CRANE_GRIP, SERVO_CRANE_ARM
        assert servos.get_angle(SERVO_CRANE_GRIP) == 180  # CRANE_GRIP_HIGH
        assert servos.get_angle(SERVO_CRANE_ARM) == 100   # CRANE_ARM_OPEN
        assert servos.get_angle(0) == 90

    def test_init_without_device_not_fatal(self):
        """Нет PCA9685 на шине -> флаг False (авто-ретрай v1.4.0 подхватит),
        открытый SMBus закрывается (без утечки fd при ретраях)."""
        from picarpro.hardware.servos import ServoController
        s = ServoController()
        try:
            assert s._pwm_initialized is False
            assert s._pca is None
            assert FakeSMBus.instances, 'SMBus не открывался'
            assert all(bus.closed for bus in FakeSMBus.instances)
        finally:
            s.shutdown()


class TestLazyBoot:
    """v1.3.2: ленивая инициализация — серво не двигаются при загрузке.

    Мануал Adeept: пик тока робота ~3.75 А; одновременный вывод всех
    серво в нейтраль на старте давал просадку питания и ребут Pi.
    """

    def test_no_pwm_writes_at_boot(self, servos):
        assert led_write_count() == 0

    def test_first_command_always_writes(self, servos):
        """Первый после загрузки угол пишется, даже если равен init."""
        assert servos.set_angle(1, 90) is True      # init 90 — не дедуплицируется
        assert chan_write_count(1) == 1
        assert servos.set_angle(1, 90) is True      # повтор — уже дедуплицируется
        assert chan_write_count(1) == 1

    def test_get_angle_falls_back_to_init(self, servos):
        assert servos.get_angle(0) == 90            # ещё не двигали
        assert servos.get_angles() == servos._init_angles

    def test_smooth_move_from_unknown(self, servos):
        """smooth_move по не двигавшемуся серво — один прямой ход."""
        servos.smooth_move(1, 120, steps=5, delay=0.001)
        assert servos.get_angle(1) == 120
        assert chan_write_count(1) == 1             # без интерполяции


class TestSetAngle:
    def test_set_within_limits(self, servos):
        assert servos.set_angle(0, 60) is True
        assert servos.get_angle(0) == 60

    def test_clamped_by_limits(self, servos):
        # руль: 30..150
        assert servos.set_angle(0, 10) is True
        assert servos.get_angle(0) == 30
        assert servos.set_angle(0, 200) is True
        assert servos.get_angle(0) == 150

    def test_invalid_channel(self, servos):
        assert servos.set_angle(-1, 90) is False
        assert servos.set_angle(99, 90) is False

    def test_pulse_counts_written(self, servos):
        """Угол -> импульс мкс -> счётчики 0..4095 (семантика
        adafruit_motor сохранена): 60° = 500 + 1900*60/180 = 1133.3 мкс
        -> int(1133.3 * 50 / 1e6 * 4096) = 232."""
        assert servos.set_angle(1, 60) is True
        on, off = chan_counts(1)
        assert (on, off) == (0, 232)

    def test_pulse_bounds(self, servos):
        # 0° -> 500 мкс -> 102; 180° (клешня, лимит max 180) -> 2400 мкс -> 491
        assert servos.set_angle(1, 0) is True
        assert chan_counts(1)[1] == 102
        assert servos.set_angle(5, 180) is True
        assert chan_counts(5)[1] == 491


class TestLimits:
    def test_get_limits(self, servos):
        limits = servos.get_limits()
        assert 0 in limits and 6 in limits
        assert limits[0]['min'] == 30

    def test_limits_from_config_only(self, servos):
        """v1.3.0: лимиты строго из config.py, файла калибровки нет."""
        from config import SERVO_LIMITS
        assert servos.get_limits() == {
            int(k): dict(v) for k, v in SERVO_LIMITS.items()}
        from config import SERVO_CRANE_GRIP, SERVO_CRANE_ARM
        assert servos.get_limits(2) == {'min': 80, 'max': 170}
        # v1.6.1: наклон (канал 4) — полный диапазон, захват (канал 6) — 100..180
        assert servos.get_limits(SERVO_CRANE_GRIP) == {'min': 0, 'max': 180}
        assert servos.get_limits(SERVO_CRANE_ARM) == {'min': 100, 'max': 180}
        assert servos.get_limits(5) == {'min': 0, 'max': 180}   # кисть/резерв

    def test_no_runtime_editing(self, servos):
        """Редактирование лимитов из кода удалено."""
        assert not hasattr(servos, 'set_limits')

    def test_clamped_to_config_limits(self, servos):
        # tilt камеры: 80..170; наклон клешни (канал 4): 0..180;
        # захват (канал 6): 100..180 (v1.6.1)
        from config import SERVO_CRANE_GRIP, SERVO_CRANE_ARM
        assert servos.set_angle(2, 10) is True
        assert servos.get_angle(2) == 80
        assert servos.set_angle(2, 200) is True
        assert servos.get_angle(2) == 170
        assert servos.set_angle(SERVO_CRANE_GRIP, -5) is True
        assert servos.get_angle(SERVO_CRANE_GRIP) == 0
        assert servos.set_angle(SERVO_CRANE_ARM, 200) is True
        assert servos.get_angle(SERVO_CRANE_ARM) == 180
        assert servos.set_angle(SERVO_CRANE_ARM, 10) is True
        assert servos.get_angle(SERVO_CRANE_ARM) == 100

    def test_same_angle_not_rewritten(self, servos):
        """Анти-джиттер: повторная команда того же угла не пишет PWM."""
        assert servos.set_angle(1, 60) is True
        writes = chan_write_count(1)
        assert writes == 1
        assert servos.set_angle(1, 60) is True
        assert chan_write_count(1) == 1                   # не выросла


class TestMove:
    def test_move_angle_offset(self, servos):
        servos.move_angle(1, 20)  # init 90 -> 110
        assert servos.get_angle(1) == 110

    def test_move_init_returns_home(self, servos):
        servos.set_angle(0, 150)
        servos.set_angle(1, 10)
        servos.move_init()
        assert servos.get_angle(0) == 90
        assert servos.get_angle(1) == 90

    def test_smooth_move_steps(self, servos):
        servos.smooth_move(1, 120, steps=5, delay=0.001)
        assert servos.get_angle(1) == 120

    def test_get_angle_invalid(self, servos):
        assert servos.get_angle(99) == 0


class TestShutdown:
    def test_shutdown_full_off_and_close(self, servos):
        """v1.4.1: shutdown = full-off всех каналов (сервы без импульсов)
        + закрытие шины (вместо adafruit-деинициализации)."""
        servos.set_angle(1, 60)
        servos.shutdown()
        assert _dev().get(PCA_ALL_LED_OFF_H) == 0x10
        assert servos._pwm_initialized is False
        # шина закрыта (последний открытый SMBus — от контроллера)
        assert all(bus.closed for bus in FakeSMBus.instances)
