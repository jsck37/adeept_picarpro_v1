#!/usr/bin/env python3
"""PiCar Pro v1 — Flask (5000) + WebSocket (8888) server.

Refactored:
  - Module system removed (no more dynamic script loading)
  - Lazy hardware init for fast systemd startup
  - OpenCV line-following added as autonomous mode
  - Log console fully integrated via WebSocket
  - Drift mode support (RWD + front steering)
  - Bluetooth gamepad management via web UI
  - Stick inversion and speed boost for DS4
  - Modular Flask blueprints
"""

import asyncio, json, os, signal, socket, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Server.logger import logger

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

from Server.config import (
    FLASK_PORT, WEBSOCKET_PORT, DEFAULT_SPEED,
    SERVO_COUNT, SERVO_INIT_ANGLE, CRANE_ENABLED,
    SERVO_STEERING, SERVO_CLAW_ARM, SERVO_CLAW_GRIP,
    CLAW_ARM_UP, CLAW_ARM_DOWN, CLAW_GRIP_OPEN, CLAW_GRIP_CLOSED,
    SWITCH_PINS, ULTRASONIC_ENABLED, LINE_TRACKER_ENABLED, DS4_ENABLED,
    DRIFT_ENABLED, HOTSPOT_IP, STEER_MAP,
)
from Server.hardware.motors import MotorController
from Server.hardware.servos import ServoController
from Server.hardware.leds_ws2812 import LEDController
from Server.hardware.ultrasonic import UltrasonicSensor
from Server.hardware.switch import SwitchController
from Server.hardware.oled import OLEDDisplay
from Server.hardware.buzzer import BuzzerController
from Server.hardware.mpu6050 import MPU6050Controller
from Server.camera.camera_opencv import Camera, CV_NONE, CV_LINE, CV_HAND
from Server.functions.autonomous import AutonomousController
from Server.utils.system_info import SystemInfo
from Server.utils.log_buffer import log_buffer

SERVO_CAL_FILE = os.path.join(os.path.dirname(__file__), "servo_cal.json")


# ── Servo calibration persistence ──────────────────────────────────────

def load_servo_cal():
    try:
        if os.path.isfile(SERVO_CAL_FILE):
            with open(SERVO_CAL_FILE) as f:
                return json.load(f).get("init_angles", [SERVO_INIT_ANGLE] * SERVO_COUNT)
    except Exception:
        pass
    return [SERVO_INIT_ANGLE] * SERVO_COUNT


def save_servo_cal(angles):
    try:
        with open(SERVO_CAL_FILE, "w") as f:
            json.dump({"init_angles": angles}, f, indent=2)
    except Exception:
        pass


def get_ip():
    """Get the best IP address for web UI access.

    Checks all network interfaces for a usable IP address.
    Prioritises hotspot gateway addresses (10.42.x.x, 192.168.4.x)
    since those are the IPs clients connect to when the Pi is an AP.
    """
    try:
        import subprocess
        # Check all wireless interfaces for hotspot IP
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
                        # Hotspot / AP typical ranges
                        if ip.startswith(("10.42.", "192.168.4.", "172.20.")):
                            return ip
            except Exception:
                continue
    except Exception:
        pass
    try:
        # Try to get any non-loopback IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip != "0.0.0.0":
            return ip
    except Exception:
        pass
    # Fallback to hotspot IP
    return HOTSPOT_IP


def start_redirect_server(port=80, target_port=None):
    """Start a lightweight HTTP server on port 80 that redirects all
    requests to the Flask app on target_port.

    This makes the web panel accessible by simply typing the Pi's
    IP address in the browser (without :5000).  It also handles
    captive portal detection requests from mobile devices, so that
    connecting to the Pi's WiFi hotspot automatically shows the
    control panel.

    Requires root/sudo to bind port 80 — if binding fails, a
    warning is logged and the function returns silently.
    """
    if target_port is None:
        target_port = FLASK_PORT

    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Get the Host header to build the redirect URL
                host = self.headers.get('Host', '')
                # Remove existing port if present
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
                # Suppress access logs for the redirect server
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


