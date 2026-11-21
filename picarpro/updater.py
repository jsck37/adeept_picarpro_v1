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
import urllib.error
import urllib.request
import zipfile

from picarpro.logger import logger
from config import (BASE_DIR, UPDATE_MAX_BYTES, UPDATE_KEEP_BACKUPS,
                    UPDATE_HEALTHY_AFTER_S, UPDATE_SERVICE_NAME,
                    UPDATE_GITHUB_REPO, UPDATE_GITHUB_BRANCH,
                    UPDATE_GITHUB_TIMEOUT_S)

ROOT = str(BASE_DIR)
# models — нейросети (yolov5n-seg / yolo11n-pose .onnx + classes.txt): без
# него веб-обновление не доставляло модели на робота, и режимы YOLO Mask /
# Hand после OTA-обновления падали с «model not found» (v1.6.12). Своя
# модель в /home/pi/models приоритетнее и заменой не затрагивается.
REPLACE_DIRS = ('picarpro', 'tools', 'tests', 'docs', 'models')
TOP_SUFFIXES = ('.py', '.sh', '.md', '.txt', '.ini')
REQUIRED = ('boot.py', 'config.py', 'picarpro/__init__.py', 'picarpro/state.py')
MAX_UNPACKED = 200 * 1024 * 1024
MAX_FILES = 5000
BACKUP_RE = re.compile(r'^backup_[0-9A-Za-z._-]+\.tar\.gz$')
_UA = {'User-Agent': 'picarpro-updater'}   # GitHub API требует User-Agent


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


# ---- проверка обновлений на GitHub (v1.6.13) --------------------------------
# Робот в AP-режиме интернета не имеет, но при подключении к домашней сети
# (клиентом) или по кабелю интернет появляется — тогда кнопка «Проверить»
# в меню «Обновление» сравнивает локальную версию с версией репозитория
# jsck37/adeept_picarpro_v1 и может скачать архив прямо с GitHub.

def parse_repo(url):
    """'https://github.com/owner/repo' -> ('owner', 'repo') или None."""
    m = re.match(r'^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/'
                 r'([A-Za-z0-9_.-]+?)(?:\.git)?/?$', url or '')
    return (m.group(1), m.group(2)) if m else None


def version_tuple(ver):
    """'1.6.13-mod2' -> (1, 6, 13, 'mod2') для сравнения версий."""
    parts = []
    for chunk in re.split(r'[.\-+_ ]', str(ver or '')):
        if chunk.isdigit():
            parts.append(int(chunk))
        elif chunk:
            parts.append(chunk.lower())
    return tuple(parts)


def version_newer(remote, current):
    """True, если remote строго новее current (по числовым частям).

    '1.6.13' > '1.6.12'; суффикс сборки ('1.6.13-mod1') новее базовой
    версии той же нумерации; на равной общей части число старше строки
    (semver-правило «релиз > пререлиз»)."""
    rt, ct = version_tuple(remote), version_tuple(current)
    if not rt or not ct:
        return False
    for a, b in zip(rt, ct):
        if a == b:
            continue
        if isinstance(a, int) and isinstance(b, int):
            return a > b
        return isinstance(a, int)     # число новее буквенного суффикса
    return len(rt) > len(ct)          # общая часть равна: длиннее = новее


def _http_get(url, timeout=None, as_bytes=False):
    """GET с таймаутом; возвращает str/bytes. Роняет UpdateError на отказ."""
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(
                req, timeout=timeout or UPDATE_GITHUB_TIMEOUT_S) as r:
            data = r.read()
            return data if as_bytes else data.decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        raise UpdateError(f'HTTP {e.code}: {url[:80]}')
    except Exception as e:
        raise UpdateError(f'{type(e).__name__}: {str(e)[:80]}')


def internet_available():
    """Есть ли доступ к github.com (быстрая проверка TCP, ~2 с)."""
    import socket
    try:
        s = socket.create_connection(('github.com', 443), timeout=2.0)
        s.close()
        return True
    except OSError:
        return False


def _remote_version(owner, repo, branch):
    """__version__ из picarpro/__init__.py ветки репозитория (raw).

    Сначала HEAD (ветка ПО УМОЛЧАНИЮ: переименование 'mod' -> 'main' не
    сломает проверку), затем явное имя ветки из конфига."""
    url_head = (f'https://raw.githubusercontent.com/{owner}/{repo}/'
                f'HEAD/picarpro/__init__.py')
    try:
        return _read_version_str(_http_get(url_head))
    except UpdateError:
        url = (f'https://raw.githubusercontent.com/{owner}/{repo}/'
               f'{branch}/picarpro/__init__.py')
        return _read_version_str(_http_get(url))


def _read_version_str(text):
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text or '')
    return m.group(1) if m else '?'


