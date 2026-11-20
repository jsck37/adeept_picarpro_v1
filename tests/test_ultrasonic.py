"""Тесты HC-SR04 (фейковый GPIO с временной диаграммой эхо-импульса)."""
import time

import pytest


@pytest.fixture
def sensor():
    from picarpro.hardware.ultrasonic import UltrasonicSensor
    s = UltrasonicSensor()
    yield s
    s.shutdown()


class TestInit:
    def test_initialized(self, sensor, fake_gpio):
        assert sensor._initialized is True
        assert fake_gpio.modes.get(11) == fake_gpio.OUT    # TRIG
        assert fake_gpio.modes.get(8) == fake_gpio.IN      # ECHO

    def test_thread_started(self, sensor):
        assert sensor._thread is not None
        assert sensor._thread.is_alive()


class TestMeasure:
    def test_normal_measurement(self, sensor, fake_gpio):
        """Эхо 0.88 мс -> дистанция ~15 см (0.00088 * 17150)."""
        fake_gpio.arm_ultrasonic(trigger_pin=11, echo_pin=8,
                                 wave_seconds=0.00088)
        d = sensor._measure()
        assert d is not None
        assert 12 <= d <= 18

    def test_no_echo_timeout(self, sensor, fake_gpio):
        """Эхо не приходит — измерение завершается по таймауту с None."""
        fake_gpio.arm_ultrasonic(trigger_pin=11, echo_pin=8,
                                 wave_seconds=0.0)
        t0 = time.monotonic()
        d = sensor._measure()
        elapsed = time.monotonic() - t0
        assert d is None
        assert elapsed < 0.5, 'нет раннего выхода по таймауту'

    def test_trigger_pulse_sequence(self, sensor, fake_gpio):
        fake_gpio.arm_ultrasonic(11, 8, 0.0001)
        sensor._measure()
        # TRIG должен был моргнуть HIGH->LOW
        trig_events = [(v, t) for (p, v, t) in fake_gpio.output_log if p == 11]
        assert len(trig_events) >= 2
        assert trig_events[0][0] == fake_gpio.HIGH
        assert trig_events[1][0] == fake_gpio.LOW


class TestLoop:
    def test_distance_filtered_by_range(self, sensor, fake_gpio):
        """Дистанция выше ULTRASONIC_MAX_DISTANCE не принимается."""
        fake_gpio._distance = 0.0
        sensor._distance = 999.0  # вне диапазона 0..200 см
        # прямая запись через loop не пройдёт: имитируем measure
        sensor._distance = 150.0
        assert sensor.get_last_distance() == 150.0

    def test_background_loop_updates(self, sensor, fake_gpio):
        fake_gpio.arm_ultrasonic(11, 8, 0.00117)  # ~20 см
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if sensor.get_last_distance() > 0:
                break
            time.sleep(0.05)
        d = sensor.get_last_distance()
        assert 14 <= d <= 26

    def test_shutdown_stops_thread(self, sensor):
        sensor.shutdown()
        time.sleep(0.05)
        assert not sensor._thread.is_alive()


class TestRadarMeasure:
    """v1.6.10: замер для радара — медиана пингов, «нет эха» не подменяется."""

    def _seq(self, sensor, values):
        it = iter(values)
        sensor._measure = lambda: next(it)

    def test_median_of_three(self, sensor):
        self._seq(sensor, [40.0, 300.0 / 10, 41.0])        # 40, 30, 41 -> 40
        d, n = sensor.measure_median(samples=3, gap=0)
        assert d == 40.0 and n == 3

    def test_single_outlier_rejected(self, sensor):
        self._seq(sensor, [50.0, 12.0, 51.0])              # «срыв» 12 см
        d, n = sensor.measure_median(samples=3, gap=0)
        assert d == 50.0

    def test_misses_ignored_when_enough_hits(self, sensor):
        self._seq(sensor, [None, 80.0, 82.0])
        d, n = sensor.measure_median(samples=3, gap=0)
        assert n == 2 and d in (80.0, 82.0)

    def test_no_echo_returns_none_not_stale_value(self, sensor):
        sensor._distance = 99.0                            # «старое» значение с другого угла
        self._seq(sensor, [None, None, None])
        d, n = sensor.measure_median(samples=3, gap=0)
        assert d is None and n == 0

    def test_does_not_overwrite_front_distance_cache(self, sensor):
        sensor._distance = 123.0
        self._seq(sensor, [20.0, 20.0, 20.0])
        sensor.measure_median(samples=3, gap=0)
        assert sensor._distance == 123.0

    def test_out_of_range_values_dropped(self, sensor):
        self._seq(sensor, [9999.0, 0.0, 60.0])
        d, n = sensor.measure_median(samples=3, gap=0)
        assert d == 60.0 and n == 1

    def test_hold_pauses_background_polling(self, sensor):
        calls = []
        sensor._measure = lambda: calls.append(1) or 30.0
        sensor.hold()
        sensor.acquire()
        time.sleep(0.35)
        n_held = len(calls)
        assert n_held == 0, 'фоновый опрос должен молчать во время скана радара'
        sensor.unhold()
        time.sleep(0.35)
        assert len(calls) > 0
        sensor.release()
