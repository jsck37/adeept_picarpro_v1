#!/usr/bin/env python3
"""PiCar Pro WebServer — Flask + WebSocket architecture."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    print("[WebServer] WARNING: websockets library not installed!")
    print("[WebServer] Install with: pip3 install websockets")

from Server.config import (
    FLASK_PORT, WEBSOCKET_PORT, DEFAULT_SPEED,
    SERVO_COUNT, SERVO_INIT_ANGLE, CRANE_ENABLED,
    HEADLIGHT_LEFT_PORT, HEADLIGHT_RIGHT_PORT,
    SERVO_STEERING,
)
from Server.hardware.motors import MotorController
from Server.hardware.servos import ServoController
from Server.hardware.leds_ws2812 import LEDController
from Server.hardware.ultrasonic import UltrasonicSensor
from Server.hardware.switch import SwitchController
from Server.hardware.oled import OLEDDisplay
from Server.hardware.buzzer import BuzzerController
from Server.hardware.mpu6050 import MPU6050Controller
from Server.camera.camera_opencv import (
    Camera, CV_MODE_NONE, CV_MODE_FIND_COLOR, CV_MODE_FIND_LINE, CV_MODE_WATCHDOG,
)
from Server.functions.autonomous import AutonomousController
from Server.utils.system_info import SystemInfo
from Server.modules import get_module_list, get_module_by_id, get_module_path


class ModuleRunner:
    """Run and manage example module scripts in subprocesses."""

    def __init__(self):
        self._process = None
        self._current_module = None
        self._lock = threading.Lock()
        self._last_command = "Ready"

    def start(self, module_id):
        with self._lock:
            self.stop()

            path = get_module_path(module_id)
            if path is None:
                return False, f"Module '{module_id}' not found"

            if not os.path.isfile(path):
                return False, f"Module file not found: {path}"

            try:
                self._process = subprocess.Popen(
                    [sys.executable, path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=os.path.dirname(os.path.dirname(os.path.dirname(path))),
                )
                self._current_module = module_id
                self._last_command = f"Run: {module_id}"
                return True, f"Started: {module_id}"
            except Exception as e:
                return False, str(e)

    def start_upload(self, filepath):
        with self._lock:
            self.stop()

            if not os.path.isfile(filepath):
                return False, f"File not found: {filepath}"

            try:
                self._process = subprocess.Popen(
                    [sys.executable, filepath],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                )
                name = os.path.basename(filepath)
                self._current_module = name
                self._last_command = f"Run: {name}"
                return True, f"Started: {name}"
            except Exception as e:
                return False, str(e)

    def stop(self):
        with self._lock:
            if self._process is not None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    try:
                        self._process.wait(timeout=2)
                    except Exception:
                        pass
                self._process = None
                self._current_module = None
                self._last_command = "Stopped"

    def set_command(self, cmd):
        with self._lock:
            self._last_command = cmd

    @property
    def running_module(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._current_module
            if self._process is not None:
                self._process = None
                self._current_module = None
            return None

    @property
    def last_command(self):
        with self._lock:
            return self._last_command

    def get_status_map(self):
        with self._lock:
            result = {}
            if self._process is not None:
                retcode = self._process.poll()
                if retcode is None:
                    result[self._current_module] = 'running'
                elif retcode == 0:
                    result[self._current_module] = 'exited'
                else:
                    result[self._current_module] = 'error'
            return result


SERVO_CAL_FILE = os.path.join(os.path.dirname(__file__), "servo_cal.json")


def load_servo_cal():
    try:
        if os.path.isfile(SERVO_CAL_FILE):
            with open(SERVO_CAL_FILE, "r") as f:
                data = json.load(f)
                return data.get("init_angles", [SERVO_INIT_ANGLE] * SERVO_COUNT)
    except Exception:
        pass
    return [SERVO_INIT_ANGLE] * SERVO_COUNT


def save_servo_cal(init_angles):
    try:
        with open(SERVO_CAL_FILE, "w") as f:
            json.dump({"init_angles": init_angles}, f, indent=2)
    except Exception as e:
        print(f"[ServoCal] Failed to save: {e}")


def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


class SharedState:

    def __init__(self):
        self.speed = DEFAULT_SPEED
        self.running = True
        self.motors = None
        self.servos = None
        self.leds = None
        self.ultrasonic = None
        self.switches = None
        self.oled = None
        self.buzzer = None
        self.mpu6050 = None
        self.autonomous = None
        self.voice = None
        self.camera = None
        self.module_runner = ModuleRunner()
        self.ws_clients = set()

    def init_camera(self):
        if self.camera is None:
            self.camera = Camera()

    def get_status(self):
        info = SystemInfo.get_all()
        ram = info['ram']
        return {
            "cpu_temp": info["cpu_temp"],
            "cpu_usage": info["cpu_usage"],
            "ram_percent": ram["percent"],
            "ram_used_mb": ram["used_mb"],
            "ram_total_mb": ram["total_mb"],
            "ram_used": ram["used"],
            "ram_total": ram["total"],
            "distance": self.ultrasonic.get_last_distance() if self.ultrasonic else 0,
            "mpu6050": self.mpu6050.get_data() if self.mpu6050 and self.mpu6050.initialized else None,
            "cv_mode": self.camera.cv_thread.cv_mode if self.camera else "none",
            "auto_active": self.autonomous.is_active() if self.autonomous else False,
            "running_module": self.module_runner.running_module,
            "module_status": self.module_runner.get_status_map(),
            "speed": self.speed,
            "crane_enabled": CRANE_ENABLED,
            "hw": {
                "motors":     self.motors._initialized if self.motors else False,
                "servos":     self.servos._pwm_initialized if self.servos else False,
                "leds":       self.leds._initialized if self.leds else False,
                "buzzer":     self.buzzer._initialized if self.buzzer else False,
                "switches":   self.switches._initialized if self.switches else False,
                "ultrasonic": self.ultrasonic._initialized if self.ultrasonic else False,
                "mpu6050":    self.mpu6050.initialized if self.mpu6050 else False,
                "oled":       self.oled._initialized if self.oled else False,
                "camera":     self.camera is not None,
                "autonomous":  (self.autonomous is not None
                                and self.motors._initialized
                                and self.ultrasonic._initialized)
                               if self.autonomous else False,
            },
        }

    def shutdown_hardware(self):
        self.running = False
        print("[WebServer] Shutting down hardware...")

        self.module_runner.stop()
        if self.motors:
            self.motors.stop()
        if self.autonomous:
            self.autonomous.shutdown()
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
        for hw in (self.servos, self.leds, self.switches, self.ultrasonic, self.buzzer, self.oled, self.mpu6050):
            if hw:
                try:
                    hw.shutdown()
                except Exception:
                    pass
        print("[WebServer] Shutdown complete")


state = SharedState()


def oled_update_loop():
    ip = get_ip_address()
    port = FLASK_PORT

    while state.running:
        try:
            info = SystemInfo.get_all()
            ram = info['ram']
            cmd = state.module_runner.last_command
            mod = state.module_runner.running_module

            line1 = f"{ip}:{port}"
            line2 = f"CPU:{info['cpu_temp']}C {info['cpu_usage']}%"
            line3 = f"RAM:{ram['used_mb']}/{ram['total_mb']}M {ram['percent']}%"
            line4 = mod if mod else cmd

            if state.oled:
                state.oled.set_lines([line1, line2, line3, line4])
        except Exception as e:
            print(f"[OLED] Update error: {e}")

        time.sleep(1.5)


def process_command(data):
    cmd = data.get('cmd', '')
    params = data.get('params', {})
    result = {'ok': False, 'cmd': cmd}

    if cmd == 'move':
        direction = params.get('dir', 'stop')
        state.module_runner.set_command(f"Move: {direction}")

        steer_angles = {
            'forward': 90, 'backward': 90,
            'left': 150, 'right': 30,
            'forward_left': 120, 'forward_right': 60,
            'backward_left': 120, 'backward_right': 60,
            'stop': 90,
        }

        if direction == 'forward':
            state.motors.move(state.speed, 'forward', 'no', 0.5)
        elif direction == 'backward':
            state.motors.move(state.speed, 'backward', 'no', 0.5)
        elif direction == 'left':
            state.motors.move(state.speed, 'forward', 'left', 0.4)
        elif direction == 'right':
            state.motors.move(state.speed, 'forward', 'right', 0.4)
        elif direction == 'forward_left':
            state.motors.move(state.speed, 'forward', 'left', 0.3)
        elif direction == 'forward_right':
            state.motors.move(state.speed, 'forward', 'right', 0.3)
        elif direction == 'backward_left':
            state.motors.move(state.speed, 'backward', 'left', 0.3)
        elif direction == 'backward_right':
            state.motors.move(state.speed, 'backward', 'right', 0.3)
        elif direction == 'stop':
            state.motors.stop()
            state.module_runner.set_command("Ready")
        else:
            result['error'] = f'Unknown direction: {direction}'
            return result

        steer_angle = steer_angles.get(direction, 90)
        state.servos.set_angle(SERVO_STEERING, steer_angle)

        result = {'ok': True, 'cmd': cmd, 'dir': direction, 'steer': steer_angle}

    elif cmd == 'speed':
        try:
            state.speed = max(0, min(100, int(params.get('value', DEFAULT_SPEED))))
            result = {'ok': True, 'cmd': cmd, 'speed': state.speed}
        except (ValueError, TypeError):
            result['error'] = 'Invalid speed value'

    elif cmd == 'servo':
        servo_id = int(params.get('id', 0))
        angle = int(params.get('angle', 90))
        if 0 <= servo_id < SERVO_COUNT:
            angle = max(0, min(180, angle))
            state.servos.set_angle(servo_id, angle)
            state.module_runner.set_command(f"Servo S{servo_id}:{angle}")
            result = {'ok': True, 'cmd': cmd, 'id': servo_id, 'angle': angle}
        else:
            result['error'] = f'Servo id must be 0-{SERVO_COUNT-1}'

    elif cmd == 'servo_calibrate':
        servo_id = int(params.get('id', 0))
        angle = int(params.get('angle', 90))
        if 0 <= servo_id < SERVO_COUNT:
            angle = max(0, min(180, angle))
            state.servos.set_init_angle(servo_id, angle)
            cal = load_servo_cal()
            cal[servo_id] = angle
            save_servo_cal(cal)
            state.module_runner.set_command(f"Cal S{servo_id}:{angle}")
            result = {'ok': True, 'cmd': cmd, 'id': servo_id, 'init_angle': angle}
        else:
            result['error'] = f'Servo id must be 0-{SERVO_COUNT-1}'

    elif cmd == 'servo_home':
        state.servos.move_init()
        state.module_runner.set_command("Servo Home")
        result = {'ok': True, 'cmd': cmd}

    elif cmd == 'led':
        mode = params.get('mode', 'off')
        color = params.get('color', [255, 0, 0])
        valid_modes = ('off', 'solid', 'breath', 'flow', 'rainbow', 'police', 'colorWipe')
        if mode in valid_modes:
            try:
                color = tuple(max(0, min(255, int(c))) for c in color[:3])
            except (ValueError, TypeError):
                color = (255, 0, 0)
            state.leds.set_mode(mode, color)
            state.module_runner.set_command(f"LED: {mode}")
            result = {'ok': True, 'cmd': cmd, 'mode': mode}
        else:
            result['error'] = f'Invalid mode. Use: {", ".join(valid_modes)}'

    elif cmd == 'buzzer':
        melody = params.get('melody', 'beep')
        melody_map = {'beep': 'beep', 'alarm': 'alarm', 'birthday': 'happy_birthday'}
        melody_key = melody_map.get(melody)
        if melody_key:
            state.buzzer.play_melody(melody_key)
            state.module_runner.set_command(f"Buzzer: {melody}")
            result = {'ok': True, 'cmd': cmd, 'melody': melody}
        else:
            result['error'] = f'Unknown melody. Use: {", ".join(melody_map.keys())}'

    elif cmd == 'switch':
        switch_id = int(params.get('id', 0))
        switch_state = params.get('state', False)
        if 0 <= switch_id < 3:
            if switch_state:
                state.switches.on(switch_id)
            else:
                state.switches.off(switch_id)
            state.module_runner.set_command(f"Switch {switch_id}: {'ON' if switch_state else 'OFF'}")
            result = {'ok': True, 'cmd': cmd, 'id': switch_id, 'state': switch_state}
        else:
            result['error'] = 'Switch id must be 0-2'

    elif cmd == 'cv_mode':
        mode = params.get('mode', 'none')
        mode_map = {
            'none': CV_MODE_NONE, 'findColor': CV_MODE_FIND_COLOR,
            'findlineCV': CV_MODE_FIND_LINE, 'watchDog': CV_MODE_WATCHDOG,
        }
        cv_mode = mode_map.get(mode)
        if cv_mode is not None:
            state.init_camera()
            state.camera.set_cv_mode(cv_mode)
            state.module_runner.set_command(f"CV: {mode}")
            result = {'ok': True, 'cmd': cmd, 'mode': mode}
        else:
            result['error'] = f'Unknown mode. Use: {", ".join(mode_map.keys())}'

    elif cmd == 'auto':
        func = params.get('func', 'stop')
        valid_funcs = ('radarScan', 'automatic', 'trackLine', 'keepDistance', 'stop')
        if func in valid_funcs:
            if func == 'stop':
                state.autonomous.stop()
                state.module_runner.set_command("Auto Stop")
            else:
                state.autonomous.start(func)
                state.module_runner.set_command(f"Auto: {func}")
            result = {'ok': True, 'cmd': cmd, 'func': func}
        else:
            result['error'] = f'Unknown function. Use: {", ".join(valid_funcs)}'

    elif cmd == 'module_start':
        module_id = params.get('id', '')
        upload_dir = os.path.join(os.path.dirname(__file__), "modules", "uploads")
        if module_id.startswith('upload_'):
            fname = module_id[len('upload_'):]
            fpath = os.path.join(upload_dir, fname)
            ok, msg = state.module_runner.start_upload(fpath)
        else:
            ok, msg = state.module_runner.start(module_id)
        result = {'ok': ok, 'cmd': cmd, 'message': msg, 'id': module_id}

    elif cmd == 'module_stop':
        state.module_runner.stop()
        result = {'ok': True, 'cmd': cmd}

    elif cmd == 'get_modules':
        lang = params.get('lang', 'en')
        modules = get_module_list(lang)
        upload_dir = os.path.join(os.path.dirname(__file__), "modules", "uploads")
        uploaded = []
        if os.path.isdir(upload_dir):
            for fname in sorted(os.listdir(upload_dir)):
                if fname.endswith('.py'):
                    uploaded.append({
                        'id': f'upload_{fname}',
                        'name': fname,
                        'desc': f'Uploaded: {fname}',
                        'icon': 'page',
                        'hardware': [],
                        'file': fname,
                        'is_upload': True,
                    })
        result = {
            'ok': True, 'cmd': cmd,
            'modules': modules,
            'uploads': uploaded,
            'running': state.module_runner.running_module,
        }

    elif cmd == 'get_info':
        result = {'ok': True, 'cmd': cmd}
        result.update(state.get_status())

    else:
        result['error'] = f'Unknown command: {cmd}'

    return result


async def ws_handler(websocket, path=None):
    state.ws_clients.add(websocket)
    client_id = id(websocket)
    print(f"[WS] Client connected: {client_id} (total: {len(state.ws_clients)})")

    try:
        status = state.get_status()
        await websocket.send(json.dumps({'type': 'status', 'data': status}))
    except Exception:
        pass

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                result = process_command(data)
                await websocket.send(json.dumps({'type': 'response', 'data': result}))
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    'type': 'response', 'data': {'ok': False, 'error': 'Invalid JSON'}
                }))
            except Exception as e:
                await websocket.send(json.dumps({
                    'type': 'response', 'data': {'ok': False, 'error': str(e)}
                }))
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        print(f"[WS] Handler error: {e}")
    finally:
        state.ws_clients.discard(websocket)
        print(f"[WS] Client disconnected: {client_id} (total: {len(state.ws_clients)})")


async def status_broadcast():
    while state.running:
        if state.ws_clients:
            try:
                status = state.get_status()
                msg = json.dumps({'type': 'status', 'data': status})
                disconnected = set()
                for ws in state.ws_clients:
                    try:
                        await ws.send(msg)
                    except websockets.exceptions.ConnectionClosed:
                        disconnected.add(ws)
                    except Exception:
                        disconnected.add(ws)
                state.ws_clients -= disconnected
            except Exception as e:
                print(f"[WS] Broadcast error: {e}")
        await asyncio.sleep(1.5)


def start_flask_thread():
    from Server.app import create_app

    app = create_app(state)

    def run_flask():
        app.run(
            host="0.0.0.0",
            port=FLASK_PORT,
            threaded=True,
            debug=False,
            use_reloader=False,
        )

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"[WebServer] Flask server started on port {FLASK_PORT}")
    return flask_thread


def main():
    global state

    print("=" * 55)
    print("  PiCar Pro Server (Flask + WebSocket)")
    print("  Matching original architecture: Client/Server separation")
    print("=" * 55)

    if not HAS_WEBSOCKETS:
        print("[WebServer] ERROR: websockets library not installed!")
        print("[WebServer] Install with: pip3 install websockets")
        sys.exit(1)

    print("[WebServer] Initializing hardware...")
    state.motors = MotorController()
    state.servos = ServoController()
    state.leds = LEDController()
    state.ultrasonic = UltrasonicSensor()
    state.switches = SwitchController()
    state.oled = OLEDDisplay()
    state.buzzer = BuzzerController()

    try:
        state.mpu6050 = MPU6050Controller()
    except Exception as e:
        print(f"[WebServer] MPU6050 not available: {e}")

    state.autonomous = AutonomousController(state.motors, state.servos, state.ultrasonic)

    try:
        from Server.functions.voice_command import VoiceCommandController
        state.voice = VoiceCommandController(state.servos, state.motors)
    except Exception:
        pass

    saved_cal = load_servo_cal()
    for i, angle in enumerate(saved_cal):
        if 0 <= i < SERVO_COUNT:
            state.servos.set_init_angle(i, angle)

    try:
        state.servos.move_init()
    except Exception as e:
        print(f"[WebServer] Warning: servo init failed: {e}")

    if state.oled:
        ip = get_ip_address()
        state.oled.set_lines([
            f"{ip}:{FLASK_PORT}",
            "Starting...",
            "",
            "",
        ])

    oled_thread = threading.Thread(target=oled_update_loop, daemon=True)
    oled_thread.start()

    flask_thread = start_flask_thread()

    def signal_handler(sig, frame):
        print(f"\n[WebServer] Signal {sig} received, shutting down...")
        state.shutdown_hardware()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"[WebServer] Starting WebSocket server on port {WEBSOCKET_PORT}...")

    async def run_server():
        # Create WebSocket server
        async with websockets.serve(ws_handler, "0.0.0.0", WEBSOCKET_PORT):
            print(f"[WebServer] WebSocket server listening on ws://0.0.0.0:{WEBSOCKET_PORT}")
            print(f"[WebServer] Web interface: http://{get_ip_address()}:{FLASK_PORT}")
            print(f"[WebServer] Ready for connections!")
            await status_broadcast()

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown_hardware()


if __name__ == "__main__":
    main()
