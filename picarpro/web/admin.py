"""Админ-панель (v1.6.15): прямой доступ к конфигу и отключение клиентов.

Две функции, обе за отдельным паролем (config.ADMIN_PASSWORD, по умолчанию
«C2H5OH», меняется в config_local.py):

1. Редактор конфига — читает текущие значения модуля config и пишет
   изменения в config_local.py (этот файл config.py импортирует последним,
   он не затрагивается обновлениями). Применение — перезапуском сервиса
   (кнопка в админ-панели, systemd), потому что почти все константы
   читаются при старте.

2. Временное отключение WiFi-клиентов — правила iptables по MAC-адресу:
     INPUT  DROP  — доступ к панели/видео робота закрыт
     FORWARD DROP  — интернет через робота закрыт
   Блокировка временная: таймаут (минуты) или «до отмены», снимается
   вручную или фоновым сборщиком. Реестр активных блокировок хранится в
   instance/admin_blocks.json и переустанавливается при старте сервиса
   (iptables волатилен). Без root/iptables возвращается ошибка — сервис
   при этом не падает.

3. Реестр устройств (v1.6.16) — instance/admin_devices.json: имена
   (последнее виденное hostname из leases + ручное переименование) и
   активное время использования (session_s — текущая сессия, active_s —
   всего). Ведёт тот же фоновый цикл, что и снятие истёкших блокировок:
   раз в SWEEP_INTERVAL_S собирает hotspot.list_clients() и обновляет
   реестр.

Все подзапросы токенизируются отдельно от основного AUTH_TOKEN:
POST /api/admin/login с паролем -> X-Admin-Token (HMAC + nonce), попытки
подбора ограничены (MAX_LOGIN_FAILS -> LOCKOUT_S на IP).
"""
import ast
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from picarpro.logger import logger

# --- пароль / токены -------------------------------------------------------
MAX_LOGIN_FAILS = 5
LOCKOUT_S = 60.0
ADMIN_TOKEN_TTL_S = 8 * 3600        # после этого — повторный вход
RULE_TAG = 'picarpro-admin'

# --- блокировки ------------------------------------------------------------
_MAC_RE = re.compile(r'^[0-9a-f]{2}(:[0-9a-f]{2}){5}$')
MAX_BLOCK_MIN = 24 * 60             # сутки — разумный «временный» максимум
SWEEP_INTERVAL_S = 10.0
BLOCKS_FILENAME = 'admin_blocks.json'

# ---------------------------------------------------------------------------
# вход: пароль, лимит попыток, HMAC-токены с nonce (отзыв по logout)
# ---------------------------------------------------------------------------
_fails = {}                          # ip -> [count, lock_until_monotonic]
_tokens_lock = threading.Lock()
_issued_nonces = set()


def login_locked(ip):
    """IP заблокирован за подбор пароля? Возвращает оставшиеся секунды."""
    rec = _fails.get(ip)
    if not rec:
        return 0.0
    left = rec[1] - time.monotonic()
    return left if left > 0 else 0.0


def register_login_fail(ip):
    rec = _fails.setdefault(ip, [0, 0.0])
    rec[0] += 1
    if rec[0] >= MAX_LOGIN_FAILS:
        rec[1] = time.monotonic() + LOCKOUT_S
        rec[0] = 0
        logger.warning(f'[Admin] подбор пароля с {ip}: блокировка входа '
                       f'на {LOCKOUT_S:.0f} c')


def register_login_ok(ip):
    _fails.pop(ip, None)


def check_password(provided, expected):
    """Постоянное сравнение строк (без утечки по времени)."""
    if not isinstance(provided, str):
        return False
    return hmac.compare_digest(provided.encode('utf-8'),
                               expected.encode('utf-8'))


def issue_token(secret, ttl_s=ADMIN_TOKEN_TTL_S):
    """Токен '<exp>.<nonce>.<hmac>' — вход по нему через X-Admin-Token."""
    exp = int(time.time()) + int(ttl_s)
    nonce = secrets.token_hex(16)
    sig = hmac.new(secret.encode('utf-8'),
                   f'{exp}.{nonce}'.encode('utf-8'), hashlib.sha256).hexdigest()
    with _tokens_lock:
        _issued_nonces.add(nonce)
    return f'{exp}.{nonce}.{sig}'


