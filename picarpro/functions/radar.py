"""Радар на HC-SR04 + серво панорамы камеры (v1.6.10, переписан).

Что было не так в прежнем радаре (v1.6.2-1.6.9):
  * один пинг на угол, «нет эха» подменялось ПОСЛЕДНИМ значением датчика — а оно
    относилось к ДРУГОМУ углу, поэтому на карте появлялись «призраки»;
  * если эха не было совсем, точка принудительно ставилась на 200 см («свободно»),
    и стена под косым углом выглядела проёмом;
  * шаг 5° с паузой 0,12 с одинаково для хода в 5° и в 60°: серво не успевало
    доехать, замер шёл в движении;
  * фоновый опрос датчика вклинивался между замерами радара (перекрёстное эхо);
  * точки не старели — в UI нельзя было отличить свежий замер от замера минутной давности.

Теперь:
  * каждый угол — медиана из ULTRASONIC_PING_SAMPLES пингов (отсекает «срывы»);
  * пауза после поворота пропорциональна ходу серво;
  * «нет эха» хранится как clear=True (дальше предела ИЛИ поверхность не отражает),
    отдельно от реального замера; навигация может не доверять clear-точкам;
  * сглаживание по углу — только когда новый и старый замеры близки (быстрая
    реакция на реальное изменение);
  * у каждой точки есть возраст.

Угол — смещение панорамы от центра в градусах, ВЛЕВО > 0 (так же, как у
servos.move_angle и у стрелок камеры в веб-панели).
"""
import threading
import time

from picarpro.logger import logger
from config import (
    ULTRASONIC_SCAN_SERVO, ULTRASONIC_MAX_DISTANCE, RADAR_SECTOR_DEG,
    RADAR_STEP_DEG, RADAR_SETTLE_BASE_S, RADAR_SETTLE_PER_DEG_S, RADAR_SMOOTH,
)

MAX_RANGE_CM = ULTRASONIC_MAX_DISTANCE * 100
_JUMP_CM = 25.0        # разница, после которой замер считается новым, а не шумом


def scan_angles(sector=RADAR_SECTOR_DEG, step=RADAR_STEP_DEG, ascending=True):
    """Углы скана от -sector до +sector (или обратно) с шагом step."""
    n = int(round(2 * sector / step))
    angles = [round(-sector + k * step, 2) for k in range(n + 1)]
    return angles if ascending else angles[::-1]


class Radar:
    def __init__(self, servos, ultrasonic, servo_id=ULTRASONIC_SCAN_SERVO,
                 sector=RADAR_SECTOR_DEG, step=RADAR_STEP_DEG):
        self.servos = servos
        self.ultrasonic = ultrasonic
        self.servo_id = servo_id
        self.sector = sector
        self.step = step
        self._angle = 0.0
        self._points = {}           # угол -> {'d': см | None, 'clear': bool, 'ts': monotonic}
        self._lock = threading.Lock()

    # ---- состояние ----------------------------------------------------------
    def reset(self):
        with self._lock:
            self._points = {}

    @property
    def angle(self):
        return self._angle

    def point_distance(self, angle):
        """Эффективная дистанция точки, см: замер; MAX_RANGE при «нет эха»;
        None — угла нет в карте."""
        with self._lock:
            p = self._points.get(angle)
        if p is None:
            return None
        return MAX_RANGE_CM if p['d'] is None else p['d']

    def sector_min(self, a0, a1, max_age=None, include_clear=True):
        """Минимальная дистанция в секторе углов [a0, a1], см. None — нет данных.

        include_clear=False — точки «нет эха» не считаются (для вывода
        «свободно» им доверять нельзя, а для поиска ближайшей стены они
        и так бесполезны).
        """
        lo, hi = (a0, a1) if a0 <= a1 else (a1, a0)
        now = time.monotonic()
        best = None
        with self._lock:
            for ang, p in self._points.items():
                if not (lo - 1e-6 <= ang <= hi + 1e-6):
                    continue
                if max_age is not None and now - p['ts'] > max_age:
                    continue
                if p['d'] is None:
                    if not include_clear:
                        continue
                    d = MAX_RANGE_CM
                else:
                    d = p['d']
                best = d if best is None else min(best, d)
        return best

    def clear_fraction(self, threshold_cm, max_age=None):
        """Доля точек карты с дистанцией >= threshold_cm (0..1)."""
        now = time.monotonic()
        total = far = 0
        with self._lock:
            for p in self._points.values():
                if max_age is not None and now - p['ts'] > max_age:
                    continue
                total += 1
                d = MAX_RANGE_CM if p['d'] is None else p['d']
                if d >= threshold_cm:
                    far += 1
        return (far / total) if total else 0.0

    def snapshot(self):
        """Список точек для UI, отсортированный по углу."""
        now = time.monotonic()
        with self._lock:
            items = sorted(self._points.items())
        return [{'angle': a,
                 'distance': MAX_RANGE_CM if p['d'] is None else p['d'],
                 'clear': p['d'] is None,
                 'age': round(now - p['ts'], 1)} for a, p in items]

    # ---- измерение ----------------------------------------------------------
    def settle_time(self, angle):
        return RADAR_SETTLE_BASE_S + RADAR_SETTLE_PER_DEG_S * abs(angle - self._angle)

    def point_at(self, angle, wait):
        """Повернуть датчик на angle и измерить. wait(sec) -> True, если режим
        остановлен. Возвращает запись точки или None (остановлено/нет датчика)."""
        if not self.ultrasonic or not getattr(self.ultrasonic, '_initialized', False):
            return None
        try:
            self.servos.move_angle(self.servo_id, angle)
        except Exception as e:
            logger.error(f'[Radar] servo move failed: {e}')
            return None
        if wait(self.settle_time(angle)):
            return None
        self._angle = angle
        d, n = self.ultrasonic.measure_median()
        rec = {'d': None, 'ts': time.monotonic()}
        if d is not None:
            rec['d'] = d
        with self._lock:
            prev = self._points.get(angle)
            if (d is not None and prev is not None and prev['d'] is not None
                    and abs(d - prev['d']) < _JUMP_CM):
                rec['d'] = round(prev['d'] * (1.0 - RADAR_SMOOTH) + d * RADAR_SMOOTH, 1)
            rec['clear'] = rec['d'] is None
            self._points[angle] = rec
        return rec

    def sweep(self, wait, alive, ascending=True, sector=None, step=None,
              on_point=None):
        """Один проход скана. Возвращает True, если проход завершён целиком."""
        angles = scan_angles(sector or self.sector, step or self.step, ascending)
        for a in angles:
            if not alive():
                return False
            if self.point_at(a, wait) is None:
                return False
            if on_point:
                on_point(a)
        return True

    def home(self):
        """Вернуть датчик вперёд."""
        try:
            self.servos.move_angle(self.servo_id, 0)
            self._angle = 0.0
        except Exception:
            pass

    def hold(self):
        """Приостановить фоновый опрос датчика на время скана."""
        h = getattr(self.ultrasonic, 'hold', None)
        if h:
            h()

    def unhold(self):
        u = getattr(self.ultrasonic, 'unhold', None)
        if u:
            u()
