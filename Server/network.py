import socket, subprocess, threading, time
from config import FLASK_PORT, HOTSPOT_IP
from Server.logger import logger


def get_ip():
    try:
        for iface in ['wlan0', 'wlan1', 'uap0']:
            try:
                result = subprocess.run(
                    ["ip", "addr", "show", iface],
                    capture_output=True, text=True, timeout=2
                )
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("inet "):
                        ip = line.split()[1].split("/")[0]
                        if ip.startswith(("10.42.", "192.168.4.", "172.20.")):
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
    return HOTSPOT_IP


def start_redirect_server(port=80, target_port=None):
    if target_port is None:
        target_port = FLASK_PORT

    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                host = self.headers.get('Host', '')
                if ':' in host:
                    host = host.split(':')[0]
                redirect_url = f'http://{host}:{target_port}{self.path}'
                self.send_response(302)
                self.send_header('Location', redirect_url)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(
                    f'<html><body>Redirecting to <a href="{redirect_url}">'
                    f'{redirect_url}</a></body></html>'.encode()
                )

            def log_message(self, format, *args):
                pass

        server = HTTPServer(('0.0.0.0', port), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"[WebServer] Port {port} redirect -> :{target_port}")
        return True
    except PermissionError:
        logger.warning(f"[WebServer] Cannot bind port {port} (need root). "
                       f"Run with sudo or access http://IP:{target_port}")
        return False
    except OSError as e:
        if 'Address already in use' in str(e) or 'Permission denied' in str(e):
            logger.warning(f"[WebServer] Port {port} already in use or denied: {e}")
        else:
            logger.warning(f"[WebServer] Port {port} redirect failed: {e}")
        return False
    except Exception as e:
        logger.warning(f"[WebServer] Port {port} redirect failed: {e}")
        return False


def oled_loop(state):
    ip, port = get_ip(), FLASK_PORT
    while state.running:
        try:
            from Server.utils.system_info import SystemInfo
            info = SystemInfo.get_all()
            ram = info['ram']
            if state.oled:
                state.oled.set_lines([
                    f"{ip}:{port}",
                    f"CPU:{info['cpu_temp']}C {info['cpu_usage']}%",
                    f"RAM:{ram['used_mb']}/{ram['total_mb']}M {ram['percent']}%",
                ])
        except Exception:
            pass
        time.sleep(1.5)