def verify_token(secret, token):
    """HMAC верен, не просрочен и nonce выдавался нами (есть в памяти)."""
    if not token:
        return False
    parts = token.split('.')
    if len(parts) != 3:
        return False
    exp_s, nonce, sig = parts
    if not exp_s.isdigit():
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    with _tokens_lock:
        if nonce not in _issued_nonces:
            return False
    good = hmac.new(secret.encode('utf-8'),
                    f'{exp_s}.{nonce}'.encode('utf-8'),
                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good)


def revoke_token(token):
    parts = (token or '').split('.')
    if len(parts) == 3:
        with _tokens_lock:
            _issued_nonces.discard(parts[1])


# ---------------------------------------------------------------------------
# редактор конфига: снимок значений + запись config_local.py
# ---------------------------------------------------------------------------
_LOCAL_HEADER = (
    '# config_local.py — локальные переопределения config.py.\n'
    '# Файл ведёт админ-панель робота (v1.6.15) и НЕ затирается обновлениями.\n'
    '# Изменения применяются после перезапуска сервиса.\n'
)


def config_entries():
    """Снимок редактируемых значений config: [{key, type, value}]."""
    import config as cfg          # корневой модуль проекта
    out = []
    for name in sorted(dir(cfg)):
        if not name.isupper() or name.startswith('_'):
            continue
        val = getattr(cfg, name)
        if isinstance(val, (int, float, str, bool, list, tuple, dict)):
            out.append({
                'key': name,
                'type': ('bool' if isinstance(val, bool) else
                         'int' if isinstance(val, int) else
                         'float' if isinstance(val, float) else
                         'str' if isinstance(val, str) else
                         'dict' if isinstance(val, dict) else 'list'),
                'value': val,
            })
    return out


def _coerce_value(key, current, new):
    """Привести new к типу current; ValueError/TypeError при несовпадении."""
    if isinstance(current, bool):
        if isinstance(new, bool):
            return new
        raise ValueError(f'{key}: ожидается true/false')
    if isinstance(current, int):
        if isinstance(new, bool) or not isinstance(new, (int, float)):
            raise ValueError(f'{key}: ожидается целое число')
        if isinstance(new, float) and not new.is_integer():
            raise ValueError(f'{key}: ожидается целое число')
        return int(new)
    if isinstance(current, float):
        if isinstance(new, bool) or not isinstance(new, (int, float)):
            raise ValueError(f'{key}: ожидается число')
        return float(new)
    if isinstance(current, str):
        if not isinstance(new, str):
            raise ValueError(f'{key}: ожидается строка')
        if len(new) > 300:
            raise ValueError(f'{key}: строка длиннее 300 символов')
        return new
    if isinstance(current, list) or isinstance(current, tuple):
        if not isinstance(new, list):
            raise ValueError(f'{key}: ожидается массив')
        for item in new:
            if isinstance(item, (dict, list)):
                raise ValueError(f'{key}: только плоские массивы')
        return list(new)
    if isinstance(current, dict):
        # пока единственный dict в конфиге — SERVO_LIMITS {id: {min, max}}
        if not isinstance(new, dict):
            raise ValueError(f'{key}: ожидается объект')
        fixed = {}
        for k, v in new.items():
            try:
                ik = int(k)
            except (TypeError, ValueError):
                raise ValueError(f'{key}: ключи — номера каналов')
            if not isinstance(v, dict) or set(v) != {'min', 'max'}:
                raise ValueError(f'{key}[{k}]: ожидается {{"min": .., "max": ..}}')
            lo, hi = v['min'], v['max']
            if isinstance(lo, bool) or isinstance(hi, bool) \
                    or not isinstance(lo, (int, float)) \
                    or not isinstance(hi, (int, float)):
                raise ValueError(f'{key}[{k}]: min/max — числа')
            lo, hi = int(lo), int(hi)
            if not (0 <= lo <= hi <= 180):
                raise ValueError(f'{key}[{k}]: проверка 0 <= min <= max <= 180')
            fixed[ik] = {'min': lo, 'max': hi}
        return fixed
    raise ValueError(f'{key}: тип не поддерживается редактором')