# ── Shared state ──────────────────────────────────────────────────────

class SharedState:
    def __init__(self):
        self.speed = DEFAULT_SPEED
        self.running = True
        self.motors = self.servos = self.leds = self.ultrasonic = None
        self.switches = self.oled = self.buzzer = self.mpu6050 = None
        self.autonomous = self.voice = self.camera = self.ds4 = None
        self.ws_clients = set()
        self._hw_inited = False

    def init_camera(self):
        if not self.camera:
            self.camera = Camera()

    def get_status(self):
        info = SystemInfo.get_all()
        ram = info['ram']
        ultra_ok = self.ultrasonic and self.ultrasonic._initialized
        mpu_ok = self.mpu6050 and self.mpu6050.initialized
        return {
            "cpu_temp": info["cpu_temp"],
            "cpu_usage": info["cpu_usage"],
            "ram_percent": ram["percent"],
            "ram_used_mb": ram["used_mb"],
            "ram_total_mb": ram["total_mb"],
            "distance": self.ultrasonic.get_last_distance() if ultra_ok else 0,
            "mpu6050": self.mpu6050.get_data() if mpu_ok else None,
            "cv_mode": self.camera.cv_thread.cv_mode if self.camera else "none",
            "auto_active": self.autonomous.is_active() if self.autonomous else False,
            "auto_mode": self.autonomous._current_mode if self.autonomous else "none",
            "speed": self.speed,
            "crane_enabled": CRANE_ENABLED,
            "ultrasonic_enabled": ULTRASONIC_ENABLED,
            "line_tracker_enabled": LINE_TRACKER_ENABLED,
            "hw": {
                "motors": self.motors._initialized if self.motors else False,
                "servos": self.servos._pwm_initialized if self.servos else False,
                "leds": self.leds._initialized if self.leds else False,
                "buzzer": self.buzzer._initialized if self.buzzer else False,
                "switches": self.switches._initialized if self.switches else False,
                "ultrasonic": ultra_ok,
                "mpu6050": mpu_ok,
                "oled": self.oled._initialized if self.oled else False,
                "camera": self.camera is not None,
                "crane": CRANE_ENABLED,
                "ds4": self.ds4.connected if self.ds4 else False,
            },
            "ds4": self.ds4.get_status() if self.ds4 else None,
        }

    def shutdown_hardware(self):
        self.running = False
        logger.info("[WebServer] Shutting down...")
        if self.autonomous:
            try:
                self.autonomous.shutdown()
            except Exception:
                pass
        if self.voice:
            try:
                self.voice.shutdown()
            except Exception:
                pass
        if self.camera:
            try:
                self.camera.shutdown()
            except Exception:
                pass
        for hw in (self.motors, self.servos, self.leds, self.switches,
                   self.ultrasonic, self.buzzer, self.oled, self.mpu6050, self.ds4):
            if hw:
                try:
                    hw.shutdown()
                except Exception:
                    pass
        logger.info("[WebServer] Shutdown complete")


state = SharedState()


# ── OLED info display loop ────────────────────────────────────────────

def oled_loop():
    ip, port = get_ip(), FLASK_PORT
    while state.running:
        try:
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


# ── Command processing ────────────────────────────────────────────────

