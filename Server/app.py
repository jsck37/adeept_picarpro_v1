#!/usr/bin/env python3
"""
PiCar Pro Flask App — serves web frontend + MJPEG camera stream

Architecture (matching original):
- Flask app on port 5000: serves templates/index.html + MJPEG stream
- This is started by WebServer.py in a background thread
- Real-time WebSocket control is handled separately by WebServer.py on port 8888
- REST API endpoints provided as fallback when WebSocket is not available
"""

import json
import os
import time

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from Server.camera.camera_opencv import Camera
from Server.config import FLASK_PORT, DEFAULT_SPEED, SERVO_COUNT, SWITCH_PINS
from Server.modules import get_module_list


def create_app(state):
    """
    Build and return the Flask application.

    Args:
        state: SharedState instance from WebServer.py (provides access to hardware, etc.)
    """

    # ── Paths ─────────────────────────────────────────────────────────────
    server_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(server_dir)
    dist_dir = os.path.join(server_dir, "dist")
    upload_dir = os.path.join(server_dir, "modules", "uploads")
    docs_dir = os.path.join(project_dir, "docs")

    # Ensure directories exist
    os.makedirs(upload_dir, exist_ok=True)

    # ── Flask app ─────────────────────────────────────────────────────────
    # Disable built-in static serving — we serve dist/ files via explicit
    # send_from_directory routes below. This is more reliable than
    # static_url_path='' which can conflict with other routes.
    app = Flask(
        __name__,
        template_folder=dist_dir,
        static_folder=None,
    )
    app.config['SECRET_KEY'] = 'picarpro_secret'

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

    @app.route("/favicon.ico")
    def favicon():
        """Return empty 204 to suppress 404 errors for favicon."""
        return "", 204

    # ── Static files from dist/ (explicit routes — no Flask static magic) ──

    @app.route("/style.css")
    def serve_css():
        """Serve the main stylesheet."""
        return send_from_directory(dist_dir, "style.css", mimetype="text/css")

    @app.route("/app.js")
    def serve_js():
        """Serve the main application script."""
        return send_from_directory(dist_dir, "app.js", mimetype="application/javascript")

    @app.route("/<path:filename>")
    def serve_dist_file(filename):
        """Catch-all: serve any other file from dist/ (images, fonts, etc.)."""
        filepath = os.path.join(dist_dir, filename)
        if os.path.isfile(filepath):
            return send_from_directory(dist_dir, filename)
        return "", 404

    # ═════════════════════════════════════════════════════════════════════
    #  CAMERA MJPEG STREAM
    # ═════════════════════════════════════════════════════════════════════

    @app.route("/video_feed")
    def video_feed():
        """MJPEG stream endpoint — the browser's <img> points here."""
        state.init_camera()

        def generate():
            while state.running:
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
    #  REST API (fallback for when WebSocket is not available)
    # ═════════════════════════════════════════════════════════════════════

    @app.route("/api/status", methods=["GET"])
    def api_status():
        """JSON status snapshot (fallback polling)."""
        return jsonify(state.get_status())

    @app.route("/api/status/stream", methods=["GET"])
    def api_status_stream():
        """SSE endpoint — pushes robot status every 1 second (fallback)."""
        def event_stream():
            while state.running:
                data = json.dumps(state.get_status())
                yield f"data: {data}\n\n"
                time.sleep(1)

        return Response(
            event_stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Movement (HTTP fallback) ──────────────────────────────────────

    @app.route("/cmd/move", methods=["POST"])
    def cmd_move():
        data = request.get_json(silent=True) or {}
        direction = data.get("dir", "stop")
        state.module_runner.set_command(f"Move: {direction}")

        if direction == "forward":
            state.motors.move(state.speed, "forward", "no", 0.5)
        elif direction == "backward":
            state.motors.move(state.speed, "backward", "no", 0.5)
        elif direction == "left":
            state.motors.move(state.speed, "forward", "left", 0.4)
        elif direction == "right":
            state.motors.move(state.speed, "forward", "right", 0.4)
        elif direction == "forward_left":
            state.motors.move(state.speed, "forward", "left", 0.3)
        elif direction == "forward_right":
            state.motors.move(state.speed, "forward", "right", 0.3)
        elif direction == "backward_left":
            state.motors.move(state.speed, "backward", "left", 0.3)
        elif direction == "backward_right":
            state.motors.move(state.speed, "backward", "right", 0.3)
        elif direction == "stop":
            state.motors.stop()
            state.module_runner.set_command("Ready")
        else:
            return jsonify({"ok": False, "error": f"Unknown: {direction}"}), 400

        return jsonify({"ok": True, "dir": direction})

    @app.route("/cmd/speed", methods=["POST"])
    def cmd_speed():
        data = request.get_json(silent=True) or {}
        try:
            state.speed = max(0, min(100, int(data.get("value", DEFAULT_SPEED))))
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Invalid speed"}), 400
        return jsonify({"ok": True, "speed": state.speed})

    @app.route("/cmd/servo", methods=["POST"])
    def cmd_servo():
        data = request.get_json(silent=True) or {}
        servo_id = int(data.get("id", 0))
        angle = int(data.get("angle", 90))
        if 0 <= servo_id < SERVO_COUNT:
            angle = max(0, min(180, angle))
            state.servos.set_angle(servo_id, angle)
            return jsonify({"ok": True, "id": servo_id, "angle": angle})
        return jsonify({"ok": False, "error": f"Servo id 0-{SERVO_COUNT-1}"}), 400

    @app.route("/cmd/servo_home", methods=["POST"])
    def cmd_servo_home():
        state.servos.move_init()
        return jsonify({"ok": True})

    @app.route("/cmd/led", methods=["POST"])
    def cmd_led():
        data = request.get_json(silent=True) or {}
        mode = data.get("mode", "off")
        color = data.get("color", [255, 0, 0])
        valid = ("off", "solid", "breath", "flow", "rainbow", "police", "colorWipe")
        if mode in valid:
            try:
                color = tuple(max(0, min(255, int(c))) for c in color[:3])
            except Exception:
                color = (255, 0, 0)
            state.leds.set_mode(mode, color)
            return jsonify({"ok": True, "mode": mode})
        return jsonify({"ok": False, "error": "Invalid mode"}), 400

    @app.route("/cmd/buzzer", methods=["POST"])
    def cmd_buzzer():
        data = request.get_json(silent=True) or {}
        melody = data.get("melody", "beep")
        melody_map = {"beep": "beep", "alarm": "alarm", "birthday": "happy_birthday"}
        key = melody_map.get(melody)
        if key:
            state.buzzer.play_melody(key)
            return jsonify({"ok": True, "melody": melody})
        return jsonify({"ok": False, "error": "Unknown melody"}), 400

    @app.route("/cmd/switch", methods=["POST"])
    def cmd_switch():
        data = request.get_json(silent=True) or {}
        sid = int(data.get("id", 0))
        switch_state = data.get("state", False)
        max_switches = len(SWITCH_PINS) if state.switches._initialized else 0
        if 0 <= sid < max_switches:
            state.switches.on(sid) if switch_state else state.switches.off(sid)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": f"Switch id must be 0-{max_switches - 1}"}), 400

    @app.route("/cmd/cv_mode", methods=["POST"])
    def cmd_cv_mode():
        from Server.camera.camera_opencv import (
            CV_MODE_NONE, CV_MODE_FIND_COLOR, CV_MODE_FIND_LINE, CV_MODE_WATCHDOG,
        )
        data = request.get_json(silent=True) or {}
        mode = data.get("mode", "none")
        mode_map = {"none": CV_MODE_NONE, "findColor": CV_MODE_FIND_COLOR,
                     "findlineCV": CV_MODE_FIND_LINE, "watchDog": CV_MODE_WATCHDOG}
        cv_mode = mode_map.get(mode)
        if cv_mode is not None:
            state.init_camera()
            state.camera.set_cv_mode(cv_mode)
            return jsonify({"ok": True, "mode": mode})
        return jsonify({"ok": False}), 400

    @app.route("/cmd/auto", methods=["POST"])
    def cmd_auto():
        data = request.get_json(silent=True) or {}
        func = data.get("func", "stop")
        valid = ("radarScan", "automatic", "trackLine", "keepDistance", "stop")
        if func in valid:
            if func == "stop":
                state.autonomous.stop()
            else:
                state.autonomous.start(func)
            return jsonify({"ok": True, "func": func})
        return jsonify({"ok": False}), 400

    # ── Module HTTP endpoints (fallback) ──────────────────────────────

    @app.route("/api/modules", methods=["GET"])
    def api_modules():
        lang = request.args.get("lang", "en")
        modules = get_module_list(lang)
        uploaded = []
        if os.path.isdir(upload_dir):
            for fname in sorted(os.listdir(upload_dir)):
                if fname.endswith(".py"):
                    uploaded.append({
                        "id": f"upload_{fname}", "name": fname,
                        "desc": f"Uploaded: {fname}", "icon": "page",
                        "hardware": [], "file": fname, "is_upload": True,
                    })
        return jsonify({"modules": modules, "uploads": uploaded,
                        "running": state.module_runner.running_module})

    @app.route("/api/modules/start", methods=["POST"])
    def api_module_start():
        data = request.get_json(silent=True) or {}
        mid = data.get("id", "")
        if mid.startswith("upload_"):
            ok, msg = state.module_runner.start_upload(os.path.join(upload_dir, mid[7:]))
        else:
            ok, msg = state.module_runner.start(mid)
        return jsonify({"ok": ok, "message": msg})

    @app.route("/api/modules/stop", methods=["POST"])
    def api_module_stop():
        state.module_runner.stop()
        return jsonify({"ok": True})

    @app.route("/api/modules/upload", methods=["POST"])
    def api_module_upload():
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file"}), 400
        f = request.files["file"]
        if not f.filename or not f.filename.endswith(".py"):
            return jsonify({"ok": False, "error": "Only .py files"}), 400
        safe_name = os.path.basename(f.filename)
        f.save(os.path.join(upload_dir, safe_name))
        return jsonify({"ok": True, "filename": safe_name})

    # ── Documentation (JSON files for Info tab) ────────────────────────

    @app.route("/docs/index.json")
    def docs_index():
        return send_from_directory(docs_dir, "index.json", mimetype="application/json")

    @app.route("/docs/pinout.json")
    def docs_pinout():
        return send_from_directory(docs_dir, "pinout.json", mimetype="application/json")

    @app.route("/docs/components/<path:name>")
    def docs_component(name):
        return send_from_directory(
            os.path.join(docs_dir, "components"), name,
            mimetype="application/json",
        )

    return app
