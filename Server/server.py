"""
PiCar Pro Server — Flask + SSE (Complete Rewrite)

Architecture:
- Flask ONLY for HTTP serving (no websockets, no React)
- SSE (Server-Sent Events) for real-time status updates to browser
- HTTP POST for all commands (movement, servos, LEDs, etc.)
- No emulator mode — hardware only
- Templates from Server/templates/, static from Server/static/
- Upload support for custom Python scripts
- Module list from Server/modules/ directory
- Servo calibration persisted to Server/servo_cal.json
- Camera MJPEG streaming
- OLED display updated every 2 seconds showing IP, port, CPU, battery, module

Runs on Raspberry Pi 3B+ (1GB RAM) — kept LIGHTWEIGHT.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (
    Flask, Response, jsonify, render_template, request,
    send_from_directory, stream_with_context,
)

from Server.config import (
    FLASK_PORT, DEFAULT_SPEED,
    BATTERY_FULL_VOLTAGE, BATTERY_WARNING_VOLTAGE, BATTERY_VOLTAGE_RATIO,
    ADS7830_ADDR, ADS7830_CHANNEL, I2C_BUS,
    SERVO_COUNT, SERVO_INIT_ANGLE,
)
from Server.hardware.motors import MotorController
from Server.hardware.servos import ServoController
from Server.hardware.leds_ws2812 import LEDController
from Server.hardware.ultrasonic import UltrasonicSensor
from Server.hardware.switch import SwitchController
from Server.hardware.oled import OLEDDisplay
from Server.hardware.buzzer import BuzzerController
from Server.camera.camera_opencv import (
    Camera, CV_MODE_NONE, CV_MODE_FIND_COLOR, CV_MODE_FIND_LINE, CV_MODE_WATCHDOG,
)
from Server.functions.autonomous import AutonomousController
from Server.utils.system_info import SystemInfo
from Server.modules import get_module_list, get_module_by_id, get_module_path


# ═════════════════════════════════════════════════════════════════════════════
#  Voltage Monitor — reads battery via ADS7830 ADC
# ═════════════════════════════════════════════════════════════════════════════

class VoltageMonitor:
    """Battery voltage monitor using ADS7830 ADC over I2C."""

    def __init__(self):
        self._adc = None
        self._voltage = 0.0
        self._percentage = 0
        self._low_callback = None
        self._low_triggered = False

        try:
            import busio
            from adafruit_ads7830 import ADS7830 as ADS7830Chip

            i2c = busio.I2C(3, 2)
            self._adc = ADS7830Chip(i2c, address=ADS7830_ADDR)
            print(f"[Voltage] ADS7830 initialized at 0x{ADS7830_ADDR:02X}")
        except Exception:
            try:
                # Fallback: try smbua2 direct read
                import smbus2
                self._bus = smbus2.SMBus(I2C_BUS)
                self._adc = "smbus"
                print(f"[Voltage] Using smbus2 fallback for ADS7830")
            except Exception as e:
                print(f"[Voltage] Failed to initialize: {e}")

    def read_raw(self):
        """Read raw ADC value from the configured channel."""
        if self._adc is None:
            return 0
        try:
            if isinstance(self._adc, str) and self._adc == "smbus":
                # smbus2 direct read: single-ended on channel ADS7830_CHANNEL
                cmd = 0x80 | (ADS7830_CHANNEL << 4) | 0x04
                raw = self._bus.read_i2c_block_data(ADS7830_ADDR, cmd, 1)
                return raw[0]
            else:
                # adafruit_ads7830
                return self._adc.read_channel(ADS7830_CHANNEL)
        except Exception:
            return 0

    def get_voltage(self):
        """Get current battery voltage."""
        raw = self.read_raw()
        if raw <= 0:
            return self._voltage

        # ADC is 8-bit (0-255), reference 3.3V, voltage divider ratio
        adc_voltage = (raw / 255.0) * 3.3
        battery_voltage = round(adc_voltage / BATTERY_VOLTAGE_RATIO, 2)

        self._voltage = battery_voltage

        # Low voltage alarm
        if (battery_voltage < BATTERY_WARNING_VOLTAGE
                and not self._low_triggered
                and self._low_callback):
            self._low_triggered = True
            self._low_callback(battery_voltage)
        elif battery_voltage >= BATTERY_WARNING_VOLTAGE + 0.3:
            self._low_triggered = False

        return self._voltage

    def get_percentage(self):
        """Get battery percentage (0-100)."""
        v = self.get_voltage()
        pct = max(0, min(100, int((v - BATTERY_WARNING_VOLTAGE) /
                                   (BATTERY_FULL_VOLTAGE - BATTERY_WARNING_VOLTAGE) * 100)))
        self._percentage = pct
        return self._percentage

    def set_low_voltage_callback(self, callback):
        """Set callback for low voltage alarm."""
        self._low_callback = callback

    def shutdown(self):
        """Clean up."""
        if hasattr(self, '_bus') and self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
        print("[Voltage] Shutdown complete")


# ═════════════════════════════════════════════════════════════════════════════
#  Module Runner — manages running example scripts via subprocess
# ═════════════════════════════════════════════════════════════════════════════

class ModuleRunner:
    """Run and manage example module scripts in subprocesses."""

    def __init__(self):
        self._process = None
        self._current_module = None
        self._lock = threading.Lock()

    def start(self, module_id):
        """Start a module by ID. Returns (success, message)."""
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
                return True, f"Started: {module_id}"
            except Exception as e:
                return False, str(e)

    def start_upload(self, filepath):
        """Start an uploaded script by path. Returns (success, message)."""
        with self._lock:
            self.stop()

            if not os.path.isfile(filepath):
                return False, f"File not found: {filepath}"

            try:
                self._process = subprocess.Popen(
                    [sys.executable, filepath],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__)))),
                )
                self._current_module = os.path.basename(filepath)
                return True, f"Started: {self._current_module}"
            except Exception as e:
                return False, str(e)

    def stop(self):
        """Stop the currently running module."""
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

    @property
    def running_module(self):
        """Return the currently running module ID, or None."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._current_module
            # Process ended — clean up
            if self._process is not None:
                self._process = None
                self._current_module = None
            return None