def process_command(data):
    cmd = data.get('cmd', '')
    p = data.get('params', {})
    r = {'ok': False, 'cmd': cmd}

    if cmd == 'move':
        d = p.get('dir', 'stop')
        if d in ('forward',):
            state.motors.move(state.speed, 'forward', 'no', 0.5)
        elif d in ('backward',):
            state.motors.move(state.speed, 'backward', 'no', 0.5)
        elif d in ('left', 'right'):
            state.motors.stop()
        elif d.startswith('forward_'):
            state.motors.move(state.speed, 'forward', d.split('_')[1], 0.3)
        elif d.startswith('backward_'):
            state.motors.move(state.speed, 'backward', d.split('_')[1], 0.3)
        elif d == 'stop':
            state.motors.stop()
        state.servos.set_angle(SERVO_STEERING, STEER_MAP.get(d, 90))
        r = {'ok': True, 'cmd': cmd, 'dir': d, 'steer': STEER_MAP.get(d, 90)}

    elif cmd == 'speed':
        try:
            state.speed = max(0, min(100, int(p.get('value', DEFAULT_SPEED))))
            r = {'ok': True, 'speed': state.speed}
        except Exception:
            r['error'] = 'Invalid speed'

    elif cmd == 'servo':
        sid, ang = int(p.get('id', 0)), int(p.get('angle', 90))
        if 0 <= sid < SERVO_COUNT:
            state.servos.set_angle(sid, max(0, min(180, ang)))
            r = {'ok': True, 'id': sid, 'angle': ang}

    elif cmd == 'servo_calibrate':
        sid, ang = int(p.get('id', 0)), int(p.get('angle', 90))
        if 0 <= sid < SERVO_COUNT:
            ang = max(0, min(180, ang))
            state.servos.set_init_angle(sid, ang)
            cal = load_servo_cal()
            cal[sid] = ang
            save_servo_cal(cal)
            r = {'ok': True, 'id': sid, 'init_angle': ang}

    elif cmd == 'servo_home':
        state.servos.move_init()
        r = {'ok': True}

    elif cmd == 'led':
        mode = p.get('mode', 'off')
        color = p.get('color', [255, 0, 0])
        if mode in ('off', 'solid', 'breath', 'flow', 'rainbow', 'police', 'colorWipe'):
            try:
                color = tuple(max(0, min(255, int(c))) for c in color[:3])
            except Exception:
                color = (255, 0, 0)
            state.leds.set_mode(mode, color)
            r = {'ok': True, 'mode': mode}

    elif cmd == 'buzzer':
        key = {'beep': 'beep', 'birthday': 'happy_birthday'}.get(
            p.get('melody', 'beep'))
        if key:
            state.buzzer.play_melody(key)
            r = {'ok': True}

    elif cmd == 'buzzer_stop':
        state.buzzer.stop()
        r = {'ok': True}

    elif cmd == 'claw':
        if not CRANE_ENABLED:
            r['error'] = 'Crane not enabled'
        else:
            act = p.get('action', '')
            actions = {
                'arm_up': (SERVO_CLAW_ARM, CLAW_ARM_UP),
                'arm_down': (SERVO_CLAW_ARM, CLAW_ARM_DOWN),
                'grip_open': (SERVO_CLAW_GRIP, CLAW_GRIP_OPEN),
                'grip_close': (SERVO_CLAW_GRIP, CLAW_GRIP_CLOSED),
            }
            if act in actions:
                state.servos.set_angle(*actions[act])
                r = {'ok': True, 'action': act}

    elif cmd == 'switch':
        sid, st = int(p.get('id', 0)), p.get('state', False)
        mx = len(SWITCH_PINS) if state.switches._initialized else 0
        if 0 <= sid < mx:
            (state.switches.on if st else state.switches.off)(sid)
            r = {'ok': True}

    elif cmd == 'cv_mode':
        mode_map = {
            'none': CV_NONE,
            'findlineCV': CV_LINE,
            'trackHand': CV_HAND,
        }
        cv = mode_map.get(p.get('mode', 'none'))
        if cv is not None:
            state.init_camera()
            state.camera.set_cv_mode(cv)
            r = {'ok': True, 'mode': p.get('mode')}

    elif cmd == 'i2c_scan':
        from Server.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
        devs = i2c_scan()
        addr, who = find_mpu6050_on_bus()
        r = {
            'ok': True,
            'devices': [f'0x{a:02X}' for a in devs],
            'mpu6050_found': addr is not None,
            'mpu6050_addr': f'0x{addr:02X}' if addr else None,
            'mpu6050_who_am_i': f'0x{who:02X}' if who else None,
        }

    elif cmd == 'auto':
        func = p.get('func', 'stop')
        valid_funcs = ('radarScan', 'automatic', 'trackLine',
                       'trackLineCV', 'trackHand', 'keepDistance', 'stop')
        if func in valid_funcs:
            # For CV line following, ensure camera is available
            if func == 'trackLineCV':
                state.init_camera()
                if state.camera:
                    state.autonomous.set_camera(state.camera)
            # For hand tracking, ensure camera is available
            if func == 'trackHand':
                state.init_camera()
                if state.camera:
                    state.autonomous.set_camera(state.camera)
            if func == 'stop':
                state.autonomous.stop()
            else:
                state.autonomous.start(func)
            r = {'ok': True, 'func': func}
        else:
            r['error'] = f'Unknown auto function: {func}'

    elif cmd == 'get_info':
        r = {'ok': True}
        r.update(state.get_status())

    elif cmd == 'ds4_status':
        r = {'ok': True}
        r.update(state.ds4.get_status() if state.ds4 else {'enabled': False, 'connected': False})

    elif cmd == 'drift':
        enabled = p.get('enabled', False)
        if DRIFT_ENABLED:
            state.motors.set_drift_mode(enabled)
            if state.ds4:
                state.ds4._drift_mode = enabled
            r = {'ok': True, 'drift_mode': enabled}
        else:
            r['error'] = 'Drift mode not enabled in config'

    elif cmd == 'get_log':
        after = p.get('after_ts', 0.0)
        lines = log_buffer.get_lines_since(after_ts=after, max_lines=500)
        r = {'ok': True, 'lines': [[ts, txt] for ts, txt in lines]}

    elif cmd == 'clear_log':
        with log_buffer._lock:
            log_buffer._lines.clear()
        r = {'ok': True}

    elif cmd == 'voice':
        action = p.get('action', 'stop')
        if state.voice:
            if action == 'start':
                state.voice.start()
                r = {'ok': True, 'action': 'start'}
            elif action == 'stop':
                state.voice.stop()
                r = {'ok': True, 'action': 'stop'}
            else:
                r['error'] = f'Unknown voice action: {action}'
        else:
            r['error'] = 'Voice control not available'

    return r