def _read_local_values(path):
    """Существующие присваивания config_local.py (AST, без исполнения)."""
    vals = {}
    try:
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return vals
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper():
            try:
                vals[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue        # не-литерал — не трогаем, перепишем при правке
    return vals


def set_config(values, local_path):
    """Записать изменения в config_local.py. Возвращает {applied, errors}.

    Пишутся только изменённые относительно текущего config ключи + уже
    присутствующие в config_local.py (не потерять прежние правки).
    Перед записью — бэкап config_local.py.bak.
    """
    import config as cfg          # корневой модуль проекта
    if not isinstance(values, dict) or not values:
        return {'applied': 0, 'errors': ['values: пустой или не объект']}
    local_vals = _read_local_values(local_path)
    errors, applied = [], {}
    for key, new in values.items():
        if not isinstance(key, str) or not key.isupper() or key.startswith('_'):
            errors.append(f'{key}: недопустимое имя')
            continue
        if key not in dir(cfg):
            errors.append(f'{key}: нет такого параметра в config')
            continue
        current = getattr(cfg, key)
        try:
            fixed = _coerce_value(key, current, new)
        except (ValueError, TypeError) as e:
            errors.append(str(e))
            continue
        local_now = local_vals.get(key, current)
        if fixed == local_now:
            continue        # без изменения — не пишем
        applied[key] = fixed
    if not applied:
        return {'applied': 0, 'errors': errors or
                ['изменений нет (значения совпадают)']}
    merged = dict(local_vals)
    merged.update(applied)
    body = _LOCAL_HEADER + '\n'
    for key in sorted(merged):
        body += f'{key} = {merged[key]!r}\n'
    try:
        if os.path.exists(local_path):
            with open(local_path, encoding='utf-8') as f:
                prev = f.read()
            with open(local_path + '.bak', 'w', encoding='utf-8') as f:
                f.write(prev)
        os.makedirs(os.path.dirname(str(local_path)) or '.', exist_ok=True)
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(body)
        try:
            os.chmod(local_path, 0o600)
        except OSError:
            pass
    except OSError as e:
        return {'applied': 0, 'errors': [f'запись {local_path}: {e}']}
    logger.info(f'[Admin] config_local.py обновлён: {", ".join(sorted(applied))}')
    return {'applied': len(applied), 'errors': errors,
            'keys': sorted(applied), 'needs_restart': True}


# ---------------------------------------------------------------------------
# блокировки клиентов (iptables по MAC) + реестр instance/admin_blocks.json
# ---------------------------------------------------------------------------
_blocks = {}                         # mac -> {until, ip, added, note}
_blocks_lock = threading.Lock()
_blocks_path = None
_sweeper_started = False


def _ipt(args, run=None):
    """Запуск iptables (инъекция run для тестов) -> (ok, stderr)."""
    run = run or __import__('subprocess').run
    try:
        r = run(['iptables'] + args, capture_output=True, text=True, timeout=5)
    except Exception as e:          # нет iptables/нет прав — не падаем
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or '').strip() or f'exit {r.returncode}'
    return True, ''


def _mac_ok(mac):
    return isinstance(mac, str) and bool(_MAC_RE.match(mac.lower()))


def _rule_spec(mac):
    mac = mac.lower()
    return ['-m', 'mac', '--mac-source', mac,
            '-m', 'comment', '--comment', RULE_TAG,
            '-j', 'DROP']


def _apply_block(mac, run=None):
    ok_all, errs = True, []
    for chain in ('INPUT', 'FORWARD'):
        ok, err = _ipt(['-I', chain] + _rule_spec(mac), run=run)
        if not ok and 'File exists' not in err:
            ok_all = False
            errs.append(f'{chain}: {err}')
    return ok_all, errs


