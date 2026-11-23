"""Подсчёт устройств, подключённых к точке доступа робота (v1.6.13).

Раньше «Устройства» и нижняя подсветка ожидания смотрели только на
WebSocket-клиенты веб-интерфейса: телефон мог быть подключён к точке
доступа (и жрать эфир), а подсветка всё равно мигала «ждём подключения».
Теперь устройство считается по факту Wi-Fi/DHCP-подключения:

  1. iw dev <iface> station dump  — авторитетный источник для hostapd-AP
     (ассоциированные станции; работает и без DHCP);
  2. leases dnsmasq               — если iw недоступен (нет прав/нет iw);
  3. /proc/net/arp                — последний фолбэк (любые соседние хосты).

Ничего не получилось (робот не в AP-режиме, нет ни iw, ни leases, ни arp —
например, запуск на ПК) — возвращается (None, None): вызывающий код
(SharedState.refresh_waiting_led) откатывается к прежнему поведению по
ws_clients. Опрос кэшируется на HOTSPOT_COUNT_TTL_S — вызовы идут из
статуса и фонового цикла подсветки.
"""
import re
import subprocess
import threading
import time

from picarpro.logger import logger

HOTSPOT_COUNT_TTL_S = 3.0
_IFACES = ('wlan0', 'wlan1', 'uap0')
# типовые пути leases dnsmasq (Debian/Raspberry Pi OS)
_LEASE_PATHS = (
    '/var/lib/misc/dnsmasq.leases',
    '/var/lib/dnsmasq/dnsmasq.leases',
    '/etc/picarpro/dnsmasq.leases',
)
_ARP_PATH = '/proc/net/arp'

_cache = {'value': None, 'ts': 0.0}
_none_logged = False
_lock = threading.Lock()


def _iw_station_count(iface, run=None):
    """Число ассоциированных станций (строки 'Station ...') или None."""
    run = run or subprocess.run
    try:
        r = run(['iw', 'dev', iface, 'station', 'dump'],
                capture_output=True, text=True, timeout=2)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return sum(1 for ln in (r.stdout or '').splitlines()
               if ln.strip().startswith('Station'))


def _lease_count(paths=_LEASE_PATHS):
    """Число активных DHCP-leases (не-timeout строки) или None."""
    for p in paths:
        try:
            with open(p, encoding='utf-8') as f:
                lines = [ln.split() for ln in f if ln.strip()]
            # формат: <expiry> <mac> <ip> <hostname> <client-id>
            now = time.time()
            n = 0
            for parts in lines:
                if len(parts) < 3:
                    continue
                exp = parts[0]
                if exp.isdigit():
                    if int(exp) < now:
                        continue        # истёкший lease
                elif exp.lower() == 'never':
                    pass
                else:
                    continue            # duid/неизвестный формат
                n += 1
            return n
        except OSError:
            continue
    return None


def _arp_count(arp_path=_ARP_PATH, local_ips=()):
    """Соседи из /proc/net/arp (завершённые записи, не мы сами) или None.

    Здесь считается УНИКАЛЬНЫЙ MAC: строка с flags 0x2/0x6 — завершённая
    запись; 0x0 — «incomplete» (не считаем). Сюда попадают и проводные
    соседи, но у робота роль шлюза играет точка доступа — это и есть её
    клиенты.
    """
    try:
        with open(arp_path, encoding='utf-8') as f:
            rows = f.read().splitlines()
    except OSError:
        return None
    macs = set()
    for ln in rows[1:]:
        parts = ln.split()
        if len(parts) < 6:
            continue
        ip, hw, flags, mac = parts[0], parts[1], parts[2], parts[3].lower()
        if hw != '0x1' or flags not in ('0x2', '0x6'):
            continue
        if not re.match(r'^[0-9a-f]{2}(:[0-9a-f]{2}){5}$', mac):
            continue
        if ip in local_ips:
            continue
        macs.add(mac)
    return len(macs)


def count_clients(ifaces=_IFACES, lease_paths=_LEASE_PATHS,
                  arp_path=_ARP_PATH, local_ips=(), use_cache=True):
    """(число устройств, источник) или (None, None), если посчитать нельзя.

    Источник: 'iw' | 'dnsmasq' | 'arp'. Параметры инъекятся для тестов.
    """
    now = time.monotonic()
    with _lock:
        if use_cache and _cache['value'] is not None \
                and now - _cache['ts'] < HOTSPOT_COUNT_TTL_S:
            return _cache['value']
    result = None
    for iface in ifaces:
        n = _iw_station_count(iface)
        if n is not None:
            result = (n, 'iw')
            break
    if result is None:
        n = _lease_count(lease_paths)
        if n is not None:
            result = (n, 'dnsmasq')
    if result is None:
        n = _arp_count(arp_path, local_ips)
        if n is not None:
            result = (n, 'arp')
    with _lock:
        _cache['value'] = result
        _cache['ts'] = now
    global _none_logged
    if result is None and not _none_logged:
        _none_logged = True
        logger.info('[Hotspot] клиенты не считаются: нет ни iw, ни leases, '
                    'ни arp — подсветка ждёт по веб-клиентам (как раньше)')
    return result if result is not None else (None, None)


