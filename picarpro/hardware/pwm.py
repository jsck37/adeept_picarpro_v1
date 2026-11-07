"""Бэкенды ШИМ для моторов.

RPi.GPIO.PWM — программный: поток в Python переключает пин, и под нагрузкой
CPU (камера + CV + веб) скважность «дрожит» — моторы гудят и дёргаются.
pigpio формирует ШИМ аппаратным DMA — стабильно при любой загрузке CPU.
Для pigpio нужен запущенный демон pigpiod (setup.py ставит его; Pi 5 не
поддерживается). Без демона автоматически используется RPi.GPIO.
"""
import threading

from picarpro.logger import logger

_RANGE = 1000            # разрешение скважности 0.1 %
_pi = None
_pi_lock = threading.Lock()
_pi_failed = False


def _get_pi():
    """Общее соединение с pigpiod или None."""
    global _pi, _pi_failed
    with _pi_lock:
        if _pi is not None:
            return _pi
        if _pi_failed:
            return None
        try:
            import pigpio
            pi = pigpio.pi()
            if pi is not None and pi.connected:
                _pi = pi
                return _pi
            _pi_failed = True
        except Exception:
            _pi_failed = True
        return None


def reset_for_tests():
    global _pi, _pi_failed
    with _pi_lock:
        _pi, _pi_failed = None, False


class PigpioPWM:
    """Тот же интерфейс, что у RPi.GPIO.PWM (start/ChangeDutyCycle/stop)."""

    def __init__(self, pi, pin, freq):
        self._pi, self._pin, self._freq = pi, pin, freq

    def start(self, duty):
        self._pi.set_PWM_range(self._pin, _RANGE)
        self._pi.set_PWM_frequency(self._pin, int(self._freq))
        self.ChangeDutyCycle(duty)

    def ChangeDutyCycle(self, duty):
        duty = max(0.0, min(100.0, float(duty)))
        self._pi.set_PWM_dutycycle(self._pin, int(round(duty * _RANGE / 100.0)))

    def stop(self):
        self._pi.set_PWM_dutycycle(self._pin, 0)


def make_pwm(gpio, pin, freq, backend='auto'):
    """(pwm, имя_бэкенда). pwm ещё не запущен (вызовите .start(0))."""
    if backend in ('auto', 'pigpio'):
        pi = _get_pi()
        if pi is not None:
            return PigpioPWM(pi, pin, freq), 'pigpio'
        if backend == 'pigpio':
            logger.warning('[Motors] pigpiod недоступен — откат на RPi.GPIO PWM')
    return gpio.PWM(pin, freq), 'rpigpio'
