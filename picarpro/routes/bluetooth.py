"""Bluetooth-управление геймпадом через bluetoothctl (BlueZ).

v1.6.3 — переписан слой работы с bluetoothctl. Что было сломано:

* **Автоподключение стирало сопряжение.** Если геймпад выключен (а при загрузке
  робота он почти всегда выключен), «прямое подключение» не удавалось, и код
  делал ``remove`` + полное спаривание. Без режима сопряжения оно тоже не
  удавалось — в итоге при каждой загрузке геймпад приходилось спаривать заново.
  Теперь ``remove`` выполняется только когда ключи действительно устарели
  (AuthenticationFailed и т.п.), а автоподключение вообще не спаривает.
* **Потерянный и «грязный» вывод.** stdout читался через ``select`` поверх
  текстовой обёртки (данные застревали в её буфере), а ANSI-цвета bluetoothctl
  ломали разбор (``[bluetooth]#``-приглашение, ``\x1b[0;92m[NEW]``). Теперь
  отдельный поток читает сырые байты, чистит ANSI и разбирает события
  ``[NEW]/[CHG]/[DEL] Device`` — список устройств собирается из событий скана.
* **Скан отдавал устаревший кэш BlueZ** (устройства, которых рядом давно нет),
  без RSSI и без признака «сопряжено/подключено». Теперь показываются только
  устройства из текущего скана плюс сопряжённые/подключённые — состояние
  выключенного, но сопряжённого геймпада берётся из ``info``.
* **После ``remove`` пара шла без повторного обнаружения** — ``pair`` получал
  «Device not available». Теперь устройство находится сканом до спаривания.
* **Параллельные запросы портили друг другу диалог** с bluetoothctl — теперь
  операции сериализуются (второй запрос получает «занято»).
* Диагностика: rfkill, служба bluetooth, адаптер, питание, hid_sony — и понятные
  сообщения вместо «Pairing failed».
Безопасность: MAC валидируется строгим regexp (раньше перевод строки в MAC
позволял внедрять команды); конфиг читается/пишется атомарно с лимитом размера.
"""
import collections
import json
import os
import re
import shutil
import subprocess
import threading
import time

from picarpro.logger import logger
from config import BT_CONFIG_FILE

MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')
MAC_IN_TEXT = r'([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})'
BTCTL_STARTUP_WAIT = 2.0          # макс. ожидание первого приглашения
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|[\x01\x02]')
PROMPT_RE = re.compile(r'^(?:\[[^\]]*\][#>]\s*)+')
EVENT_RE = re.compile(r'^\[(NEW|CHG|DEL)\]\s+Device\s+' + MAC_IN_TEXT + r'\s*(.*)$')
GAMEPAD_WORDS = ('wireless controller', 'dualshock', 'ds4', 'ds5', 'dualsense',
                 'xbox', 'gamepad', '8bitdo', 'pro controller', 'joy-con',
                 'sony interactive', 'playstation', 'nintendo')


class BluetoothBusy(Exception):
    pass


def validate_mac(mac):
    """MAC в верхнем регистре или None, если формат неверный."""
    if not isinstance(mac, str) or not MAC_RE.match(mac.strip()):
        return None
    return mac.strip().upper()


def is_gamepad(name, icon=''):
    n = (name or '').lower()
    return icon == 'input-gaming' or any(k in n for k in GAMEPAD_WORDS)


def clean_line(raw):
    """Строка вывода bluetoothctl без ANSI, приглашения и мусора."""
    s = ANSI_RE.sub('', raw).replace('\r', '').strip()
    return PROMPT_RE.sub('', s).strip()