# ── WebSocket handler ─────────────────────────────────────────────────

async def ws_handler(ws, path=None):
    state.ws_clients.add(ws)
    # Send initial status + recent log history
    try:
        await ws.send(json.dumps({'type': 'status', 'data': state.get_status()}))
        recent = log_buffer.get_lines(last_n=100)
        if recent:
            await ws.send(json.dumps({
                'type': 'log_history',
                'lines': [[ts, txt] for ts, txt in recent],
            }))
    except Exception:
        pass

    # Subscribe to new log lines
    log_queue = asyncio.Queue()

    def on_log(text):
        try:
            log_queue.put_nowait(text)
        except Exception:
            pass

    log_buffer.subscribe(on_log)

    try:
        async def forward_logs():
            try:
                while state.running:
                    try:
                        text = await asyncio.wait_for(log_queue.get(), timeout=0.5)
                        await ws.send(json.dumps({'type': 'log', 'text': text}))
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break
            except Exception:
                pass

        log_task = asyncio.create_task(forward_logs())

        async for msg in ws:
            try:
                r = process_command(json.loads(msg))
                await ws.send(json.dumps({'type': 'response', 'data': r}))
            except json.JSONDecodeError:
                await ws.send(json.dumps({
                    'type': 'response',
                    'data': {'ok': False, 'error': 'Invalid JSON'},
                }))
            except Exception as e:
                await ws.send(json.dumps({
                    'type': 'response',
                    'data': {'ok': False, 'error': str(e)},
                }))
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception:
        pass
    finally:
        log_task.cancel()
        log_buffer.unsubscribe(on_log)
        state.ws_clients.discard(ws)


