"""Арбитраж управления между несколькими веб-устройствами.

Раньше два устройства (телефон + ПК) управляли роботом одновременно и
мешали друг другу: «стоп» с одного обрывал движение с другого, ручная
команда боролась с автономным режимом, а кто «последний» — зависело от
порядка пакетов. Теперь:

* водитель — устройство, приславшее первую команду управления; пока он
  активен (команда не реже CONTROL_IDLE_RELEASE_S), остальным команды
  управления отклоняются с кодом ``not_driver``;
* чтение (логи, статус, батарея ...), «стоп» и аварийный стоп разрешены
  всем — безопасность важнее арбитража;
* ``take_control`` отдаёт управление запросившему сразу, ``release_control``
  освобождает; при отключении водителя управление освобождается;
* клиент без идентификатора (внутренние вызовы, скрипты) не ограничивается.
"""
import threading
import time

from config import CONTROL_LOCK_ENABLED, CONTROL_IDLE_RELEASE_S

# команды, которые ничего не двигают — доступны всем
READ_ONLY_CMDS = frozenset((
    'get_log', 'clear_log', 'web_active', 'battery', 'ds4_status',
    'yolo_status', 'servo_get_limits', 'i2c_scan',
    'hello', 'view', 'take_control', 'release_control',
))
# всегда разрешены любому устройству (безопасность)
ALWAYS_ALLOWED = READ_ONLY_CMDS | {'estop'}


# команды, у которых часть действий — только чтение
READ_ACTIONS = {
    'mission': frozenset(('list', 'status')),
    'push': frozenset(('status',)),
}


def is_read_action(cmd, params):
    acts = READ_ACTIONS.get(cmd)
    return bool(acts) and str((params or {}).get('action', 'status')) in acts


def is_stop_request(cmd, params):
    return cmd == 'move' and str((params or {}).get('dir', '')) == 'stop'


class ControlArbiter:
    def __init__(self, idle_s=None, enabled=None):
        self._idle_s = CONTROL_IDLE_RELEASE_S if idle_s is None else idle_s
        self._enabled = CONTROL_LOCK_ENABLED if enabled is None else enabled
        self._lock = threading.Lock()
        self._driver = None
        self._ts = 0.0

    @property
    def driver(self):
        """Текущий водитель или None (свободно / истёк)."""
        with self._lock:
            return self._current(time.monotonic())

    def _current(self, now):
        if self._driver and now - self._ts > self._idle_s:
            self._driver = None
        return self._driver

    def check(self, client, cmd, params=None):
        """(разрешено, текущий водитель). Успешная команда управления
        закрепляет водителя и продлевает его «аренду»."""
        if not self._enabled or client is None:
            return True, self._driver
        if (cmd in ALWAYS_ALLOWED or is_stop_request(cmd, params)
                or is_read_action(cmd, params)):
            return True, self._driver
        now = time.monotonic()
        with self._lock:
            cur = self._current(now)
            if cur is None or cur == client:
                self._driver, self._ts = client, now
                return True, client
            return False, cur

    def take(self, client):
        with self._lock:
            self._driver, self._ts = client, time.monotonic()
            return client

    def release(self, client):
        """Освободить управление, если им владел client. True — освобождено."""
        with self._lock:
            if self._driver == client:
                self._driver = None
                return True
            return False
