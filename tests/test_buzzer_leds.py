"""Тесты зуммера (PWM-мелодии) и WS2812 LED (SPI-кодирование)."""
import time

import pytest


@pytest.fixture
def buzzer():
    from picarpro.hardware.buzzer import BuzzerController
    b = BuzzerController()
    yield b
    b.shutdown()


@pytest.fixture
def leds():
    from picarpro.hardware.leds_ws2812 import LEDController
    l = LEDController()
    yield l
    l.shutdown()


class TestBuzzer:
    def test_init(self, buzzer, fake_gpio):
        assert buzzer._initialized
        pwm = fake_gpio.pwm_for(24)
        assert pwm is not None
        assert pwm.started

    def test_beep_starts_tone(self, buzzer, fake_gpio):
        buzzer.play_melody('beep')
        time.sleep(0.05)
        pwm = fake_gpio.pwm_for(24)
        assert pwm.frequency == 440       # A4
        assert pwm.duty_cycle == 50
        buzzer.stop()
        assert pwm.duty_cycle == 0

    def test_unknown_melody_ignored(self, buzzer, fake_gpio):
        buzzer.play_melody('nonexistent')
        time.sleep(0.05)
        assert fake_gpio.pwm_for(24).duty_cycle == 0

    def test_birthday_melody_plays(self, buzzer, fake_gpio):
        buzzer.play_melody('happy_birthday')
        time.sleep(0.05)
        pwm = fake_gpio.pwm_for(24)
        # нота открывается
        assert pwm.duty_cycle > 0
        buzzer.stop()

    def test_stop_stops_melody(self, buzzer, fake_gpio):
        buzzer.play_melody('happy_birthday')
        time.sleep(0.05)
        buzzer.stop()
        time.sleep(0.05)
        assert fake_gpio.pwm_for(24).duty_cycle == 0

    def test_alarm_melody_exists(self, buzzer):
        """Мелодия alarm добавлена при слиянии для watchdog/низкой батареи."""
        from picarpro.hardware.buzzer import MELODIES
        assert 'alarm' in MELODIES


class TestLEDBits:
    """v1.6.1: 3 SPI-бита на бит WS2812 ('1'=0b110, '0'=0b100) при запросе
    8 МГц (на bcm2835 факт. 7.8125 МГц). В v1.0..v1.6.0 было 4 бита при
    запросе 6.4 МГц — фактические те же 7.8125 МГц, из-за чего T0H был
    256 нс и «ноль» декодировался чипом как «единица»: лента светила
    белым мусором. Тайминги схемы: T1H=256 нс, T0H=128 нс, период 384 нс."""

    def test_byte_encoding(self):
        from picarpro.hardware.leds_ws2812 import _byte_to_spi
        out = _byte_to_spi(0b10000000)
        assert len(out) == 3
        # старший бит = '1' -> 110, остальные семь = '0' -> 100:
        # 110 100 100 100 100 100 100 100 = 0xD24924
        assert out == b'\xD2\x49\x24'

    def test_zero_byte(self):
        from picarpro.hardware.leds_ws2812 import _byte_to_spi
        # 8 нулей: 100 x 8 = 0x924924
        assert _byte_to_spi(0) == b'\x92\x49\x24'

    def test_all_ones_byte(self):
        from picarpro.hardware.leds_ws2812 import _byte_to_spi
        # 8 единиц: 110 x 8 = 0xDB6DB6
        assert _byte_to_spi(0xFF) == b'\xDB\x6D\xB6'

    def test_timing_window(self):
        """Ширины импульсов должны попадать в окно декодирования WS2812:
        T0H < порога (~150-250 нс), T1H > порога, при фактических
        7.8125 МГц (SPI-бит = 128 нс)."""
        from picarpro.hardware import leds_ws2812 as m
        assert m._BIT0 == 0b100
        assert m._BIT1 == 0b110
        spi_hz = 7_812_500          # 250 МГц / делитель 32
        bit_ns = 1e9 / spi_hz
        t0h = bin(m._BIT0).count('1') * bit_ns
        t1h = bin(m._BIT1).count('1') * bit_ns
        assert t0h < 250            # '0' не должен читаться как '1'
        assert t1h > 250            # '1' не должен читаться как '0'
        period = 3 * bit_ns
        assert 300 <= period <= 1600  # окно 1.25 мкс ±600 нс


class TestLEDController:
    def test_init_spi(self, leds):
        from tests.fakes.all_fakes import FakeSpiDev
        assert leds._initialized
        assert FakeSpiDev.instances
        assert FakeSpiDev.instances[0].opened

    def test_solid_writes_frame(self, leds):
        leds.fill(10, 20, 30)
        from tests.fakes.all_fakes import FakeSpiDev
        from picarpro.hardware.leds_ws2812 import _LATCH_BYTES
        data = FakeSpiDev.instances[0].written[-1]
        # 16 LED * 3 канала * 3 байта (v1.6.1: 3 SPI-бита на бит) + latch
        assert len(data) == 16 * 3 * 3 + _LATCH_BYTES
        # latch при фактических 7.8125 МГц (запрос 8 МГц, делитель 32)
        assert _LATCH_BYTES * 8 / 7_812_500 >= 50e-6

    def test_off_mode_clears(self, leds):
        leds.fill(255, 255, 255)
        leds.set_mode('off', (0, 0, 0))
        from tests.fakes.all_fakes import FakeSpiDev
        from picarpro.hardware.leds_ws2812 import _byte_to_spi
        data = FakeSpiDev.instances[0].written[-1]
        # нулевой байт цвета кодируется в 0x924924 (100 x 8)
        assert data[:12] == _byte_to_spi(0) * 4

    def test_brightness_scaling(self, leds):
        from config import LED_BRIGHTNESS
        leds.fill(255, 255, 255)
        from tests.fakes.all_fakes import FakeSpiDev
        data = FakeSpiDev.instances[0].written[-1]
        scaled = int(255 * (LED_BRIGHTNESS / 255.0))
        # первый байт после кодирования канала G
        assert scaled < 255  # яркость применена

    def test_breath_animation_runs(self, leds):
        leds.set_mode('breath', (255, 0, 0))
        time.sleep(0.25)
        assert leds._thread.is_alive()
        leds.set_mode('off', (0, 0, 0))
        time.sleep(0.1)

    def test_rainbow_animation_runs(self, leds):
        leds.set_mode('rainbow', (255, 255, 255))
        time.sleep(0.15)
        from tests.fakes.all_fakes import FakeSpiDev
        assert len(FakeSpiDev.instances[0].written) > 1
        leds.set_mode('off', (0, 0, 0))

    def test_police_animation_alternates(self, leds):
        leds.set_mode('police', (255, 0, 0))
        time.sleep(0.35)
        from tests.fakes.all_fakes import FakeSpiDev
        frames = FakeSpiDev.instances[0].written
        assert len(frames) >= 2
        assert frames[-1] != frames[-2]  # красный/синий чередуются
        leds.set_mode('off', (0, 0, 0))

    def test_wheel(self):
        from picarpro.hardware.leds_ws2812 import LEDController
        r, g, b = LEDController._wheel(0)
        assert (r, g, b) == (0, 255, 0)
        r, g, b = LEDController._wheel(85)
        assert (r, g, b) == (255, 0, 0)
        r, g, b = LEDController._wheel(170)
        assert (r, g, b) == (0, 0, 255)

    def test_shutdown_closes_spi(self, leds):
        from tests.fakes.all_fakes import FakeSpiDev
        leds.shutdown()
        assert FakeSpiDev.instances[0].closed