# ---------------------------------------------------------------------------
class BluetoothctlSession:
    """Долгоживущая сессия bluetoothctl: поток-читатель + журнал строк."""

    def __init__(self, binary='bluetoothctl'):
        self._binary = binary
        self._lock = threading.RLock()
        self._cond = threading.Condition()
        self._lines = collections.deque(maxlen=4000)   # (seq, text)
        self._seq = 0
        self._proc = None
        self._reader = None
        self.devices = {}          # MAC -> {name,rssi,paired,trusted,connected,icon,seen}
        self.scan_epoch = 0        # растёт при каждом новом скане: «видно в этом скане»
        self._ensure_running()

    # ---- процесс ----------------------------------------------------------
    def alive(self):
        return bool(self._proc and self._proc.poll() is None)

    def _ensure_running(self):
        with self._lock:
            if self.alive():
                return True
            try:
                self._proc = subprocess.Popen(
                    [self._binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, bufsize=0)
            except Exception as e:
                logger.error(f'[BT] cannot start {self._binary}: {e}')
                self._proc = None
                return False
            proc = self._proc
            self._reader = threading.Thread(target=self._read_loop, args=(proc,),
                                            daemon=True)
            self._reader.start()
            time.sleep(0.2)
            for cmd in ('power on', 'agent on', 'default-agent', 'pairable on'):
                self._write(cmd)
                time.sleep(0.1)
            logger.info('[BT] bluetoothctl session started')
            return True

    def _write(self, cmd):
        try:
            self._proc.stdin.write((cmd + '\n').encode())
            self._proc.stdin.flush()
            return True
        except Exception:
            return False

    def _read_loop(self, proc):
        buf = b''
        fd = proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            # строки разделяются \n и \r; хвост без разделителя копится
            parts = re.split(rb'[\r\n]+', buf)
            buf = parts.pop()
            for p in parts:
                line = clean_line(p.decode('utf-8', 'replace'))
                if line:
                    self._ingest(line)
            # защита от переполнения хвостом без разделителя
            if buf and len(buf) > 8192:
                buf = b''

    def _ingest(self, line):
        m = EVENT_RE.match(line)
        with self._cond:
            if m:
                self._on_event(m.group(1), m.group(2).upper(), m.group(3))
            self._seq += 1
            self._lines.append((self._seq, line))
            self._cond.notify_all()

    def _on_event(self, kind, mac, rest):
        if kind == 'DEL':
            self.devices.pop(mac, None)
            return
        d = self.devices.setdefault(mac, {'name': '', 'rssi': None, 'paired': False,
                                          'trusted': False, 'connected': False,
                                          'icon': '', 'seen': 0})
        if kind == 'NEW':
            d['seen'] = self.scan_epoch
            if rest and rest.upper() != mac.replace(':', '-'):
                d['name'] = rest.strip()
            return
        d['seen'] = self.scan_epoch               # любое изменение = устройство живо
        key, _, val = rest.partition(':')
        key, val = key.strip(), val.strip()
        if key in ('Name', 'Alias') and val:
            d['name'] = val
        elif key == 'RSSI':
            mm = re.search(r'-?\d+', val)
            if mm:
                d['rssi'] = int(mm.group())
        elif key in ('Paired', 'Trusted', 'Connected'):
            d[key.lower()] = (val.lower() == 'yes')
        elif key == 'Icon':
            d['icon'] = val

    # ---- команды ----------------------------------------------------------
    def mark(self):
        with self._cond:
            return self._seq

    def lines_since(self, mark):
        with self._cond:
            return [t for sq, t in self._lines if sq > mark]

    def send(self, cmd):
        self._ensure_running()
        return self._write(cmd)

    def run(self, cmd, until=None, timeout=10.0, settle=0.25):
        """Отправить команду и вернуть строки ответа. until(lines) -> bool —
        признак завершения; без него ждём timeout, но не дольше settle
        тишины после последней новой строки."""
        if not self._ensure_running():
            return []
        mark = self.mark()
        if not self._write(cmd):
            self._restart()
            mark = self.mark()
            if not self._write(cmd):
                return []
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                lines = [t for sq, t in self._lines if sq > mark]
                if until and until(lines):
                    break
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                if not until and lines:
                    # без критерия — ждём, пока вывод не затихнет
                    before = self._seq
                    self._cond.wait(timeout=min(settle, left))
                    if self._seq == before:
                        break
                    continue
                self._cond.wait(timeout=min(0.3, left))
        if settle and until:
            time.sleep(settle)            # хвост: [CHG] Connected: yes и т.п.
        return self.lines_since(mark)

    def wait_for(self, predicate, timeout):
        """Ждать, пока predicate() станет истинным (по событиям). Возвращает его значение."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                val = predicate()
                if val:
                    return val
                left = deadline - time.monotonic()
                if left <= 0:
                    return val
                self._cond.wait(timeout=min(0.3, left))

    def _restart(self):
        with self._lock:
            if self._proc:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
                self._proc = None
        return self._ensure_running()

    def shutdown(self):
        with self._lock:
            if self._proc:
                try:
                    self._write('quit')
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None


# ---------------------------------------------------------------------------
_session = None
_session_lock = threading.Lock()
_op_lock = threading.Lock()          # одна BT-операция за раз


def _get_session():
    global _session
    with _session_lock:
        if _session is None or not _session.alive():
            if _session is not None:
                _session.shutdown()
            _session = BluetoothctlSession()
        return _session


def shutdown_session():
    global _session
    with _session_lock:
        if _session:
            _session.shutdown()
            _session = None


class _Op:
    """Контекст «одна операция за раз»: второй запрос получает BluetoothBusy."""

    def __enter__(self):
        if not _op_lock.acquire(timeout=0.2):
            raise BluetoothBusy('Bluetooth is busy with another operation')
        return self

    def __exit__(self, *a):
        _op_lock.release()


def _run(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return 127, str(e)


def _load_hid_sony():
    try:
        with open('/proc/modules') as f:
            for line in f:
                if line.startswith(('hid_sony ', 'hid_playstation ')):
                    return True
    except Exception:
        pass
    rc, _ = _run(['modprobe', 'hid-sony'], timeout=5)
    return rc == 0


def diagnose():
    """Снимок состояния Bluetooth-стека (для UI и логов)."""
    out = {'bluetoothctl': shutil.which('bluetoothctl') is not None,
           'service': None, 'rfkill_blocked': None, 'hid_sony': False,
           'adapter': None, 'powered': None}
    if shutil.which('systemctl'):
        out['service'] = _run(['systemctl', 'is-active', 'bluetooth'])[1].strip() or None
    if shutil.which('rfkill'):
        txt = _run(['rfkill', 'list', 'bluetooth'])[1].lower()
        out['rfkill_blocked'] = ('soft blocked: yes' in txt or 'hard blocked: yes' in txt)
    try:
        with open('/proc/modules') as f:
            out['hid_sony'] = any(l.startswith(('hid_sony ', 'hid_playstation '))
                                  for l in f)
    except Exception:
        pass
    return out


def ensure_adapter(s):
    """Подготовить адаптер: снять rfkill, запустить службу, включить питание.
    Возвращает (ok, сообщение)."""
    if shutil.which('rfkill'):
        _run(['rfkill', 'unblock', 'bluetooth'])
    if shutil.which('systemctl'):
        if _run(['systemctl', 'is-active', 'bluetooth'])[1].strip() != 'active':
            logger.warning('[BT] bluetooth service inactive — starting')
            _run(['systemctl', 'start', 'bluetooth'], timeout=10)
            time.sleep(1.0)
    lines = s.run('show', until=lambda L: any(
        'Powered:' in l or 'No default controller' in l for l in L), timeout=4,
        settle=0.1)
    text = '\n'.join(lines)
    if 'No default controller' in text or not lines:
        return False, ('No Bluetooth adapter found. Check that Bluetooth is '
                       'enabled (dtparam=krnbt=on / USB dongle) and that the '
                       '"bluetooth" service is running.')
    if re.search(r'Powered:\s*no', text):
        s.run('power on', until=lambda L: any('succeeded' in l or 'Failed' in l
                                              for l in L), timeout=4, settle=0.1)
    return True, 'ok'


# ---------------------------------------------------------------------------
def _info(s, mac):
    """Сведения BlueZ об устройстве или None, если оно не известно."""
    lines = s.run(f'info {mac}', until=lambda L: any(
        'not available' in l or l.startswith('Connected:') or 'Icon:' in l
        or 'Paired:' in l for l in L), timeout=4, settle=0.15)
    if not lines or any('not available' in l for l in lines):
        return None
    info = {'name': '', 'paired': False, 'trusted': False, 'connected': False, 'icon': ''}
    for l in lines:
        k, _, v = l.partition(':')
        k, v = k.strip(), v.strip()
        if k in ('Name', 'Alias') and v and not info['name']:
            info['name'] = v
        elif k in ('Paired', 'Trusted', 'Connected'):
            info[k.lower()] = (v.lower() == 'yes')
        elif k == 'Icon':
            info['icon'] = v
    return info


def _discover(s, mac, timeout=12.0):
    """Сканировать, пока устройство не появится. True/False."""
    s.scan_epoch += 1
    epoch = s.scan_epoch
    s.send('scan on')
    try:
        return bool(s.wait_for(
            lambda: s.devices.get(mac, {}).get('seen', 0) >= epoch, timeout))
    finally:
        s.send('scan off')
        time.sleep(0.4)


_REASONS = (   # (признак в тексте ошибки, вид, подсказка)
    ('page-timeout', 'unreachable', 'Controller is not reachable — switch it on (press PS) and retry'),
    ('host is down', 'unreachable', 'Controller is not reachable — switch it on (press PS) and retry'),
    ('hostdown', 'unreachable', 'Controller is not reachable — switch it on (press PS) and retry'),
    ('authenticationfailed', 'bond', 'Stored pairing is invalid'),
    ('authentication failed', 'bond', 'Stored pairing is invalid'),
    ('authenticationrejected', 'bond', 'Stored pairing is invalid'),
    ('authenticationcanceled', 'pair', 'Pairing was cancelled — put the controller in pairing mode (hold Share + PS)'),
    ('authenticationtimeout', 'pair', 'Pairing timed out — put the controller in pairing mode (hold Share + PS)'),
    ('key missing', 'bond', 'Stored pairing is invalid'),
    ('input/output error', 'bond', 'Stored pairing is invalid'),
    ('alreadyconnected', 'connected', 'Already connected'),
    ('already connected', 'connected', 'Already connected'),
    ('alreadyexists', 'paired', 'Already paired'),
    ('inprogress', 'busy', 'Bluetooth is busy — retry'),
    ('notready', 'adapter', 'Bluetooth adapter is not ready'),
    ('not available', 'missing', 'Controller not found'),
    ('connectionattemptfailed', 'unreachable', 'Controller did not answer — check that it is on and in range'),
)


def _classify(lines, ok_markers):
    """('ok'|'fail', вид, текст) по ответу bluetoothctl."""
    text = '\n'.join(lines)
    low = text.lower()
    if any(m in low for m in ok_markers):
        return 'ok', 'ok', ''
    for needle, kind, hint in _REASONS:
        if needle in low:
            return 'fail', kind, hint
    m = re.search(r'Failed to \w+: (.*)', text)
    return 'fail', 'other', (m.group(1)[:100] if m else 'No response from bluetoothctl')


def _terminal(*words):
    return lambda L: any(w in l.lower() for w in words for l in L)


def _connect(s, mac, attempts=3):
    last = ('fail', 'other', '')
    for i in range(attempts):
        lines = s.run(f'connect {mac}', until=_terminal(
            'connection successful', 'failed to connect', 'not available'), timeout=18)
        last = _classify(lines, ('connection successful',))
        if last[0] == 'ok' or last[1] in ('connected',):
            return 'ok', 'ok', ''
        if last[1] in ('unreachable', 'bond', 'missing'):
            break                      # повтор не поможет
        time.sleep(1.2 * (i + 1))
    return last


def _is_connected(s, mac):
    info = _info(s, mac)
    return bool(info and info['connected'])


def _post_connect(mac, ds4_controller=None):
    if ds4_controller is not None:
        try:
            ds4_controller.trigger_rescan()
        except Exception:
            pass


def pair_and_connect(mac, ds4_controller=None, timeout=30, allow_pairing=True):
    """Подключить геймпад. Возвращает (ok, message).

    allow_pairing=False — только подключение уже сопряжённого устройства
    (автоподключение при загрузке): ничего не удаляем и не спариваем.
    """
    safe_mac = validate_mac(mac)
    if not safe_mac:
        return False, 'Invalid MAC address'
    mac = safe_mac
    try:
        with _Op():
            return _pair_and_connect(mac, ds4_controller, allow_pairing, timeout)
    except BluetoothBusy as e:
        return False, str(e)
    except Exception as e:
        logger.error(f'[BT] connect error: {e}')
        return False, f'Bluetooth error: {str(e)[:80]}'


def _pair_and_connect(mac, ds4, allow_pairing, timeout=30):
    _load_hid_sony()
    s = _get_session()
    ok, msg = ensure_adapter(s)
    if not ok:
        return False, msg
    logger.info(f'[BT] connecting to {mac} (pairing {"allowed" if allow_pairing else "off"})')
    info = _info(s, mac)
    if info and info['connected']:
        _post_connect(mac, ds4)
        return True, f'Already connected to {mac}'

    if info is None:                     # BlueZ о нём не знает
        if not allow_pairing:
            return False, ('Controller is not paired. Use Scan with the controller '
                           'in pairing mode (hold Share + PS).')
        if not _discover(s, mac, 12.0):
            return False, ('Controller not found. Hold Share + PS until the light bar '
                           'flashes, keep it close, and try again.')
        info = _info(s, mac) or {'paired': False}

    if info['paired']:
        s.run(f'trust {mac}', until=_terminal('succeeded', 'failed'), timeout=4, settle=0.1)
        res = _connect(s, mac)
        if res[0] == 'ok' and _is_connected(s, mac):
            _post_connect(mac, ds4)
            return True, f'Connected to {mac}'
        if res[1] == 'unreachable':
            # НЕ удаляем сопряжение: геймпад просто выключен или далеко
            return False, res[2]
        if res[1] != 'bond':
            return False, f'Connection failed: {res[2]}'
        if not allow_pairing:
            return False, ('Stored pairing is invalid. Remove and pair the controller '
                           'again (Scan with the controller in pairing mode).')
        logger.warning(f'[BT] stale bond for {mac} — removing and pairing again')
        s.run(f'remove {mac}', until=_terminal('removed', 'not available', 'failed'),
              timeout=4, settle=0.2)
        if not _discover(s, mac, 12.0):
            return False, ('Old pairing removed, but the controller was not found. '
                           'Hold Share + PS and try again.')

    if not allow_pairing:
        return False, 'Controller is not paired.'

    # --- спаривание нового (или заново найденного) устройства ---
    s.send('scan off')
    time.sleep(0.3)
    lines = s.run(f'pair {mac}', until=_terminal(
        'pairing successful', 'failed to pair', 'not available'), timeout=timeout)
    kind = _classify(lines, ('pairing successful',))
    if kind[0] == 'fail' and kind[1] == 'missing':
        if _discover(s, mac, 8.0):           # пропало из кэша — найти и повторить
            lines = s.run(f'pair {mac}', until=_terminal(
                'pairing successful', 'failed to pair', 'not available'), timeout=timeout)
            kind = _classify(lines, ('pairing successful',))
    if kind[0] == 'fail' and kind[1] not in ('paired', 'connected'):
        hint = kind[2] if kind[1] in ('pair', 'unreachable', 'missing') else \
            'Pairing failed — put the controller in pairing mode (hold Share + PS) and retry'
        return False, hint
    s.run(f'trust {mac}', until=_terminal('succeeded', 'failed'), timeout=4, settle=0.1)
    # DS4 после сопряжения обычно подключается сам — подождём, потом подключим явно
    if not s.wait_for(lambda: s.devices.get(mac, {}).get('connected'), 4.0):
        res = _connect(s, mac)
        if res[0] != 'ok':
            return False, f'Paired, but connection failed: {res[2]}'
    if _is_connected(s, mac):
        _post_connect(mac, ds4)
        return True, f'Paired and connected to {mac}'
    return False, 'Paired, but the controller did not connect — press PS on it'


def auto_reconnect(mac, ds4_controller=None):
    """Автоподключение при загрузке/по кнопке: только connect, без remove/pair."""
    return pair_and_connect(mac, ds4_controller, allow_pairing=False)


def disconnect_device(mac, forget=True):
    """Отключить устройство; forget=True — ещё и удалить сопряжение."""
    safe_mac = validate_mac(mac)
    if not safe_mac:
        return
    try:
        with _Op():
            s = _get_session()
            s.run(f'disconnect {safe_mac}', until=_terminal('successful', 'failed',
                  'not available'), timeout=6, settle=0.2)
            if forget:
                s.run(f'remove {safe_mac}', until=_terminal('removed', 'not available',
                      'failed'), timeout=4, settle=0.2)
    except BluetoothBusy:
        pass


# ---------------------------------------------------------------------------
def _hcitool_scan(scan_time=8):
    devices = []
    if not shutil.which('hcitool'):
        return devices
    rc, out = _run(['hcitool', 'scan', f'--length={max(1, min(scan_time, 16))}'],
                   timeout=scan_time + 5)
    if rc == 0:
        for line in out.splitlines():
            m = re.match(r'\s*' + MAC_IN_TEXT + r'\s+(.*)', line)
            if m:
                devices.append({'name': m.group(2).strip() or 'Unknown',
                                'mac': m.group(1).upper(), 'rssi': None,
                                'paired': False, 'trusted': False, 'connected': False,
                                'is_gamepad': is_gamepad(m.group(2))})
    return devices


def scan_devices(scan_time=6):
    """Список устройств рядом: [{name, mac, rssi, paired, trusted, connected,
    is_gamepad}]. Только реально обнаруженные в этом скане + уже известные
    (сопряжённые/подключённые) — устаревший кэш BlueZ не показывается."""
    with _Op():
        _load_hid_sony()
        s = _get_session()
        ok, msg = ensure_adapter(s)
        if not ok:
            raise RuntimeError(msg)
        s.scan_epoch += 1
        epoch = s.scan_epoch
        s.send('scan on')
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < scan_time:
                time.sleep(0.25)
                # геймпад уже найден — не томим пользователя полным таймаутом
                if time.monotonic() - t0 > min(4.0, scan_time) and any(
                        d['seen'] >= epoch and is_gamepad(d['name'], d['icon'])
                        for d in list(s.devices.values())):
                    break
        finally:
            s.send('scan off')
            time.sleep(0.4)
        # Список известных BlueZ устройств: в нём виден выключенный, но
        # сопряжённый геймпад (в событиях скана его нет). Для устройств,
        # не замеченных этим сканом, состояние берём из info: сопряжённые
        # оставляем, прочий устаревший кэш отбрасываем.
        listing = s.run('devices', until=_terminal('device '), timeout=3, settle=0.3)
        for line in listing:
            m = re.match(r'^Device\s+' + MAC_IN_TEXT + r'\s*(.*)$', line)
            if not m:
                continue
            lmac = m.group(1).upper()
            d = s.devices.get(lmac)
            if d and d['seen'] >= epoch:
                continue                       # найдено сканом — состояние актуально
            info = _info(s, lmac)
            if info:
                d = s.devices.setdefault(lmac, {'name': '', 'rssi': None,
                                                'paired': False, 'trusted': False,
                                                'connected': False, 'icon': '',
                                                'seen': 0})
                d['name'] = info['name'] or d['name']
                d['paired'] = info['paired']
                d['trusted'] = info['trusted']
                d['connected'] = info['connected']
                d['icon'] = info['icon']
            else:
                s.devices.pop(lmac, None)      # BlueZ забыл устройство — убрать из кэша
        out = []
        for mac, d in list(s.devices.items()):
            known = d['paired'] or d['trusted'] or d['connected']
            if d['seen'] < epoch and not known:
                continue                       # устаревший кэш
            name = d['name'] or 'Unknown'
            out.append({'name': name, 'mac': mac, 'rssi': d['rssi'],
                        'paired': d['paired'], 'trusted': d['trusted'],
                        'connected': d['connected'],
                        'is_gamepad': is_gamepad(name, d['icon'])})
        if not out:
            logger.info('[BT] bluetoothctl found nothing — trying hcitool')
            out = _hcitool_scan(scan_time=max(scan_time, 8))
        out.sort(key=lambda d: (not d['is_gamepad'], -(d['rssi'] if d['rssi'] is not None else -999),
                                d['name']))
        return out


# ---------------------------------------------------------------------------
def load_config():
    try:
        if os.path.isfile(BT_CONFIG_FILE) \
                and os.path.getsize(BT_CONFIG_FILE) < 64 * 1024:
            with open(BT_CONFIG_FILE) as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_config(cfg):
    try:
        tmp = str(BT_CONFIG_FILE) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, BT_CONFIG_FILE)
    except Exception:
        pass