def _remove_block(mac, run=None):
    mac = mac.lower()
    for chain in ('INPUT', 'FORWARD'):
        for _ in range(4):          # правил может «залипнуть» несколько
            ok, _e = _ipt(['-D', chain] + _rule_spec(mac), run=run)
            if not ok:
                break


def _load_blocks(path):
    global _blocks, _blocks_path
    _blocks_path = path
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            _blocks = {str(k).lower(): v for k, v in data.items()
                       if isinstance(v, dict)}
    except (OSError, ValueError):
        _blocks = {}


def _save_blocks():
    if _blocks_path is None:
        return
    try:
        os.makedirs(os.path.dirname(str(_blocks_path)) or '.', exist_ok=True)
        with open(_blocks_path, 'w', encoding='utf-8') as f:
            json.dump(_blocks, f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.error(f'[Admin] не сохранён реестр блокировок: {e}')


def blocks_status(now=None):
    now = now if now is not None else time.time()
    with _blocks_lock:
        out = {}
        for mac, info in _blocks.items():
            until = info.get('until')
            out[mac] = {'ip': info.get('ip') or '',
                        'note': info.get('note') or '',
                        'until': until,
                        'remaining_s': (max(0, int(until - now))
                                        if until else None)}
        return out


def block(mac, minutes, blocks_dir, run=None, note='', sweep=True):
    """Временно отключить клиента по MAC. minutes=None/0 — «до отмены».

    Возвращает {'ok': True} или {'ok': False, 'error': ...} — сервис не
    падает, если iptables недоступен (не root/нет модуля).
    """
    global _blocks_path
    if not _mac_ok(mac):
        return {'ok': False, 'error': 'MAC задан неверно (aa:bb:cc:dd:ee:ff)'}
    mac = mac.lower()
    if minutes in (None, 0, '0', ''):
        until = None
    else:
        try:
            m = float(minutes)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'minutes — число минут'}
        if not (0 < m <= MAX_BLOCK_MIN):
            return {'ok': False,
                    'error': f'minutes: 0 < x <= {MAX_BLOCK_MIN}'}
        until = time.time() + m * 60.0
    ok, errs = _apply_block(mac, run=run)
    with _blocks_lock:
        _blocks[mac] = {'until': until, 'ip': '', 'added': time.time(),
                        'note': note}
        _blocks_path = os.path.join(str(blocks_dir), BLOCKS_FILENAME)
        _save_blocks()
    if sweep:
        _ensure_sweeper()
    if not ok:
        return {'ok': False,
                'error': 'iptables: ' + '; '.join(errs or ['неизвестная ошибка'])}
    logger.info(f'[Admin] клиент {mac} отключён'
                + (f' на {minutes} мин' if until else ' (до отмены)'))
    return {'ok': True, 'mac': mac}


def unblock(mac, run=None):
    mac = (mac or '').lower()
    if not _mac_ok(mac):
        return {'ok': False, 'error': 'MAC задан неверно'}
    _remove_block(mac, run=run)
    with _blocks_lock:
        _blocks.pop(mac, None)
        _save_blocks()
    logger.info(f'[Admin] клиент {mac} разблокирован')
    return {'ok': True, 'mac': mac}


def reapply_blocks(blocks_dir, run=None, now=None):
    """При старте сервиса: перечитать реестры и вернуть iptables-правила.

    Истёкшие блокировки снимаются из реестра. Ошибки iptables не фатальны
    (панель поднимется в любом случае). v1.6.16: здесь же читается реестр
    устройств (имена + активное время) и стартует фоновый цикл — он ведёт
    и снятие истёкших блокировок, и трекер присутствия устройств.
    """
    _load_blocks(os.path.join(str(blocks_dir), BLOCKS_FILENAME))
    _load_devices(os.path.join(str(blocks_dir), DEVICES_FILENAME))
    now = now if now is not None else time.time()
    expired = []
    with _blocks_lock:
        for mac, info in _blocks.items():
            if info.get('until') and info['until'] < now:
                expired.append(mac)
        for mac in expired:
            _blocks.pop(mac, None)
        if expired:
            _save_blocks()
    applied = 0
    for mac in list(_blocks):
        ok, _e = _apply_block(mac, run=run)
        if ok:
            applied += 1
    if _blocks:
        logger.info(f'[Admin] блокировок восстановлено: {applied}/'
                    f'{len(_blocks)}')
    _ensure_sweeper()
    return applied


