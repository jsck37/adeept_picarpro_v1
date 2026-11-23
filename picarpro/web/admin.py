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
    """При старте сервиса: перечитать реестр и вернуть iptables-правила.

    Истёкшие блокировки снимаются из реестра. Ошибки iptables не фатальны
    (панель поднимется в любом случае).
    """
    _load_blocks(os.path.join(str(blocks_dir), BLOCKS_FILENAME))
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


def _sweep_loop():
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


def _ensure_sweeper():
    global _sweeper_started
    if _sweeper_started:
        return
    _sweeper_started = True
    t = threading.Thread(target=_sweep_loop, name='admin-blocks-sweeper',
                         daemon=True)
    t.start()
