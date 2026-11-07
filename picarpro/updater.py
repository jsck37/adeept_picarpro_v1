"""Обновление «по воздуху» из архива (.tar.gz / .zip) и откат.

Робот в режиме точки доступа не имеет интернета, поэтому обновление — это
загрузка архива проекта из веб-панели (телефон/ПК). Порядок безопасный:
  1. архив распаковывается во временный каталог (без абсолютных путей, ``..``,
     ссылок и устройств; лимиты размера и числа файлов);
  2. проверяется, что это проект (boot.py, config.py, picarpro/...), и все
     .py компилируются — битый архив не доходит до замены файлов;
  3. текущая версия сохраняется в `.backups/` (хранится UPDATE_KEEP_BACKUPS);
  4. каталоги кода заменяются целиком, файлы верхнего уровня копируются;
     missions/, config_local.py и .backups/ не затрагиваются;
  5. ставится маркер `.update_pending`: если новая версия падает при старте
     (сервис перезапускается чаще, чем за UPDATE_HEALTHY_AFTER_S) — следующий
     запуск САМ откатывается на резервную копию.
config.py заменяется, прежний сохраняется как config_user_prev.py; свои
значения держите в config_local.py — он обновлением не трогается.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile

from picarpro.logger import logger
from config import (BASE_DIR, UPDATE_MAX_BYTES, UPDATE_KEEP_BACKUPS,
                    UPDATE_HEALTHY_AFTER_S, UPDATE_SERVICE_NAME)

ROOT = str(BASE_DIR)
REPLACE_DIRS = ('picarpro', 'tools', 'tests', 'docs')
TOP_SUFFIXES = ('.py', '.sh', '.md', '.txt', '.ini')
REQUIRED = ('boot.py', 'config.py', 'picarpro/__init__.py', 'picarpro/state.py')
MAX_UNPACKED = 200 * 1024 * 1024
MAX_FILES = 5000
BACKUP_RE = re.compile(r'^backup_[0-9A-Za-z._-]+\.tar\.gz$')


class UpdateError(Exception):
    pass


def _paths(root=None):
    root = root or ROOT
    return {'root': root, 'backups': os.path.join(root, '.backups'),
            'marker': os.path.join(root, '.update_pending'),
            'tmp': os.path.join(root, '.update_tmp')}


def current_version(root=None):
    return _read_version(os.path.join(root or ROOT, 'picarpro', '__init__.py'))


def _read_version(path):
    try:
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]",
                      open(path, encoding='utf-8').read())
        return m.group(1) if m else '?'
    except OSError:
        return '?'


# ---- распаковка ----------------------------------------------------------------
def _safe_name(name):
    n = name.replace('\\', '/')
    if n.startswith('/') or re.match(r'^[A-Za-z]:', n):
        return None
    parts = [p for p in n.split('/') if p not in ('', '.')]
    if '..' in parts or not parts:
        return None
    return '/'.join(parts)


def safe_extract(archive, dest):
    """Распаковать архив в dest. Возвращает корень проекта внутри dest."""
    os.makedirs(dest, exist_ok=True)
    total = count = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = _safe_name(info.filename)
                if name is None:
                    raise UpdateError(f'unsafe path in archive: {info.filename[:60]}')
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError('symlinks are not allowed')
                total += info.file_size
                count += 1
                if total > MAX_UNPACKED or count > MAX_FILES:
                    raise UpdateError('archive too large')
                target = os.path.join(dest, name)
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as src, open(target, 'wb') as out:
                        shutil.copyfileobj(src, out)
    else:
        try:
            tar = tarfile.open(archive, 'r:*')
        except tarfile.TarError:
            raise UpdateError('not a tar.gz/zip archive')
        with tar:
            for m in tar:
                name = _safe_name(m.name)
                if name is None:
                    raise UpdateError(f'unsafe path in archive: {m.name[:60]}')
                if not (m.isfile() or m.isdir()):
                    raise UpdateError('links/devices are not allowed')
                total += m.size
                count += 1
                if total > MAX_UNPACKED or count > MAX_FILES:
                    raise UpdateError('archive too large')
                target = os.path.join(dest, name)
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with tar.extractfile(m) as src, open(target, 'wb') as out:
                        shutil.copyfileobj(src, out)
    entries = [e for e in os.listdir(dest) if not e.startswith('.')]
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
        return os.path.join(dest, entries[0])
    return dest


def validate_tree(root):
    for rel in REQUIRED:
        if not os.path.isfile(os.path.join(root, rel)):
            raise UpdateError(f'not a PiCar Pro project: {rel} missing')
    errors = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'node_modules')]
        for fn in files:
            if fn.endswith('.py'):
                path = os.path.join(base, fn)
                try:
                    # compile() — проверка синтаксиса без записи .pyc на диск
                    with open(path, 'rb') as f:
                        compile(f.read(), path, 'exec', dont_inherit=True)
                except (SyntaxError, ValueError) as e:
                    errors.append(f'{os.path.relpath(path, root)}: {str(e)[:80]}')
    if errors:
        raise UpdateError('syntax errors in archive: ' + '; '.join(errors[:3]))
    return _read_version(os.path.join(root, 'picarpro', '__init__.py'))


# ---- резервные копии -----------------------------------------------------------------
def _project_entries(root):
    items = [d for d in REPLACE_DIRS if os.path.isdir(os.path.join(root, d))]
    items += [f for f in sorted(os.listdir(root))
              if f.endswith(TOP_SUFFIXES) and os.path.isfile(os.path.join(root, f))]
    return items


def create_backup(root=None):
    P = _paths(root)
    os.makedirs(P['backups'], exist_ok=True)
    name = f"backup_{current_version(P['root'])}_{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    path = os.path.join(P['backups'], name)
    with tarfile.open(path, 'w:gz') as tar:
        for item in _project_entries(P['root']):
            tar.add(os.path.join(P['root'], item), arcname=item,
                    filter=lambda ti: None if '__pycache__' in ti.name else ti)
    for old in list_backups(P['root'])[UPDATE_KEEP_BACKUPS:]:
        try:
            os.remove(os.path.join(P['backups'], old['name']))
        except OSError:
            pass
    return path


def list_backups(root=None):
    d = _paths(root)['backups']
    try:
        names = [n for n in os.listdir(d) if BACKUP_RE.match(n)]
    except FileNotFoundError:
        return []
    names.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
    return [{'name': n, 'size': os.path.getsize(os.path.join(d, n)),
             'ts': int(os.path.getmtime(os.path.join(d, n)))} for n in names]


# ---- замена файлов -----------------------------------------------------------------------
def _install_tree(src, root):
    """Заменить каталоги кода и скопировать файлы верхнего уровня."""
    for d in REPLACE_DIRS:
        new = os.path.join(src, d)
        if not os.path.isdir(new):
            continue
        old = os.path.join(root, d)
        bak = old + '.old'
        if os.path.isdir(bak):
            shutil.rmtree(bak)
        if os.path.isdir(old):
            os.rename(old, bak)
        try:
            shutil.copytree(new, old, ignore=shutil.ignore_patterns('__pycache__'))
        except Exception:
            if os.path.isdir(old):
                shutil.rmtree(old)
            if os.path.isdir(bak):
                os.rename(bak, old)
            raise
        shutil.rmtree(bak, ignore_errors=True)
    for f in os.listdir(src):
        p = os.path.join(src, f)
        if f.endswith(TOP_SUFFIXES) and os.path.isfile(p) and f != 'config_local.py':
            shutil.copy2(p, os.path.join(root, f))


def _config_values(path):
    out = {}
    try:
        tree = ast.parse(open(path, encoding='utf-8').read())
    except (OSError, SyntaxError):
        return out
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return out


def apply_update(archive, root=None):
    """Установить обновление из архива. Возвращает словарь результата."""
    P = _paths(root)
    if os.path.getsize(archive) > UPDATE_MAX_BYTES:
        raise UpdateError('archive too large')
    tmp = tempfile.mkdtemp(dir=_ensure(P['tmp']))
    try:
        src = safe_extract(archive, tmp)
        new_version = validate_tree(src)
        old_version = current_version(P['root'])
        old_cfg = _config_values(os.path.join(P['root'], 'config.py'))
        new_cfg = _config_values(os.path.join(src, 'config.py'))
        backup = create_backup(P['root'])
        cfg_path = os.path.join(P['root'], 'config.py')
        if os.path.isfile(cfg_path):
            shutil.copy2(cfg_path, os.path.join(P['root'], 'config_user_prev.py'))
        _install_tree(src, P['root'])
        with open(P['marker'], 'w') as f:
            json.dump({'backup': os.path.basename(backup), 'boots': 0,
                       'from': old_version, 'to': new_version,
                       'ts': time.time(), 'last_start': 0}, f)
        changed = sorted(k for k in old_cfg if k in new_cfg and old_cfg[k] != new_cfg[k])
        logger.info(f'[Update] {old_version} -> {new_version}; backup {os.path.basename(backup)}')
        return {'ok': True, 'from': old_version, 'to': new_version,
                'backup': os.path.basename(backup), 'config_changed': changed}
    finally:
        shutil.rmtree(P['tmp'], ignore_errors=True)


def _ensure(path):
    os.makedirs(path, exist_ok=True)
    return path


def rollback(name=None, root=None):
    P = _paths(root)
    backups = list_backups(P['root'])
    if not backups:
        raise UpdateError('no backups')
    if name is None:
        name = backups[0]['name']
    if not BACKUP_RE.match(name or '') or name not in [b['name'] for b in backups]:
        raise UpdateError('backup not found')
    tmp = tempfile.mkdtemp(dir=_ensure(P['tmp']))
    try:
        src = safe_extract(os.path.join(P['backups'], name), tmp)
        for rel in REQUIRED:
            if not os.path.isfile(os.path.join(src, rel)):
                raise UpdateError('backup is incomplete')
        before = current_version(P['root'])
        _install_tree(src, P['root'])
        try:
            os.remove(P['marker'])
        except FileNotFoundError:
            pass
        logger.warning(f'[Update] rolled back {before} -> {current_version(P["root"])} ({name})')
        return {'ok': True, 'from': before, 'to': current_version(P['root']), 'backup': name}
    finally:
        shutil.rmtree(P['tmp'], ignore_errors=True)


# ---- защита от «кирпича» ---------------------------------------------------------------------
def startup_check(root=None, now=None):
    """Вызывается из boot.py самым первым. Возвращает 'rolled_back', 'pending'
    или None. Если после обновления сервис перезапускается, не проработав
    UPDATE_HEALTHY_AFTER_S, — откат на резервную копию."""
    P = _paths(root)
    try:
        with open(P['marker']) as f:
            m = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    now = time.time() if now is None else now
    crashed = m.get('boots', 0) >= 1 and now - m.get('last_start', 0) < UPDATE_HEALTHY_AFTER_S
    if crashed:
        try:
            rollback(m.get('backup'), P['root'])
            logger.error('[Update] new version failed to stay up — rolled back')
            return 'rolled_back'
        except Exception as e:
            logger.error(f'[Update] auto-rollback failed: {e}')
            return None
    m['boots'] = m.get('boots', 0) + 1
    m['last_start'] = now
    with open(P['marker'], 'w') as f:
        json.dump(m, f)
    return 'pending'


def mark_healthy(root=None):
    try:
        os.remove(_paths(root)['marker'])
        logger.info('[Update] new version is healthy')
        return True
    except FileNotFoundError:
        return False


def schedule_healthy_mark(root=None):
    t = threading.Timer(UPDATE_HEALTHY_AFTER_S, mark_healthy, args=(root,))
    t.daemon = True
    t.start()
    return t


def schedule_restart(delay=1.5):
    """Перезапуск сервиса через systemd (если он есть). True — запланирован."""
    import shutil as _sh
    if _sh.which('systemctl') is None:
        return False

    def _go():
        try:
            subprocess.Popen(['systemctl', 'restart', UPDATE_SERVICE_NAME],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.error(f'[Update] restart failed: {e}')
    t = threading.Timer(delay, _go)
    t.daemon = True
    t.start()
    return True


def info(root=None):
    P = _paths(root)
    pending = None
    try:
        with open(P['marker']) as f:
            pending = json.load(f)
    except (FileNotFoundError, ValueError):
        pass
    return {'version': current_version(P['root']), 'backups': list_backups(P['root']),
            'pending': pending, 'max_mb': UPDATE_MAX_BYTES // (1024 * 1024)}