async def status_broadcast():
    while state.running:
        if state.ws_clients:
            try:
                msg = json.dumps({'type': 'status', 'data': state.get_status()})
                gone = set()
                for ws in state.ws_clients:
                    try:
                        await ws.send(msg)
                    except Exception:
                        gone.add(ws)
                state.ws_clients -= gone
            except Exception:
                pass
        await asyncio.sleep(1.5)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    # LogBuffer already receives logs via loguru _logbuffer_sink
    # (no need for log_buffer.install() since we don't use print() anymore)

    logger.info("=" * 50)
    logger.info("  PiCar Pro v1 (Flask + WebSocket)")
    logger.info("=" * 50)

    if not HAS_WS:
        logger.error("[WebServer] websockets not installed!")
        sys.exit(1)

    # ── Hardware init (lazy where possible) ──────────────────────────
    logger.info("[WebServer] Initialising hardware...")

    state.motors = MotorController()
    state.servos = ServoController()
    state.leds = LEDController()
    state.switches = SwitchController()
    state.oled = OLEDDisplay()
    state.buzzer = BuzzerController()

    # MPU6050 — init in background to avoid blocking startup
    try:
        state.mpu6050 = MPU6050Controller()
    except Exception as e:
        logger.error(f"[MPU6050] Error: {e}")

    state.ultrasonic = UltrasonicSensor()

    if DS4_ENABLED:
        try:
            from Server.hardware.ds4 import DS4Controller
            state.ds4 = DS4Controller()
        except Exception as e:
            logger.error(f"[DS4] Init error: {e}")

    state.autonomous = AutonomousController(state.motors, state.servos, state.ultrasonic)
    # Camera ref for CV line following — set lazily when camera is initialised

    try:
        from Server.functions.voice_command import VoiceCommandController
        state.voice = VoiceCommandController(state.servos, state.motors)
    except Exception:
        pass

    # Apply saved servo calibration
    cal = load_servo_cal()
    for i, a in enumerate(cal):
        if 0 <= i < SERVO_COUNT:
            state.servos.set_init_angle(i, a)
    try:
        state.servos.move_init()
    except Exception:
        pass

    # OLED info display
    ip = get_ip()
    if state.oled:
        state.oled.set_lines([f"{ip}:{FLASK_PORT}", "Starting...", "", ""])
    threading.Thread(target=oled_loop, daemon=True).start()

    # Flask in background thread
    from Server.app import create_app
    app = create_app(state)
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0", port=FLASK_PORT, threaded=True,
            debug=False, use_reloader=False,
        ),
        daemon=True,
    ).start()
    logger.info(f"[WebServer] Flask on :{FLASK_PORT}")

    # Port 80 redirect for hotspot / captive portal access
    start_redirect_server(port=80, target_port=FLASK_PORT)

    # Signal handlers
    signal.signal(signal.SIGINT, lambda s, f: (state.shutdown_hardware(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (state.shutdown_hardware(), sys.exit(0)))

    # DS4 start
    if state.ds4:
        state.ds4.start(
            motors=state.motors, servos=state.servos, leds=state.leds,
            buzzer=state.buzzer, switches=state.switches,
            speed=state.speed, shared_state=state,
            autonomous=state.autonomous,
        )
        # Auto-connect to last known gamepad via bluetoothctl
        try:
            from Server.routes.bluetooth_routes import auto_connect_on_boot
            auto_connect_on_boot(state.ds4)
        except Exception as e:
            logger.warning(f"[WebServer] BT auto-connect setup failed: {e}")

    logger.info("-" * 50)
    logger.info(f"  MPU6050: {'ON' if state.mpu6050.initialized else 'OFF (retrying)'}")
    logger.info(f"  Buzzer:  {'ON' if state.buzzer._initialized else 'OFF'}")
    logger.info(f"  Crane:   {'ON' if CRANE_ENABLED else 'OFF'}")
    logger.info(f"  DS4:     {'ON' if state.ds4 else 'OFF'}")
    logger.info(f"  Drift:   {'ON' if DRIFT_ENABLED else 'OFF'}")
    logger.info("-" * 50)
    logger.info(f"  Web UI:  http://{ip}:{FLASK_PORT}")
    logger.info(f"  Hotspot: {HOTSPOT_IP}:{FLASK_PORT}")
    logger.info("-" * 50)

    async def run():
        async with websockets.serve(ws_handler, "0.0.0.0", WEBSOCKET_PORT):
            logger.info(f"[WebServer] Ready! http://{ip}:{FLASK_PORT}")
            await status_broadcast()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown_hardware()


if __name__ == "__main__":
    main()
