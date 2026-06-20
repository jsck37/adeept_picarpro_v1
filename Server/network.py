import socket, subprocess, threading, time
from Server.logger import logger
from Server.utils.system_info import SystemInfo
from config import FLASK_PORT


def get_ip():
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


def start_redirect_server(port=80, target_port=None):
    if target_port is None:
        target_port = FLASK_PORT
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        class R(BaseHTTPRequestHandler):
            def do_GET(self):
                host = self.headers.get('Host', '').split(':')[0]
                self.send_response(302)
                self.send_header('Location', f'http://{host}:{target_port}{self.path}')
                self.end_headers()
            def log_message(self, *a): pass
        server = HTTPServer(('0.0.0.0', port), R)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f'[Web] Port {port} redirect -> :{target_port}')
        return True
    except Exception as e:
        logger.warning(f'[Web] Port {port} redirect failed: {e}')
        return False


def oled_loop(state):
    ip = get_ip()
    port = FLASK_PORT
    while state.running:
        try:
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
