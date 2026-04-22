"""
Main unified server for PiCar Pro.
Combines WebSocket command/control + Flask MJPEG streaming.

Optimizations over v1:
- Single server entry point (v1 had separate WebServer/GUIServer)
- Non-blocking command handling (v1 used time.sleep in asyncio loop!)
- Proper asyncio integration for WebSocket
- Graceful shutdown with signal handlers
- No global state - all state in RobotServer class
- Centralized configuration
- Battery monitoring with low voltage alarm
- Voice command support (optional)
- System info reporting
"""

import asyncio
import json
import os
import signal
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Server.config import (
    WEBSOCKET_PORT, FLASK_PORT, DEFAULT_SPEED,
)
from Server.hardware.motors import MotorController
from Server.hardware.servos import ServoController
from Server.hardware.leds_ws2812 import LEDController
from Server.hardware.ultrasonic import UltrasonicSensor
from Server.hardware.switch import SwitchController
from Server.hardware.oled import OLEDDisplay
from Server.hardware.voltage import VoltageMonitor
from Server.hardware.buzzer import BuzzerController
from Server.camera.camera_opencv import Camera, CV_MODE_NONE, CV_MODE_FIND_COLOR, CV_MODE_FIND_LINE, CV_MODE_WATCHDOG
from Server.functions.autonomous import AutonomousController
from Server.utils.system_info import SystemInfo