def _latest_release(owner, repo):
    """Последний релиз (тег, имя, URL, архив-ресурс) или None."""
    url = f'https://api.github.com/repos/{owner}/{repo}/releases/latest'
    try:
        data = json.loads(_http_get(url))
    except UpdateError:
        return None          # 404 = релизов нет; сеть пропала — тоже ок
    assets = []
    for a in data.get('assets') or []:
        name = a.get('name', '')
        if name.endswith(('.tar.gz', '.tgz', '.zip')):
            assets.append({'name': name, 'url': a.get('browser_download_url'),
                           'size': a.get('size', 0)})
    return {'tag': data.get('tag_name'), 'name': data.get('name'),
            'url': data.get('html_url'), 'notes': (data.get('body') or '')[:500],
            'assets': assets}


def github_check(root=None):
    """Проверить обновление на GitHub. Работает ТОЛЬКО при интернете.

    Возвращает словарь для /api/update/check: интернет, версии, есть ли
    обновление, релиз (если есть). Все сетевые ошибки — не исключение,
    а {'ok': False, 'error': ...}: кнопка в UI показывает причину.
    """
    repo = parse_repo(UPDATE_GITHUB_REPO)
    if repo is None:
        return {'ok': False, 'error': 'UPDATE_GITHUB_REPO is not a GitHub URL'}
    owner, name = repo
    if not internet_available():
        return {'ok': True, 'internet': False, 'repo': UPDATE_GITHUB_REPO,
                'message': 'нет доступа к интернету — подключите робота '
                           'к сети (клиентом Wi-Fi или кабелем)'}
    try:
        remote = _remote_version(owner, name, UPDATE_GITHUB_BRANCH)
        release = _latest_release(owner, name)
    except UpdateError as e:
        return {'ok': False, 'internet': True, 'repo': UPDATE_GITHUB_REPO,
                'error': f'GitHub недоступен: {e}'}
    current = current_version(root)
    update_available = version_newer(remote, current)
    result = {'ok': True, 'internet': True, 'repo': UPDATE_GITHUB_REPO,
              'branch': UPDATE_GITHUB_BRANCH, 'current': current,
              'remote': remote, 'update_available': update_available,
              'release': release}
    if not update_available and remote not in ('?', None):
        result['message'] = f'установлена последняя версия (v{current})'
    elif remote == '?':
        result['message'] = 'не удалось прочитать версию репозитория'
    return result


def github_download(dest_dir):
    """Скачать архив обновления с GitHub (ресурс релиза или tarball ветки).

    Возвращает путь к скачанному файлу. Ресурс релиза предпочтительнее —
    это проверенный владельцем срез, а не «живая» ветка.
    """
    repo = parse_repo(UPDATE_GITHUB_REPO)
    if repo is None:
        raise UpdateError('UPDATE_GITHUB_REPO is not a GitHub URL')
    owner, name = repo
    url = None
    release = _latest_release(owner, name)
    if release:
        for a in release.get('assets') or []:
            if a.get('url') and a.get('name', '').endswith(
                    ('.tar.gz', '.tgz', '.zip')):
                url = a['url']
                break
    if url is None:
        # tarball ветки (codeload отдаёт gz-поток без имени файла):
        # сначала ветка по умолчанию (HEAD), затем явная из конфига
        urls = (f'https://codeload.github.com/{owner}/{name}/tar.gz/HEAD',
                f'https://codeload.github.com/{owner}/{name}/tar.gz/refs/'
                f'heads/{UPDATE_GITHUB_BRANCH}')
    else:
        urls = (url,)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, 'github_update.tar.gz'
                        if (url is None or url.endswith('.gz')) else 'github_update.zip')
    last_err = None
    for u in urls:
        try:
            _download_to(u, dest)
            if os.path.getsize(dest) < 512:
                raise UpdateError('архив пуст — проверьте репозиторий/релиз')
            return dest
        except UpdateError as e:
            last_err = e
    try:
        os.remove(dest)
    except OSError:
        pass
    raise last_err or UpdateError('download failed')


def _download_to(url, dest):
    """Скачать url в dest с лимитом UPDATE_MAX_BYTES (вспомогательная)."""
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
            total = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > UPDATE_MAX_BYTES:
                    raise UpdateError(
                        f'архив больше лимита {UPDATE_MAX_BYTES // (1024 * 1024)} '
                        'МБ — скачайте и установите его вручную через панель')
                f.write(chunk)
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f'скачивание не удалось: {type(e).__name__}: '
                          f'{str(e)[:80]}')
    if os.path.getsize(dest) < 512:
        os.remove(dest)
        raise UpdateError('архив пуст — проверьте репозиторий/релиз')
    return dest


def github_install(root=None):
    """Скачать с GitHub и установить обновление (кнопка в меню)."""
    P = _paths(root)
    tmp = _ensure(P['tmp'])
    archive = github_download(tmp)
    try:
        result = apply_update(archive, root)
        result['source'] = 'github'
        return result
    finally:
        try:
            os.remove(archive)
        except OSError:
            pass
