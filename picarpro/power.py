"""Менеджер питания: экономия энергии в простое и защита при разряде.

* Простой: если робот стоит и никто не управляет им дольше
  SERVO_IDLE_RELEASE_S, с серво из SERVO_IDLE_RELEASE_IDS (по умолчанию
  рулевого) снимается удержание — серво перестаёт потреблять ток и греться.
  Любая команда движения/автономный режим снова выставляют угол сами.
* Низкий заряд (нужен ADS7830): потолок скорости моторов и приглушённая
  подсветка до восстановления напряжения.

Менеджер — один поток, просыпающийся раз в POWER_MANAGER_INTERVAL_S.
"""
import threading
import time

from picarpro.logger import logger
from config import (
    SERVO_IDLE_RELEASE_S, SERVO_IDLE_RELEASE_IDS,
    LOW_BATTERY_SPEED_CAP, LOW_BATTERY_LED_SCALE,
    MOTOR_MAX_SPEED_DEFAULT, POWER_MANAGER_INTERVAL_S,
)


class PowerManager:
    def __init__(self, state):
        self._state = state
        self._low_mode = False
        self._released = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ---- состояние -------------------------------------------------------
    @property
    def low_power(self):
        return self._low_mode

    def _busy(self):
        """Робот чем-то занят: едет или выполняет режим."""
        st = self._state
        if st.motors is not None and getattr(st.motors, 'is_moving', False):
            return True
        if st.autonomy_active():
            return True
        return False

    def tick(self, now=None):
        """Один проход (вынесен для тестов)."""
        now = time.monotonic() if now is None else now
        self._check_battery()
        self._check_idle(now)

    def _check_battery(self):
        st = self._state
        try:
            status = st.voltage.get_status() if st.voltage else None
        except Exception:
            status = None
        low = bool(status and status.get('low'))
        if low == self._low_mode:
            return
        self._low_mode = low
        if st.motors is not None:
            st.motors.max_speed = (LOW_BATTERY_SPEED_CAP if low
                                   else MOTOR_MAX_SPEED_DEFAULT)
        if st.leds is not None and hasattr(st.leds, 'set_power_scale'):
            st.leds.set_power_scale(LOW_BATTERY_LED_SCALE if low else 1.0)
        if low:
            logger.warning(f'[Power] low battery: speed cap '
                           f'{LOW_BATTERY_SPEED_CAP}%, dimmed LEDs')
        else:
            logger.info('[Power] battery OK: limits removed')

    def _check_idle(self, now):
        st = self._state
        if (SERVO_IDLE_RELEASE_S <= 0 or not SERVO_IDLE_RELEASE_IDS
                or st.servos is None):
            return
        if self._busy():
            self._released = False
            return
        if now - st._last_activity < SERVO_IDLE_RELEASE_S:
            self._released = False
            return
        if self._released:
            return
        released = [sid for sid in SERVO_IDLE_RELEASE_IDS
                    if st.servos.release(sid)]
        self._released = True
        if released:
            logger.info(f'[Power] idle — servo(s) {released} released')

    def _loop(self):
        while not self._stop.wait(POWER_MANAGER_INTERVAL_S):
            try:
                self.tick()
            except Exception as e:
                logger.warning(f'[Power] error: {e}')

    def shutdown(self):
        self._stop.set()