# ---------------------------------------------------------------------------
# v1.6.16: реестр устройств (instance/admin_devices.json) — имена и активное
# время использования.
#
# Имена. hostname из leases dnsmasq многие устройства не присылают (часть
# телефонов шлёт '*'), а lease-файл точки доступа NetworkManager раньше не
# находился вовсе — в админ-панели вместо имён стояло «—». Реестр запоминает
# последнее виденное имя по MAC; владелец может задать имя вручную
# (device_set_name) — ручное имя приоритетно и переживает смену lease.
#
# Активное время. Общий фоновый цикл (sweeper, раз в SWEEP_INTERVAL_S)
# собирает hotspot.list_clients() и копит устройству активные секунды:
#   session_s — непрерывное присутствие (разрыв больше TRACK_GAP_S = новая
#   сессия), active_s — сумма всех сессий («активное время использования»).
# Реестр переживает перезапуск сервиса.
# ---------------------------------------------------------------------------
DEVICES_FILENAME = 'admin_devices.json'
DEVICE_NAME_MAX = 64
TRACK_INTERVAL_S = SWEEP_INTERVAL_S    # шаг накопления времени (10 с)
TRACK_GAP_S = 120.0    # разрыв больше этого — устройство возвращалось
DEVICE_PRUNE_S = 30 * 24 * 3600.0      # 30 дней не в сети — вычистить
MAX_DEVICES = 64
DEVICES_SAVE_EVERY_S = 300.0           # периодическая запись (беречь SD)

_devices = {}      # mac -> {name, manual, first_seen, last_seen, active_s, session_s}
_devices_lock = threading.Lock()
_devices_path = None
_devices_last_save = 0.0


def _new_device_rec(now):
    return {'name': '', 'manual': False, 'first_seen': now, 'last_seen': now,
            'active_s': 0.0, 'session_s': 0.0}


def _load_devices(path):
    global _devices, _devices_path, _devices_last_save
    _devices_path = path
    _devices_last_save = time.monotonic()
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            _devices = {str(k).lower(): v for k, v in data.items()
                        if isinstance(v, dict) and _mac_ok(k)}
    except (OSError, ValueError):
        _devices = {}


def _save_devices(force=False):
    """Записать реестр (атомарно). force — при структурных изменениях."""
    global _devices_last_save
    if _devices_path is None:
        return
    mono = time.monotonic()
    if not force and mono - _devices_last_save < DEVICES_SAVE_EVERY_S:
        return                      # активное время дожмётся по таймеру
    _devices_last_save = mono
    try:
        os.makedirs(os.path.dirname(str(_devices_path)) or '.', exist_ok=True)
        tmp = str(_devices_path) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_devices, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _devices_path)
    except OSError as e:
        logger.error(f'[Admin] не сохранён реестр устройств: {e}')


def _prune_devices_locked(now, hard=False):
    """Убрать устаревшие записи. hard — урезать до MAX_DEVICES. -> были ли."""
    removed = False
    stale = [m for m, r in _devices.items()
             if now - r.get('last_seen', 0) > DEVICE_PRUNE_S]
    for m in stale:
        _devices.pop(m, None)
        removed = True
    if hard:
        while len(_devices) >= MAX_DEVICES:
            oldest = min(_devices, key=lambda m: _devices[m].get('last_seen', 0))
            _devices.pop(oldest, None)
            removed = True
    return removed


