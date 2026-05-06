#!/usr/bin/env python3
"""PiCar Pro WebServer — Flask (5000) + WebSocket (8888).

Optimized version with:
- Optional ultrasonic sensor (ULTRASONIC_ENABLED flag)
- Optional line tracker (LINE_TRACKER_ENABLED flag)
- Robust MPU6050 initialization
- Fixed buzzer driver (RPi.GPIO + gpiozero fallback)
- Clean voice commands (only existing servos)
"""

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
    print("[WebServer] websockets not installed!")

from Server.config import (
    FLASK_PORT, WEBSOCKET_PORT, DEFAULT_SPEED,
    SERVO_COUNT, SERVO_INIT_ANGLE, CRANE_ENABLED,
    SERVO_STEERING, SERVO_CLAW_ARM, SERVO_CLAW_GRIP,
    CLAW_ARM_UP, CLAW_ARM_DOWN, CLAW_GRIP_OPEN, CLAW_GRIP_CLOSED,
    SWITCH_PINS,
    ULTRASONIC_ENABLED, LINE_TRACKER_ENABLED,
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

    def __init__(self):
        self._thread = None
        self._current_module = None
        self._lock = threading.Lock()
        self._last_command = "Ready"
        self._stop_flag = threading.Event()

    def _get_hw_dict(self):
        """Build hw dict from shared state for injection into modules."""
        return {
            'motors':     state.motors,
            'servos':     state.servos,
            'leds':       state.leds,
            'switches':   state.switches,
            'buzzer':     state.buzzer,
            'oled':       state.oled,
            'mpu6050':    state.mpu6050,
            'ultrasonic':  state.ultrasonic,
        }

    def start(self, module_id):
        with self._lock:
            self.stop()
            path = get_module_path(module_id)
            if path is None:
                return False, f"Module '{module_id}' not found"
            if not os.path.isfile(path):
                return False, f"File not found: {path}"
            try:
                self._stop_flag.clear()
                self._current_module = module_id
                self._last_command = f"Run: {module_id}"
                self._thread = threading.Thread(
                    target=self._run_injected, args=(path,), daemon=True
                )
                self._thread.start()
                return True, f"Started: {module_id}"
            except Exception as e:
                return False, str(e)

    def start_upload(self, filepath):
        with self._lock:
            self.stop()
            if not os.path.isfile(filepath):
                return False, f"File not found: {filepath}"
            try:
                self._stop_flag.clear()
                name = os.path.basename(filepath)
                self._current_module = name
                self._last_command = f"Run: {name}"
                self._thread = threading.Thread(
                    target=self._run_injected, args=(filepath,), daemon=True
                )
                self._thread.start()
                return True, f"Started: {name}"
            except Exception as e:
                return False, str(e)

    def _run_injected(self, path):
        """Load module script and call main(hw=...) with injected state."""
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("module_script", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'main'):
                mod.main(hw=self._get_hw_dict())
            else:
                print(f"[Module] {path} has no main() function")
        except Exception as e:
            print(f"[Module] Error running {path}: {e}")
        finally:
            with self._lock:
                self._current_module = None
                self._last_command = "Ready"

    def stop(self):
        with self._lock:
            self._stop_flag.set()
            if self._thread is not None and self._thread.is_alive():
                # Modules using hw injection should check stop_flag
                # or be finite (not infinite loops)
                self._thread.join(timeout=3)
            self._thread = None
            self._current_module = None
            self._last_command = "Stopped"

    def set_command(self, cmd):
        with self._lock:
            self._last_command = cmd

    @property
    def running_module(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._current_module
            if self._thread is not None:
                self._thread = None
                self._current_module = None
            return None

    @property
    def last_command(self):
        with self._lock:
            return self._last_command


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
        print(f"[ServoCal] Save failed: {e}")


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
            "running_module": self.module_runner.running_module,
            "speed": self.speed,
            "crane_enabled": CRANE_ENABLED,
            "ultrasonic_enabled": ULTRASONIC_ENABLED,
            "line_tracker_enabled": LINE_TRACKER_ENABLED,
            "hw": {
                "motors":     self.motors._initialized if self.motors else False,
                "servos":     self.servos._pwm_initialized if self.servos else False,
                "leds":       self.leds._initialized if self.leds else False,
                "buzzer":     self.buzzer._initialized if self.buzzer else False,
                "switches":   self.switches._initialized if self.switches else False,
                "ultrasonic": ultra_ok,
                "mpu6050":    mpu_ok,
                "oled":       self.oled._initialized if self.oled else False,
                "camera":     self.camera is not None,
                "crane":      CRANE_ENABLED,
            },
        }

    def shutdown_hardware(self):
        self.running = False
        print("[WebServer] Shutting down...")
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
        for hw in (self.servos, self.leds, self.switches, self.ultrasonic,
                   self.buzzer, self.oled, self.mpu6050):
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
            line1 = f"{ip}:{port}"
            line2 = f"CPU:{info['cpu_temp']}C {info['cpu_usage']}%"
            line3 = f"RAM:{ram['used_mb']}/{ram['total_mb']}M {ram['percent']}%"
            if state.oled:
                state.oled.set_lines([line1, line2, line3])
        except Exception:
            pass
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
            state.motors.stop()
        elif direction == 'right':
            state.motors.stop()
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
            result = {'ok': True, 'cmd': cmd, 'mode': mode}
        else:
            result['error'] = f'Invalid mode'

    elif cmd == 'buzzer':
        melody = params.get('melody', 'beep')
        melody_map = {'beep': 'beep', 'alarm': 'alarm', 'birthday': 'happy_birthday'}
        melody_key = melody_map.get(melody)
        if melody_key:
            state.buzzer.play_melody(melody_key)
            result = {'ok': True, 'cmd': cmd, 'melody': melody}
        else:
            result['error'] = 'Unknown melody'

    elif cmd == 'buzzer_stop':
        state.buzzer.stop()
        result = {'ok': True, 'cmd': cmd}

    elif cmd == 'claw':
        if not CRANE_ENABLED:
            result['error'] = 'Crane not enabled'
        else:
            action = params.get('action', '')
            if action == 'arm_up':
                state.servos.set_angle(SERVO_CLAW_ARM, CLAW_ARM_UP)
                state.module_runner.set_command("Claw: Arm Up")
                result = {'ok': True, 'cmd': cmd, 'action': action}
            elif action == 'arm_down':
                state.servos.set_angle(SERVO_CLAW_ARM, CLAW_ARM_DOWN)
                state.module_runner.set_command("Claw: Arm Down")
                result = {'ok': True, 'cmd': cmd, 'action': action}
            elif action == 'grip_open':
                state.servos.set_angle(SERVO_CLAW_GRIP, CLAW_GRIP_OPEN)
                state.module_runner.set_command("Claw: Grip Open")
                result = {'ok': True, 'cmd': cmd, 'action': action}
            elif action == 'grip_close':
                state.servos.set_angle(SERVO_CLAW_GRIP, CLAW_GRIP_CLOSED)
                state.module_runner.set_command("Claw: Grip Close")
                result = {'ok': True, 'cmd': cmd, 'action': action}
            else:
                result['error'] = f'Unknown claw action: {action}'

    elif cmd == 'switch':
        switch_id = int(params.get('id', 0))
        switch_state = params.get('state', False)
        max_switches = len(SWITCH_PINS) if state.switches._initialized else 0
        if 0 <= switch_id < max_switches:
            if switch_state:
                state.switches.on(switch_id)
            else:
                state.switches.off(switch_id)
            result = {'ok': True, 'cmd': cmd, 'id': switch_id, 'state': switch_state}
        else:
            result['error'] = f'Switch id must be 0-{max_switches - 1}'

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
            result = {'ok': True, 'cmd': cmd, 'mode': mode}
        else:
            result['error'] = 'Unknown mode'

    elif cmd == 'auto':
        func = params.get('func', 'stop')
        valid_funcs = ('radarScan', 'automatic', 'trackLine', 'keepDistance', 'stop')
        if func in valid_funcs:
            if func == 'stop':
                state.autonomous.stop()
                result = {'ok': True, 'cmd': cmd, 'func': func}
            else:
                ok, msg = state.autonomous.start(func)
                result = {'ok': ok, 'cmd': cmd, 'func': func, 'message': msg}
        else:
            result['error'] = 'Unknown function'

    elif cmd == 'module_start':
        module_id = params.get('id', '')
        upload_dir = os.path.join(os.path.dirname(__file__), "modules", "uploads")
        if module_id.startswith('upload_'):
            fpath = os.path.join(upload_dir, module_id[len('upload_'):])
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
                        'id': f'upload_{fname}', 'name': fname,
                        'desc': f'Uploaded: {fname}', 'icon': 'page',
                        'hardware': [], 'file': fname, 'is_upload': True,
                    })
        result = {
            'ok': True, 'cmd': cmd,
            'modules': modules, 'uploads': uploaded,
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
                await websocket.send(json.dumps(
                    {'type': 'response', 'data': {'ok': False, 'error': 'Invalid JSON'}}
                ))
            except Exception as e:
                await websocket.send(json.dumps(
                    {'type': 'response', 'data': {'ok': False, 'error': str(e)}}
                ))
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception:
        pass
    finally:
        state.ws_clients.discard(websocket)


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
                    except Exception:
                        disconnected.add(ws)
                state.ws_clients -= disconnected
            except Exception:
                pass
        await asyncio.sleep(1.5)


def start_flask_thread():
    from Server.app import create_app
    app = create_app(state)

    def run_flask():
        app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True,
                debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"[WebServer] Flask on port {FLASK_PORT}")
    return flask_thread