class RobotServer:
    """
    Unified robot control server.
    
    Manages all hardware, camera, autonomous functions, and network communication.
    WebSocket handles commands, Flask serves MJPEG camera stream.
    """

    def __init__(self):
        # Hardware controllers
        self.motors = MotorController()
        self.servos = ServoController()
        self.leds = LEDController()
        self.ultrasonic = UltrasonicSensor()
        self.switches = SwitchController()
        self.oled = OLEDDisplay()
        self.buzzer = BuzzerController()
        self.voltage = VoltageMonitor()
        self.autonomous = AutonomousController(self.motors, self.servos, self.ultrasonic)

        # Voice commands (optional)
        self.voice = None
        try:
            from Server.functions.voice_command import VoiceCommandController
            self.voice = VoiceCommandController(self.servos, self.motors)
        except Exception:
            pass

        # Camera (initialized lazily)
        self.camera = None

        # State
        self.speed = DEFAULT_SPEED
        self.mode = "PT"  # PT (Pan-Tilt) or AR (Arm)
        self._running = True
        self._ws_clients = set()

        # Set up low voltage alarm
        self.voltage.set_low_voltage_callback(self._on_low_voltage)

        # Show startup on OLED
        self.oled.show_startup()

    def _on_low_voltage(self, voltage):
        """Callback when battery voltage is critically low."""
        self.buzzer.play_alarm()
        self.switches.all_on()
        self.oled.set_lines([
            "LOW BATTERY!",
            f"V: {voltage}V",
            "Charge now!",
            "",
        ])

    def init_camera(self):
        """Initialize camera (lazy - only when needed)."""
        if self.camera is None:
            self.camera = Camera()

    def get_ip_address(self):
        """Get the robot's IP address for display."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "0.0.0.0"

    # =========================================================================
    # WebSocket Command Handlers
    # =========================================================================

    async def handle_websocket(self, websocket):
        """Handle a WebSocket client connection."""
        self._ws_clients.add(websocket)
        print(f"[Server] Client connected: {websocket.remote_address}")

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._process_command(websocket, data)
                except json.JSONDecodeError:
                    print(f"[Server] Invalid JSON: {message}")
                except Exception as e:
                    print(f"[Server] Command error: {e}")

        except Exception as e:
            print(f"[Server] WebSocket error: {e}")
        finally:
            self._ws_clients.discard(websocket)
            print(f"[Server] Client disconnected")

    async def _process_command(self, websocket, data):
        """Process a single command from a WebSocket client."""
        command = data.get("command", "")
        value = data.get("value", "")

        # ---- Robot Movement ----
        if command == "forward":
            self.motors.move(self.speed, 'forward', 'no', 0.5)
        elif command == "backward":
            self.motors.move(self.speed, 'backward', 'no', 0.5)
        elif command == "left":
            self.motors.move(self.speed, 'forward', 'left', 0.4)
        elif command == "right":
            self.motors.move(self.speed, 'forward', 'right', 0.4)
        elif command == "stop":
            self.motors.stop()

        # ---- Speed Control ----
        elif command == "speed":
            try:
                self.speed = max(0, min(100, int(value)))
            except (ValueError, TypeError):
                pass

        # ---- Servo Control ----
        elif command == "servo":
            servo_id = data.get("id", 0)
            angle = data.get("angle", 90)
            self.servos.set_angle(servo_id, angle)
        elif command == "servoMove":
            servo_id = data.get("id", 0)
            offset = data.get("offset", 0)
            self.servos.move_angle(servo_id, offset)
        elif command == "servoInit":
            self.servos.move_init()

        # ---- Switch Control ----
        elif command == "switch":
            switch_id = data.get("id", 0)
            state = data.get("state", False)
            if state:
                self.switches.on(switch_id)
            else:
                self.switches.off(switch_id)

        # ---- LED Control ----
        elif command == "led":
            mode = data.get("mode", "solid")
            color = data.get("color", [255, 0, 0])
            self.leds.set_mode(mode, tuple(color))

        # ---- CV Mode ----
        elif command == "cvMode":
            mode = value
            if self.camera is None:
                self.init_camera()
            if mode == "findColor":
                self.camera.set_cv_mode(CV_MODE_FIND_COLOR)
            elif mode == "findlineCV":
                self.camera.set_cv_mode(CV_MODE_FIND_LINE)
            elif mode == "watchDog":
                self.camera.set_cv_mode(CV_MODE_WATCHDOG)
            elif mode == "none":
                self.camera.set_cv_mode(CV_MODE_NONE)

        # ---- CV Color Range ----
        elif command == "colorRange":
            if self.camera is not None:
                self.camera.set_color_range(
                    data.get("lh", 35), data.get("ls", 43), data.get("lv", 46),
                    data.get("uh", 77), data.get("us", 255), data.get("uv", 255),
                )

        # ---- CV Line Position ----
        elif command == "linePos":
            if self.camera is not None:
                self.camera.cv_thread.line_pos_1 = data.get("pos1", 440)
                self.camera.cv_thread.line_pos_2 = data.get("pos2", 380)

        # ---- Autonomous Functions ----
        elif command == "function":
            func = value
            if func == "radarScan":
                self.autonomous.start("radarScan")
            elif func == "automatic":
                self.autonomous.start("automatic")
            elif func == "trackLine":
                self.autonomous.start("trackLine")
            elif func == "keepDistance":
                self.autonomous.start("keepDistance")
            elif func == "stop":
                self.autonomous.stop()

        # ---- Buzzer ----
        elif command == "buzzer":
            melody = value if value else "beep"
            self.buzzer.play_melody(melody)

        # ---- Voice Control ----
        elif command == "voice":
            if self.voice is not None:
                if value == "start":
                    self.voice.start()
                elif value == "stop":
                    self.voice.stop()

        # ---- Mode Selection ----
        elif command == "mode":
            self.mode = value  # "PT" or "AR"

        # ---- Robot Mode Config ----
        elif command == "configPWM":
            # Update servo init angles for calibration
            servo_id = data.get("id", 0)
            angle = data.get("angle", 90)
            self.servos.set_init_angle(servo_id, angle)

        # ---- Status Queries ----
        elif command == "status":
            status = self._get_status()
            await websocket.send(json.dumps(status))

        elif command == "radarData":
            data = self.autonomous.get_radar_data()
            await websocket.send(json.dumps({
                "type": "radarData",
                "data": data,
            }))

    def _get_status(self):
        """Get current robot status."""
        info = SystemInfo.get_all()
        return {
            "type": "status",
            "speed": self.speed,
            "mode": self.mode,
            "voltage": self.voltage.get_voltage(),
            "battery": self.voltage.get_percentage(),
            "cpu_temp": info["cpu_temp"],
            "cpu_usage": info["cpu_usage"],
            "ram": info["ram"],
            "distance": self.ultrasonic.get_last_distance(),
            "cv_mode": self.camera.cv_thread.cv_mode if self.camera else "none",
            "auto_active": self.autonomous.is_active(),
        }

    # =========================================================================
    # Flask MJPEG Server (runs in separate thread)
    # =========================================================================

    def start_flask(self):
        """Start Flask MJPEG streaming server in a background thread."""
        from flask import Flask, Response, send_from_directory

        app = Flask(__name__, static_folder=None)

        @app.route('/video_feed')
        def video_feed():
            """MJPEG stream endpoint."""
            self.init_camera()

            def generate():
                while self._running:
                    frame = self.camera.get_frame()
                    if frame:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

            return Response(generate(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')

        @app.route('/')
        def index():
            """Serve the web UI."""
            dist_path = os.path.join(os.path.dirname(__file__), 'dist')
            return send_from_directory(dist_path, 'index.html')

        @app.route('/<path:path>')
        def static_files(path):
            """Serve static files."""
            dist_path = os.path.join(os.path.dirname(__file__), 'dist')
            return send_from_directory(dist_path, path)

        @app.after_request
        def add_cors(response):
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        # Run Flask in a separate thread
        def run_flask():
            app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True, use_reloader=False)

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print(f"[Server] Flask MJPEG server on :{FLASK_PORT}")

    # =========================================================================
    # WebSocket Server (main asyncio loop)
    # =========================================================================

    async def start_websocket(self):
        """Start WebSocket server."""
        import websockets

        async def handler(websocket):
            await self.handle_websocket(websocket)

        async with websockets.serve(handler, "0.0.0.0", WEBSOCKET_PORT):
            print(f"[Server] WebSocket server on :{WEBSOCKET_PORT}")

            # Show IP on OLED
            ip = self.get_ip_address()
            self.oled.show_ip(ip)

            # Keep running until shutdown
            while self._running:
                await asyncio.sleep(1)

    # =========================================================================
    # Main Run Loop
    # =========================================================================

    def run(self):
        """Start the robot server."""
        print("=" * 50)
        print("  PiCar Pro - Optimized Server")
        print("=" * 50)

        # Initialize servos to home position
        self.servos.move_init()

        # Start Flask MJPEG server
        self.start_flask()

        # Set up signal handlers for graceful shutdown
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._signal_handler)

        try:
            loop.run_until_complete(self.start_websocket())
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
            loop.close()

    def _signal_handler(self):
        """Handle shutdown signals gracefully."""
        print("\n[Server] Shutdown signal received")
        self._running = False

    def shutdown(self):
        """Gracefully shut down all subsystems."""
        print("[Server] Shutting down...")

        self._running = False
        self.motors.stop()
        self.autonomous.shutdown()

        if self.voice is not None:
            self.voice.shutdown()

        if self.camera is not None:
            self.camera.shutdown()

        self.servos.shutdown()
        self.leds.shutdown()
        self.switches.shutdown()
        self.ultrasonic.shutdown()
        self.voltage.shutdown()
        self.buzzer.shutdown()
        self.oled.shutdown()

        print("[Server] Shutdown complete")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == '__main__':
    server = RobotServer()
    server.run()
