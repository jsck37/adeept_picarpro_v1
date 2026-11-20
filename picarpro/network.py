"""Сетевые утилиты: определение IP, captive portal на порту 80, OLED-цикл.

Отличие от исходной версии: get_ip() кэшируется на 30 секунд. Раньше
каждый вызов get_status() (раз в секунду на каждого WS-клиента) порождал
до четырёх subprocess ('ip addr show' на каждый интерфейс).
"""
import socket
import subprocess
import threading
import time

from picarpro.logger import logger
from picarpro.utils.system_info import SystemInfo
from config import FLASK_PORT

_IP_TTL_S = 30.0
_ip_cache = {'value': None, 'ts': 0.0}
_ap_cache = {'value': None, 'ts': 0.0}
_ip_lock = threading.Lock()


def _detect_ip():
    try:
        for iface in ['wlan0', 'wlan1', 'uap0', 'eth0']:
            try:
                result = subprocess.run(
                    ["ip", "addr", "show", iface],
                    capture_output=True, text=True, timeout=2
                )
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("inet "):
                        ip = line.split()[1].split("/")[0]
                        if ip.startswith(("10.42.", "192.168.", "172.20.")):
                            return ip
            except Exception:
                continue
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip != "0.0.0.0":
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def get_ip():
    """IP машины с кэшем на 30 с (см. выше — устранение subprocess-шторма)."""
    now = time.monotonic()
    with _ip_lock:
        if _ip_cache['value'] is not None and now - _ip_cache['ts'] < _IP_TTL_S:
            return _ip_cache['value']
    ip = _detect_ip()
    with _ip_lock:
        _ip_cache['value'] = ip
        _ip_cache['ts'] = now
    return ip


def reset_ip_cache():
    with _ip_lock:
        _ip_cache['value'] = None
        _ip_cache['ts'] = 0.0
        _ap_cache['value'] = None
        _ap_cache['ts'] = 0.0


def _detect_ap_ip():
    """IP стороны точки доступа (wlan0/uap0 прежде всего); None, если пусто.

    В отличие от _detect_ip() подходит ЛЮБОЙ адрес, не только 10.42.* —
    HOTSPOT_IP в /etc/picarpro/hotspot.conf мог быть изменён владельцем.
    """
    for iface in ('wlan0', 'uap0', 'wlan1', 'eth0'):
        try:
            result = subprocess.run(
                ["ip", "addr", "show", iface],
                capture_output=True, text=True, timeout=2
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    ip = line.split()[1].split("/")[0]
                    if not ip.startswith("127."):
                        return ip
        except Exception:
            continue
    return None


def get_ap_ip():
    """IP точки доступа с кэшем на 30 с (None — интерфейсов нет/не подняты)."""
    now = time.monotonic()
    with _ip_lock:
        if _ap_cache['value'] is not None and now - _ap_cache['ts'] < _IP_TTL_S:
            return _ap_cache['value']
    ip = _detect_ap_ip()
    with _ip_lock:
        _ap_cache['value'] = ip
        _ap_cache['ts'] = now
    return ip


# Пути, по которым телефоны/ПК проверяют связность сразу после подключения к
# Wi-Fi. Раньше на них отвечали «Success» (204/200), чтобы Android не считал
# сеть «порталом входа» — и интерфейс НИКОГДА не открывался сам, только по
# адресу вручную. Теперь наоборот: проверка получает редирект на веб-UI, ОС
# распознаёт captive portal и сама открывает его окно с интерфейсом робота:
#   Android — «Вход в сеть» (CaptivePortalLogin), iOS — мини-браузер CNA,
#   Windows — «Войти в сеть», Firefox — своя плашка портала.
_PROBE_PATHS = frozenset((
    '/generate_204',                # Android (connectivitycheck.gstatic.com и др.)
    '/gen_204',                     # Android, запасной
    '/hotspot-detect.html',         # iOS/macOS CNA (captive.apple.com)
    '/library/test/success.html',   # часть устройств Apple
    '/connecttest.txt',             # Windows NCSI (Win10, www.msftncsi.com)
    '/ncsi.txt',                    # Windows NCSI
    '/redirect',                    # Windows NCSI (Win11, www.msftconnecttest.com)
    '/success.txt',                 # Firefox detectportal.firefox.com
))


def is_probe(path):
    """Служебная проверка связности ОС (путь без учёта query string)?"""
    return path.split('?', 1)[0] in _PROBE_PATHS


def portal_location(path='/', host='', port=None):
    """URL редиректа captive portal: IP точки доступа + порт веб-интерфейса.

    IP берётся с wlan0/uap0; если интерфейсов нет (скрипт на ПК) — Host из
    запроса; и вовсе без него — дефолтный HOTSPOT_IP из fix_wifi_hostapd.sh.
    """
    if port is None:
        port = FLASK_PORT
    ip = get_ap_ip() or (host or '').split(':')[0] or '10.42.0.1'
    return f'http://{ip}:{port}{path}'


def start_redirect_server(port=80, target_port=None):
    """Captive portal: порт 80 -> http://<IP точки доступа>:5000 (нужен root/CAP).

    На проверках связности редирект ведёт на корень интерфейса — именно он
    заставляет ОС открыть окно портала; обычные запросы сохраняют путь
    (http://10.42.0.1/main.html -> http://10.42.0.1:5000/main.html).
    """
    if target_port is None:
        target_port = FLASK_PORT
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class R(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.do_GET(head=True)

            def do_GET(self, head=False):
                # проверки ОС -> на корень интерфейса, остальное — путь сохраняется
                target = portal_location(
                    '/' if is_probe(self.path) else self.path,
                    self.headers.get('Host', ''), target_port)
                body = (f'<html><body>Redirecting to '
                        f'<a href="{target}">{target}</a></body></html>'
                        ).encode('utf-8')
                self.send_response(302)
                self.send_header('Location', target)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = HTTPServer(('0.0.0.0', port), R)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f'[Web] Port {port} captive portal -> :{target_port}')
        return True
    except Exception as e:
        logger.warning(f'[Web] Port {port} captive portal failed: {e}')
        return False


def oled_loop(state):
    """Обновление OLED: IP, температура CPU, RAM, предупреждение о питании."""
    port = FLASK_PORT
    while state.running:
        try:
            ip = get_ip()    # кэш 30 с: смена сети отражается на экране
            info = SystemInfo.get_all()
            ram = info['ram']
            low_v = info['low_voltage']
            if state.oled and state.oled._initialized:
                state.oled.set_low_voltage(low_v)
                state.oled.set_lines([
                    f'{ip}:{port}',
                    f'CPU:{info["cpu_temp"]}C {info["cpu_usage"]}%',
                    f'RAM:{ram["used_mb"]}/{ram["total_mb"]}M {ram["percent"]}%',
                ])
        except Exception:
            pass
        time.sleep(2.0)