# ═════════════════════════════════════════════════════════════════════════════
#  Servo Calibration Persistence
# ═════════════════════════════════════════════════════════════════════════════

SERVO_CAL_FILE = os.path.join(os.path.dirname(__file__), "servo_cal.json")


def load_servo_cal():
    """Load servo calibration angles from JSON file."""
    try:
        if os.path.isfile(SERVO_CAL_FILE):
            with open(SERVO_CAL_FILE, "r") as f:
                data = json.load(f)
                return data.get("init_angles", [SERVO_INIT_ANGLE] * SERVO_COUNT)
    except Exception:
        pass
    return [SERVO_INIT_ANGLE] * SERVO_COUNT


def save_servo_cal(init_angles):
    """Save servo calibration angles to JSON file."""
    try:
        with open(SERVO_CAL_FILE, "w") as f:
            json.dump({"init_angles": init_angles}, f, indent=2)
    except Exception as e:
        print(f"[ServoCal] Failed to save: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  Helper: get the robot's own IP address
# ═════════════════════════════════════════════════════════════════════════════

def get_ip_address():
    """Get the robot's IP address for display on OLED."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


# ═════════════════════════════════════════════════════════════════════════════
#  Flask Application Factory
# ═════════════════════════════════════════════════════════════════════════════

def create_app():
    """Build and return the Flask application with all routes."""

    # ── Paths ─────────────────────────────────────────────────────────────
    server_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(server_dir)
    template_dir = os.path.join(server_dir, "templates")
    static_dir = os.path.join(server_dir, "static")
    upload_dir = os.path.join(server_dir, "uploads")
    docs_dir = os.path.join(project_dir, "docs")

    # Ensure directories exist
    os.makedirs(upload_dir, exist_ok=True)

    # ── Flask app ─────────────────────────────────────────────────────────
    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
    )

    # ── Initialize hardware ───────────────────────────────────────────────
    print("[Server] Initializing hardware...")
    motors = MotorController()
    servos = ServoController()
    leds = LEDController()
    ultrasonic = UltrasonicSensor()
    switches = SwitchController()
    oled = OLEDDisplay()
    buzzer = BuzzerController()
    voltage = VoltageMonitor()
    autonomous = AutonomousController(motors, servos, ultrasonic)

    # ── Voice command (optional) ──────────────────────────────────────────
    voice = None
    try:
        from Server.functions.voice_command import VoiceCommandController
        voice = VoiceCommandController(servos, motors)
    except Exception:
        pass

    # ── Apply saved servo calibration ─────────────────────────────────────
    saved_cal = load_servo_cal()
    for i, angle in enumerate(saved_cal):
        if 0 <= i < SERVO_COUNT:
            servos.set_init_angle(i, angle)

    # ── Move servos to home ───────────────────────────────────────────────
    servos.move_init()

    # ── Set low voltage alarm ─────────────────────────────────────────────
    def on_low_voltage(v):
        buzzer.play_alarm()
        switches.all_on()
        oled.set_lines(["LOW BATTERY!", f"V: {v}V", "Charge now!", ""])
    voltage.set_low_voltage_callback(on_low_voltage)

    # ── Shared state ──────────────────────────────────────────────────────
    speed = DEFAULT_SPEED
    module_runner = ModuleRunner()
    camera = None  # Lazy init
    running = True

    def init_camera():
        """Initialize camera (lazy — only when first needed)."""
        nonlocal camera
        if camera is None:
            camera = Camera()

    # ── OLED update thread ────────────────────────────────────────────────
    def oled_update_loop():
        """Update OLED every 2 seconds with IP, CPU, battery, module info."""
        ip = get_ip_address()
        port = FLASK_PORT

        while running:
            try:
                v = voltage.get_voltage()
                pct = voltage.get_percentage()
                info = SystemInfo.get_all()
                mod = module_runner.running_module

                line1 = f"{ip}:{port}"
                line2 = f"CPU:{info['cpu_temp']}C {info['cpu_usage']}%"
                line3 = f"Bat:{v}V {pct}%"
                line4 = mod if mod else "Ready"

                oled.set_lines([line1, line2, line3, line4])
            except Exception as e:
                print(f"[OLED] Update error: {e}")

            time.sleep(2)

    oled_thread = threading.Thread(target=oled_update_loop, daemon=True)
    oled_thread.start()

    # ═════════════════════════════════════════════════════════════════════
    #  Status helper — used by both /api/status and SSE stream
    # ═════════════════════════════════════════════════════════════════════

    def get_status():
        """Gather current robot status as dict."""
        info = SystemInfo.get_all()
        return {
            "speed": speed,
            "voltage": voltage.get_voltage(),
            "battery": voltage.get_percentage(),
            "cpu_temp": info["cpu_temp"],
            "cpu_usage": info["cpu_usage"],
            "ram": info["ram"],
            "distance": ultrasonic.get_last_distance(),
            "cv_mode": camera.cv_thread.cv_mode if camera else "none",
            "auto_active": autonomous.is_active(),
            "running_module": module_runner.running_module,
        }

    # ═════════════════════════════════════════════════════════════════════
    #  CORS helper
    # ═════════════════════════════════════════════════════════════════════

    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    # ═════════════════════════════════════════════════════════════════════
    #  PAGE ROUTES
    # ═════════════════════════════════════════════════════════════════════

    @app.route("/")
    def page_index():
        """Main control page."""
        return render_template("index.html")

    @app.route("/about")
    def page_about():
        """Pinout + documentation page."""
        return render_template("about.html")

    # ═════════════════════════════════════════════════════════════════════
    #  CAMERA MJPEG STREAM
    # ═════════════════════════════════════════════════════════════════════

    @app.route("/video_feed")
    def video_feed():
        """MJPEG stream endpoint — the browser's <img> points here."""
        init_camera()

        def generate():
            while running:
                frame = Camera.get_frame()
                if frame:
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                else:
                    time.sleep(0.05)

        return Response(
            generate(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    # ═════════════════════════════════════════════════════════════════════
    #  COMMAND ROUTES (all POST)
    # ═════════════════════════════════════════════════════════════════════

    # ── Movement ──────────────────────────────────────────────────────────

    @app.route("/cmd/move", methods=["POST"])
    def cmd_move():
        """Move robot. Body: {dir: "forward|backward|left|right|stop"}"""
        data = request.get_json(silent=True) or {}
        direction = data.get("dir", "stop")

        if direction == "forward":
            motors.move(speed, "forward", "no", 0.5)
        elif direction == "backward":
            motors.move(speed, "backward", "no", 0.5)
        elif direction == "left":
            motors.move(speed, "forward", "left", 0.4)
        elif direction == "right":
            motors.move(speed, "forward", "right", 0.4)
        elif direction == "stop":
            motors.stop()
        else:
            return jsonify({"ok": False, "error": f"Unknown direction: {direction}"}), 400

        return jsonify({"ok": True, "dir": direction})

    # ── Speed ─────────────────────────────────────────────────────────────

    @app.route("/cmd/speed", methods=["POST"])
    def cmd_speed():
        """Set motor speed. Body: {value: 0-100}"""
        data = request.get_json(silent=True) or {}
        nonlocal speed
        try:
            speed = max(0, min(100, int(data.get("value", DEFAULT_SPEED))))
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid speed value"}), 400
        return jsonify({"ok": True, "speed": speed})

    # ── Servo ─────────────────────────────────────────────────────────────

    @app.route("/cmd/servo", methods=["POST"])
    def cmd_servo():
        """Set servo angle. Body: {id: 0-7, angle: 0-180}"""
        data = request.get_json(silent=True) or {}
        servo_id = data.get("id", 0)
        angle = data.get("angle", 90)

        try:
            servo_id = int(servo_id)
            angle = int(angle)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid servo id or angle"}), 400

        if not (0 <= servo_id < SERVO_COUNT):
            return jsonify({"ok": False, "error": f"Servo id must be 0-{SERVO_COUNT-1}"}), 400

        angle = max(0, min(180, angle))
        servos.set_angle(servo_id, angle)
        return jsonify({"ok": True, "id": servo_id, "angle": angle})

    # ── Servo Calibration ─────────────────────────────────────────────────

    @app.route("/cmd/servo_calibrate", methods=["POST"])
    def cmd_servo_calibrate():
        """Set servo init angle (calibration). Body: {id: 0-7, angle: 0-180}"""
        data = request.get_json(silent=True) or {}
        servo_id = data.get("id", 0)
        angle = data.get("angle", 90)

        try:
            servo_id = int(servo_id)
            angle = int(angle)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid servo id or angle"}), 400

        if not (0 <= servo_id < SERVO_COUNT):
            return jsonify({"ok": False, "error": f"Servo id must be 0-{SERVO_COUNT-1}"}), 400

        angle = max(0, min(180, angle))
        servos.set_init_angle(servo_id, angle)

        # Persist to JSON
        cal = load_servo_cal()
        cal[servo_id] = angle
        save_servo_cal(cal)

        return jsonify({"ok": True, "id": servo_id, "init_angle": angle})

    # ── Servo Home ────────────────────────────────────────────────────────

    @app.route("/cmd/servo_home", methods=["POST"])
    def cmd_servo_home():
        """Move all servos to their init (calibrated) positions."""
        servos.move_init()
        return jsonify({"ok": True, "message": "All servos moved to home"})

    # ── LED ───────────────────────────────────────────────────────────────

    @app.route("/cmd/led", methods=["POST"])
    def cmd_led():
        """Set LED mode. Body: {mode: "off|solid|breath|flow|rainbow|police", color: [r,g,b]}"""
        data = request.get_json(silent=True) or {}
        mode = data.get("mode", "solid")
        color = data.get("color", [255, 0, 0])

        valid_modes = ("off", "solid", "breath", "flow", "rainbow", "police", "colorWipe")
        if mode not in valid_modes:
            return jsonify({"ok": False, "error": f"Invalid mode. Use: {', '.join(valid_modes)}"}), 400

        # Ensure color is a tuple of 3 ints
        try:
            color = tuple(max(0, min(255, int(c))) for c in color[:3])
        except (ValueError, TypeError):
            color = (255, 0, 0)

        leds.set_mode(mode, color)
        return jsonify({"ok": True, "mode": mode, "color": list(color)})

    # ── Buzzer ────────────────────────────────────────────────────────────

    @app.route("/cmd/buzzer", methods=["POST"])
    def cmd_buzzer():
        """Play buzzer melody. Body: {melody: "beep|alarm|birthday"}"""
        data = request.get_json(silent=True) or {}
        melody = data.get("melody", "beep")

        melody_map = {
            "beep": "beep",
            "alarm": "alarm",
            "birthday": "happy_birthday",
        }

        melody_key = melody_map.get(melody)
        if melody_key is None:
            return jsonify({"ok": False, "error":
                            f"Unknown melody. Use: {', '.join(melody_map.keys())}"}), 400

        buzzer.play_melody(melody_key)
        return jsonify({"ok": True, "melody": melody})

    # ── Switch ────────────────────────────────────────────────────────────

    @app.route("/cmd/switch", methods=["POST"])
    def cmd_switch():
        """Toggle a switch/LED. Body: {id: 0-2, state: true|false}"""
        data = request.get_json(silent=True) or {}
        switch_id = data.get("id", 0)
        state = data.get("state", False)

        try:
            switch_id = int(switch_id)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid switch id"}), 400

        if not (0 <= switch_id < 3):
            return jsonify({"ok": False, "error": "Switch id must be 0-2"}), 400

        if state:
            switches.on(switch_id)
        else:
            switches.off(switch_id)

        return jsonify({"ok": True, "id": switch_id, "state": state})

    # ── CV Mode ───────────────────────────────────────────────────────────

    @app.route("/cmd/cv_mode", methods=["POST"])
    def cmd_cv_mode():
        """Set computer vision mode. Body: {mode: "none|findColor|findlineCV|watchDog"}"""
        data = request.get_json(silent=True) or {}
        mode = data.get("mode", "none")

        mode_map = {
            "none": CV_MODE_NONE,
            "findColor": CV_MODE_FIND_COLOR,
            "findlineCV": CV_MODE_FIND_LINE,
            "watchDog": CV_MODE_WATCHDOG,
        }

        cv_mode = mode_map.get(mode)
        if cv_mode is None:
            return jsonify({"ok": False, "error":
                            f"Unknown mode. Use: {', '.join(mode_map.keys())}"}), 400

        init_camera()
        camera.set_cv_mode(cv_mode)
        return jsonify({"ok": True, "mode": mode})

    # ── Autonomous Functions ───────────────────────────────────────────────

    @app.route("/cmd/auto", methods=["POST"])
    def cmd_auto():
        """Start/stop autonomous function. Body: {func: "radarScan|automatic|trackLine|keepDistance|stop"}"""
        data = request.get_json(silent=True) or {}
        func = data.get("func", "stop")

        valid_funcs = ("radarScan", "automatic", "trackLine", "keepDistance", "stop")
        if func not in valid_funcs:
            return jsonify({"ok": False, "error":
                            f"Unknown function. Use: {', '.join(valid_funcs)}"}), 400

        if func == "stop":
            autonomous.stop()
        else:
            autonomous.start(func)

        return jsonify({"ok": True, "func": func})

    # ═════════════════════════════════════════════════════════════════════
    #  MODULE ENDPOINTS
    # ═════════════════════════════════════════════════════════════════════

    @app.route("/api/modules", methods=["GET"])
    def api_modules():
        """List all available modules from Server/modules/."""
        lang = request.args.get("lang", "en")
        # Built-in modules
        modules = get_module_list(lang)

        # Also scan uploads/ for user-uploaded scripts
        uploaded = []
        if os.path.isdir(upload_dir):
            for fname in sorted(os.listdir(upload_dir)):
                if fname.endswith(".py"):
                    uploaded.append({
                        "id": f"upload_{fname}",
                        "name": fname,
                        "desc": f"Uploaded script: {fname}",
                        "icon": "📄",
                        "hardware": [],
                        "file": fname,
                        "is_upload": True,
                    })

        return jsonify({
            "modules": modules,
            "uploads": uploaded,
            "running": module_runner.running_module,
        })

    @app.route("/api/modules/start", methods=["POST"])
    def api_module_start():
        """Start a module. Body: {id: "module_id"}"""
        data = request.get_json(silent=True) or {}
        module_id = data.get("id", "")

        # Check if it's an uploaded script
        if module_id.startswith("upload_"):
            fname = module_id[len("upload_"):]
            fpath = os.path.join(upload_dir, fname)
            ok, msg = module_runner.start_upload(fpath)
        else:
            ok, msg = module_runner.start(module_id)

        return jsonify({"ok": ok, "message": msg, "id": module_id})

    @app.route("/api/modules/stop", methods=["POST"])
    def api_module_stop():
        """Stop the currently running module."""
        module_runner.stop()
        return jsonify({"ok": True, "message": "Module stopped"})

    @app.route("/api/modules/upload", methods=["POST"])
    def api_module_upload():
        """Upload a .py script to Server/uploads/."""
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file provided"}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "Empty filename"}), 400

        if not f.filename.endswith(".py"):
            return jsonify({"ok": False, "error": "Only .py files are allowed"}), 400

        # Sanitize filename
        safe_name = os.path.basename(f.filename)
        save_path = os.path.join(upload_dir, safe_name)

        try:
            f.save(save_path)
            return jsonify({"ok": True, "filename": safe_name})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═════════════════════════════════════════════════════════════════════
    #  STATUS ENDPOINTS
    # ═════════════════════════════════════════════════════════════════════

    @app.route("/api/status", methods=["GET"])
    def api_status():
        """JSON status snapshot (for simple polling)."""
        return jsonify(get_status())

    @app.route("/api/status/stream", methods=["GET"])
    def api_status_stream():
        """SSE endpoint — pushes robot status every 1 second."""

        def event_stream():
            while running:
                data = json.dumps(get_status())
                yield f"data: {data}\n\n"
                time.sleep(1)

        return Response(
            stream_with_context(event_stream()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    # ═════════════════════════════════════════════════════════════════════
    #  DOCUMENTATION ENDPOINTS
    # ═════════════════════════════════════════════════════════════════════

    @app.route("/docs/components/<path:name>")
    def docs_component(name):
        """Serve a component datasheet JSON."""
        return send_from_directory(
            os.path.join(docs_dir, "components"), name
        )

    @app.route("/docs/pinout.json")
    def docs_pinout():
        """Serve the GPIO pinout JSON."""
        return send_from_directory(docs_dir, "pinout.json")

    @app.route("/docs/index.json")
    def docs_index():
        """Serve the documentation index JSON."""
        return send_from_directory(docs_dir, "index.json")

    # ═════════════════════════════════════════════════════════════════════
    #  GRACEFUL SHUTDOWN
    # ═════════════════════════════════════════════════════════════════════

    def shutdown_hardware():
        """Clean up all hardware on exit."""
        nonlocal running
        running = False
        print("[Server] Shutting down hardware...")

        module_runner.stop()
        motors.stop()
        autonomous.shutdown()

        if voice is not None:
            try:
                voice.shutdown()
            except Exception:
                pass

        if camera is not None:
            try:
                camera.shutdown()
            except Exception:
                pass

        try:
            servos.shutdown()
        except Exception:
            pass
        try:
            leds.shutdown()
        except Exception:
            pass
        try:
            switches.shutdown()
        except Exception:
            pass
        try:
            ultrasonic.shutdown()
        except Exception:
            pass
        try:
            voltage.shutdown()
        except Exception:
            pass
        try:
            buzzer.shutdown()
        except Exception:
            pass
        try:
            oled.shutdown()
        except Exception:
            pass

        print("[Server] Shutdown complete")

    # Register shutdown on signal
    def signal_handler(sig, frame):
        print(f"\n[Server] Signal {sig} received, shutting down...")
        shutdown_hardware()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    return app, shutdown_hardware


# ═════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 50)
    print("  PiCar Pro Server  (Flask + SSE)")
    print("=" * 50)

    app, shutdown = create_app()

    try:
        app.run(
            host="0.0.0.0",
            port=FLASK_PORT,
            threaded=True,
            use_reloader=False,
            debug=False,
        )
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