def main():
    global state

    print("=" * 55)
    print("  PiCar Pro Server v1 (Flask + WebSocket)")
    print("=" * 55)

    if not HAS_WEBSOCKETS:
        print("[WebServer] ERROR: websockets not installed!")
        sys.exit(1)

    print("[WebServer] Initializing hardware...")

    # Always available
    state.motors = MotorController()
    state.servos = ServoController()
    state.leds = LEDController()
    state.switches = SwitchController()
    state.oled = OLEDDisplay()
    state.buzzer = BuzzerController()

    # MPU6050 — may not be connected, handles gracefully
    try:
        state.mpu6050 = MPU6050Controller()
    except Exception as e:
        print(f"[WebServer] MPU6050 error: {e}")

    # Ultrasonic — optional, controlled by ULTRASONIC_ENABLED flag
    state.ultrasonic = UltrasonicSensor()

    # Autonomous controller — adapts to available hardware
    state.autonomous = AutonomousController(
        state.motors, state.servos, state.ultrasonic
    )

    # Voice — optional, disabled if Sherpa-NCNN not found
    try:
        from Server.functions.voice_command import VoiceCommandController
        state.voice = VoiceCommandController(state.servos, state.motors)
    except Exception:
        pass

    # Servo calibration
    saved_cal = load_servo_cal()
    for i, angle in enumerate(saved_cal):
        if 0 <= i < SERVO_COUNT:
            state.servos.set_init_angle(i, angle)

    try:
        state.servos.move_init()
    except Exception as e:
        print(f"[WebServer] Servo init warning: {e}")

    # OLED startup message
    if state.oled:
        ip = get_ip_address()
        state.oled.set_lines([f"{ip}:{FLASK_PORT}", "Starting...", "", ""])

    oled_thread = threading.Thread(target=oled_update_loop, daemon=True)
    oled_thread.start()

    flask_thread = start_flask_thread()

    def signal_handler(sig, frame):
        print(f"\n[WebServer] Signal {sig}, shutting down...")
        state.shutdown_hardware()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Print hardware status summary
    print("-" * 55)
    print(f"  Ultrasonic: {'ON' if state.ultrasonic._initialized else 'OFF'}")
    print(f"  MPU6050:    {'ON' if state.mpu6050.initialized else 'OFF (retrying)'}")
    print(f"  Buzzer:     {'ON' if state.buzzer._initialized else 'OFF'}")
    print(f"  LineTrack:  {'ON' if LINE_TRACKER_ENABLED else 'OFF'}")
    print(f"  OLED:       {'ON' if state.oled._initialized else 'OFF'}")
    print(f"  Crane:      {'ON' if CRANE_ENABLED else 'OFF'}")
    print("-" * 55)

    print(f"[WebServer] WebSocket on port {WEBSOCKET_PORT}...")

    async def run_server():
        async with websockets.serve(ws_handler, "0.0.0.0", WEBSOCKET_PORT):
            print(f"[WebServer] Ready! http://{get_ip_address()}:{FLASK_PORT}")
            await status_broadcast()

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown_hardware()


if __name__ == "__main__":
    main()