def devices_track(clients, now=None, interval_s=TRACK_INTERVAL_S):
    """Отметить присутствие устройств и накопить активное время (v1.6.16).

    clients — список dict из hotspot.list_clients(). Вызывается из
    фонового цикла; для тестов можно звать напрямую с injected now.
    """
    if not clients:
        return
    now = now if now is not None else time.time()
    structural = False
    with _devices_lock:
        for c in clients:
            mac = (c.get('mac') or '').lower()
            if not _mac_ok(mac):
                continue
            rec = _devices.get(mac)
            if rec is None:
                if len(_devices) >= MAX_DEVICES:
                    _prune_devices_locked(now, hard=True)
                if len(_devices) >= MAX_DEVICES:
                    continue        # реестр переполнен — новое не пишем
                rec = _new_device_rec(now)
                rec['active_s'] = float(interval_s)
                rec['session_s'] = float(interval_s)
                _devices[mac] = rec
                structural = True
            else:
                gap = now - rec.get('last_seen', now)
                if 0 < gap <= TRACK_GAP_S:
                    rec['active_s'] += gap
                    rec['session_s'] += gap
                else:
                    # устройство возвращалось (или сервис перезапускался) —
                    # сессия новая, в общий счёт идёт только текущий шаг
                    rec['active_s'] += float(interval_s)
                    rec['session_s'] = float(interval_s)
                    structural = True
                rec['last_seen'] = now
            # имя: запоминаем последнее виденное hostname (ручное не трогаем)
            if not rec.get('manual'):
                hn = str(c.get('hostname') or '').strip()
                if hn and hn != '*':
                    hn = hn[:DEVICE_NAME_MAX]
                    if hn != rec.get('name'):
                        rec['name'] = hn
                        structural = True
        if _prune_devices_locked(now):
            structural = True
        _save_devices(force=structural)


def device_set_name(mac, name):
    """Задать устройству имя вручную (пустое — сбросить на авто)."""
    if not _mac_ok(mac):
        return {'ok': False, 'error': 'MAC задан неверно (aa:bb:cc:dd:ee:ff)'}
    mac = mac.lower()
    name = str(name or '').strip()[:DEVICE_NAME_MAX]
    with _devices_lock:
        rec = _devices.get(mac)
        if rec is None:
            rec = _new_device_rec(time.time())
            _devices[mac] = rec
        if name:
            rec['name'] = name
            rec['manual'] = True
        else:
            # сброс: имя снова подберётся из leases при появлении устройства
            rec['manual'] = False
            rec['name'] = ''
        _save_devices(force=True)
    logger.info(f'[Admin] имя устройства {mac}: {name!r}')
    return {'ok': True, 'mac': mac, 'name': name}


def devices_info():
    """Снимок реестра: {mac: {name, manual, first_seen, last_seen, ...}}."""
    with _devices_lock:
        return {mac: dict(rec) for mac, rec in _devices.items()}


def known_names():
    """Имена из реестра для hotspot.list_clients(known=...): {mac: {'name'}}."""
    with _devices_lock:
        return {mac: {'name': rec.get('name') or ''}
                for mac, rec in _devices.items() if rec.get('name')}


def _sweep_loop():
    from picarpro.utils import hotspot    # локальный импорт (без циклов)
    while True:
        time.sleep(SWEEP_INTERVAL_S)
        now = time.time()
        expired = []
        with _blocks_lock:
            for mac, info in _blocks.items():
                if info.get('until') and info['until'] < now:
                    expired.append(mac)
        for mac in expired:
            logger.info(f'[Admin] блокировка {mac} истекла — снимаю')
            unblock(mac)
        # v1.6.16: трекер устройств — имена + активное время использования
        try:
            clients = hotspot.list_clients()
        except Exception as e:
            clients = []
            logger.error(f'[Admin] трекер устройств: {e}')
        if clients:
            try:
                devices_track(clients, now=now)
            except Exception as e:
                logger.error(f'[Admin] трекер устройств: {e}')


def _ensure_sweeper():
    global _sweeper_started
    if _sweeper_started:
        return
    _sweeper_started = True
    t = threading.Thread(target=_sweep_loop, name='admin-blocks-sweeper',
                         daemon=True)
    t.start()
