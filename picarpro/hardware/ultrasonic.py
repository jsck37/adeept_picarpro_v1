import threading, time
from picarpro.logger import logger
from config import (ULTRASONIC_TRIGGER, ULTRASONIC_ECHO,
                    ULTRASONIC_MAX_DISTANCE, ULTRASONIC_ECHO_TIMEOUT_S,
                    ULTRASONIC_IDLE_INTERVAL_S, ULTRASONIC_ACTIVE_INTERVAL_S,
                    ULTRASONIC_STALE_S, ULTRASONIC_PING_SAMPLES,
                    ULTRASONIC_PING_GAP_S, ULTRASONIC_USE_EDGE_EVENTS)

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None

# Сколько подряд «нет эха» нужно, чтобы считать путь свободным. Одиночный
# пропуск (косая/мягкая поверхность) игнорируем и держим прошлое значение.
_MISSES_FOR_CLEAR = 3


class UltrasonicSensor:
    """HC-SR04 с опросом по требованию.

    * без потребителей — один замер в ULTRASONIC_IDLE_INTERVAL_S (для UI);
    * потребитель вызывает ``acquire()`` / ``release()`` — пока он есть,
      датчик опрашивается каждые ULTRASONIC_ACTIVE_INTERVAL_S;
    * «нет эха» несколько раз подряд = свободно до ULTRASONIC_MAX_DISTANCE
      (раньше оставалось последнее валидное значение — «призрачное»
      препятствие или, наоборот, слепота к реальному);
    * ``get_distance_cm()`` возвращает None, если данных нет или они старые.
    """

    def __init__(self):
        self._distance = 0.0          # см; 0.0 — ещё не измерено
        self._distance_ts = 0.0       # time.monotonic() последнего обновления
        self._misses = 0
        self._running = False
        self._thread = None
        self._initialized = False
        self._demand = 0
        self._demand_lock = threading.Lock()
        self._measure_lock = threading.Lock()   # один замер за раз
        self._wake = threading.Event()
        self._hold = 0                          # >0: фоновый опрос приостановлен (радар)
        self._hold_lock = threading.Lock()
        if not _HAS_GPIO:
            logger.warning('[Ultrasonic] RPi.GPIO not available')
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(ULTRASONIC_TRIGGER, GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(ULTRASONIC_ECHO, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            self._initialized = True
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            logger.info(f'[Ultrasonic] OK — trig={ULTRASONIC_TRIGGER} echo={ULTRASONIC_ECHO}')
        except Exception as e:
            logger.error(f'[Ultrasonic] init failed: {e}')

    # ---- потребители -----------------------------------------------------
    def acquire(self):
        """Потребителю нужны частые измерения (автономный режим и т.п.)."""
        with self._demand_lock:
            self._demand += 1
        self._wake.set()

    def release(self):
        with self._demand_lock:
            self._demand = max(0, self._demand - 1)

    def _interval(self):
        with self._demand_lock:
            busy = self._demand > 0
        return ULTRASONIC_ACTIVE_INTERVAL_S if busy else ULTRASONIC_IDLE_INTERVAL_S

    # ---- измерение -------------------------------------------------------
    def _measure(self):
        """Одно измерение дистанции (см) или None при отсутствии эха.

        v1.6.10: эхо ловится ОПРОСОМ пина по time.perf_counter() (до ~30 мс
        занятого ожидания). GPIO.wait_for_edge на Raspberry Pi перенастраивает
        детектор фронтов на каждый вызов и «съедает» миллисекунды — это давало
        ошибку в несколько сантиметров и пропуски эха на радаре. Прежнее
        поведение — ULTRASONIC_USE_EDGE_EVENTS = True.
        """
        GPIO.output(ULTRASONIC_TRIGGER, GPIO.HIGH)
        time.sleep(0.00001)
        GPIO.output(ULTRASONIC_TRIGGER, GPIO.LOW)
        wait_edge = getattr(GPIO, 'wait_for_edge', None)
        if ULTRASONIC_USE_EDGE_EVENTS and wait_edge is not None:
            timeout_ms = int(ULTRASONIC_ECHO_TIMEOUT_S * 1000)
            if wait_edge(ULTRASONIC_ECHO, GPIO.RISING,
                         timeout=timeout_ms) is None:
                return None
            t_start = time.perf_counter()
            if wait_edge(ULTRASONIC_ECHO, GPIO.FALLING,
                         timeout=timeout_ms) is None:
                return None
            t_end = time.perf_counter()
        else:
            clock = time.perf_counter
            deadline = clock() + 0.03
            while GPIO.input(ULTRASONIC_ECHO) == 0:
                if clock() > deadline:
                    return None
            t_start = clock()
            deadline = t_start + 0.03
            while GPIO.input(ULTRASONIC_ECHO) == 1:
                if clock() > deadline:
                    return None
            t_end = clock()
        return round((t_end - t_start) * 17150.0, 1)

    def _store(self, d):
        """Принять результат замера (см) или None («нет эха»)."""
        max_cm = ULTRASONIC_MAX_DISTANCE * 100
        if d is not None and 0 < d <= max_cm:
            self._misses = 0
            self._distance = d
        else:
            self._misses += 1
            if self._misses < _MISSES_FOR_CLEAR:
                return                    # одиночный промах — ждём
            self._distance = max_cm       # эха нет — впереди свободно
        self._distance_ts = time.monotonic()

    def _loop(self):
        # первый замер — после паузы: старт сервиса и так загружен
        while self._running:
            self._wake.wait(timeout=self._interval())
            self._wake.clear()
            if not self._running:
                break
            if self._hold:
                continue                  # радар сам управляет пингами
            try:
                with self._measure_lock:
                    self._store(self._measure())
            except Exception:
                pass

    def measure_now(self, attempts=2):
        """Синхронный свежий замер (см) — после поворота/сервы, когда кэш
        устарел. Возвращает дистанцию или None, если эха нет."""
        if not self._initialized:
            return None
        result = None
        for _ in range(max(1, attempts)):
            try:
                with self._measure_lock:
                    d = self._measure()
                    self._store(d)
            except Exception:
                d = None
            if d is not None:
                result = d
                break
            time.sleep(0.03)
        return result

    def hold(self):
        """Приостановить фоновый опрос (радар качает датчик по углам: чужие
        пинги между его замерами дают перекрёстное эхо)."""
        with self._hold_lock:
            self._hold += 1

    def unhold(self):
        with self._hold_lock:
            self._hold = max(0, self._hold - 1)
        self._wake.set()

    def measure_median(self, samples=None, gap=None):
        """Медиана нескольких пингов -> (дистанция см | None, число валидных).

        Для радара (v1.6.10): один пинг HC-SR04 «срывается» на косых и мягких
        поверхностях, медиана из 3 это отсекает. «Нет эха» остаётся None
        (неизвестно / дальше предела), а НЕ подменяется прошлым значением с
        другого угла, как было. Кэш _distance (фронтальный) не трогаем —
        радар измеряет под разными углами.
        """
        if not self._initialized:
            return None, 0
        n = max(1, int(samples or ULTRASONIC_PING_SAMPLES))
        pause = ULTRASONIC_PING_GAP_S if gap is None else gap
        max_cm = ULTRASONIC_MAX_DISTANCE * 100
        vals = []
        for k in range(n):
            try:
                with self._measure_lock:
                    d = self._measure()
            except Exception:
                d = None
            if d is not None and 0 < d <= max_cm:
                vals.append(d)
            if k < n - 1:
                time.sleep(pause)
        if not vals:
            return None, 0
        vals.sort()
        return vals[len(vals) // 2], len(vals)

    # ---- чтение ----------------------------------------------------------
    def get_last_distance(self):
        """Последнее значение, см (0.0 — не измерено). Для UI/телеметрии."""
        return self._distance

    def get_distance_cm(self):
        """Дистанция для управления: см или None, если данных нет/протухли."""
        if self._distance <= 0.0:
            return None
        if self._distance_ts == 0.0:
            return self._distance     # значение выставлено извне (тесты/симуляция)
        limit = max(ULTRASONIC_STALE_S, 3 * self._interval())
        if time.monotonic() - self._distance_ts > limit:
            return None
        return self._distance

    def shutdown(self):
        self._running = False
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info('[Ultrasonic] shutdown')