def reset_cache():
    """Сброс кэша (тесты, смена сети)."""
    global _none_logged
    with _lock:
        _cache['value'] = None
        _cache['ts'] = 0.0
        _none_logged = False


def local_ip_candidates(get_ip_fn=None):
    """IP-адреса робота на интерфейсах — чтобы не считать саму себя по arp."""
    ips = []
    if get_ip_fn is not None:
        try:
            ip = get_ip_fn()
            if ip and not ip.startswith('127.'):
                ips.append(ip)
        except Exception:
            pass
    return ips


# ---------------------------------------------------------------------------
# v1.6.15: полный список устройств (для админ-панели) — не только счётчик.
# Слияние трёх источников по MAC:
#   iw station dump  -> MAC (ассоциированные станции, сигнал)
#   leases dnsmasq   -> MAC + IP + hostname
#   /proc/net/arp    -> MAC + IP (фолбэк для IP, если leases нет)
# ---------------------------------------------------------------------------

_MAC_RE = re.compile(r'^[0-9a-f]{2}(:[0-9a-f]{2}){5}$')


def _iw_stations(iface, run=None):
    """MAC'и ассоциированных станций + сигнал: [{mac, signal_dbm}] или []."""
    run = run or subprocess.run
    try:
        r = run(['iw', 'dev', iface, 'station', 'dump'],
                capture_output=True, text=True, timeout=2)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    out = []
    cur = None
    for ln in (r.stdout or '').splitlines():
        s = ln.strip()
        if s.startswith('Station'):
            cur = {'mac': s.split()[1].lower(), 'signal_dbm': None}
            out.append(cur)
        elif cur is not None and s.startswith('signal:'):
            try:
                # формат iw: "signal:  -53.00 dBm" — берём только число
                cur['signal_dbm'] = int(float(s.split(':')[1].split()[0]))
            except (ValueError, IndexError):
                pass
    return out


def _lease_entries(paths=_LEASE_PATHS):
    """Активные leases: [{mac, ip, hostname, expires}] (первый найденный файл)."""
    for p in paths:
        try:
            with open(p, encoding='utf-8') as f:
                lines = [ln.split() for ln in f if ln.strip()]
        except OSError:
            continue
        now = time.time()
        out = []
        for parts in lines:
            if len(parts) < 4:
                continue
            exp = parts[0]
            if exp.isdigit():
                if int(exp) < now:
                    continue        # истёкший lease
            elif exp.lower() == 'never':
                pass
            else:
                continue            # duid/неизвестный формат
            mac = parts[1].lower()
            if not _MAC_RE.match(mac):
                continue
            out.append({'mac': mac, 'ip': parts[2],
                        'hostname': parts[3] if parts[3] != '*' else '',
                        'expires': int(exp) if exp.isdigit() else None})
        return out
    return []


def _arp_entries(arp_path=_ARP_PATH, local_ips=()):
    """Завершённые записи arp: [{ip, mac}] (завершённые, не мы сами)."""
    try:
        with open(arp_path, encoding='utf-8') as f:
            rows = f.read().splitlines()
    except OSError:
        return []
    out = []
    for ln in rows[1:]:
        parts = ln.split()
        if len(parts) < 6:
            continue
        ip, hw, flags, mac = parts[0], parts[1], parts[2], parts[3].lower()
        if hw != '0x1' or flags not in ('0x2', '0x6'):
            continue
        if not _MAC_RE.match(mac):
            continue
        if ip in local_ips:
            continue
        out.append({'ip': ip, 'mac': mac})
    return out


def list_clients(ifaces=_IFACES, lease_paths=_LEASE_PATHS,
                 arp_path=_ARP_PATH, local_ips=(), run=None):
    """Список подключённых устройств: [{mac, ip, hostname, signal_dbm}].

    Слияние по MAC (v1.6.15). Устройства, известные только по iw (без
    DHCP), идут с пустыми ip/hostname. Список может быть пустым, даже
    когда clients подключены через чужой роутер (робот в клиентском
    режиме) — блокировки админ-панели в этом случае всё равно работают
    по MAC (iptables), просто строку не из чего собрать.
    """
    merged = {}
    for st in _iw_stations_all(ifaces, run=run):
        merged.setdefault(st['mac'], {})['signal_dbm'] = st.get('signal_dbm')
    for le in _lease_entries(lease_paths):
        merged.setdefault(le['mac'], {})
        merged[le['mac']]['ip'] = le['ip']
        merged[le['mac']]['hostname'] = le.get('hostname') or ''
    for ar in _arp_entries(arp_path, local_ips=local_ips):
        merged.setdefault(ar['mac'], {})
        merged[ar['mac']].setdefault('ip', ar['ip'])
    out = []
    for mac, info in merged.items():
        out.append({'mac': mac, 'ip': info.get('ip') or '',
                    'hostname': info.get('hostname') or '',
                    'signal_dbm': info.get('signal_dbm')})
    out.sort(key=lambda d: (not d['ip'], d['ip'], d['mac']))
    return out


def _iw_stations_all(ifaces, run=None):
    """Первый iface со станциями (или суммарный, если везде пусто)."""
    best = []
    for iface in ifaces:
        st = _iw_stations(iface, run=run)
        if st is None:
            continue
        if st:
            return st
        best = best or st
    return best
