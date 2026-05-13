#!/usr/bin/env python3
"""PiCar Pro Flask app — HTTP + MJPEG + REST API."""

import json, os, time
from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from Server.camera.camera_opencv import Camera
from Server.config import (
    FLASK_PORT, DEFAULT_SPEED, SERVO_COUNT, SWITCH_PINS, CRANE_ENABLED,
    SERVO_CLAW_ARM, SERVO_CLAW_GRIP, CLAW_ARM_UP, CLAW_ARM_DOWN, CLAW_GRIP_OPEN, CLAW_GRIP_CLOSED,
)
from Server.modules import get_module_list


def create_app(state):
    server_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(server_dir, "dist")
    upload_dir = os.path.join(server_dir, "modules", "uploads")
    docs_dir = os.path.join(os.path.dirname(server_dir), "docs")
    os.makedirs(upload_dir, exist_ok=True)

    app = Flask(__name__, template_folder=dist_dir, static_folder=None)
    app.config['SECRET_KEY'] = 'picarpro'

    @app.after_request
    def cors(r):
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/favicon.ico")
    def favicon():
        return "", 204

    @app.route("/style.css")
    def css():
        return send_from_directory(dist_dir, "style.css", mimetype="text/css")

    @app.route("/app.js")
    def js():
        return send_from_directory(dist_dir, "app.js", mimetype="application/javascript")

    @app.route("/<path:fn>")
    def dist_files(fn):
        fp = os.path.join(dist_dir, fn)
        if os.path.isfile(fp):
            return send_from_directory(dist_dir, fn)
        return "", 404

    @app.route("/video_feed")
    def video_feed():
        state.init_camera()
        def gen():
            while state.running:
                frame = Camera.get_frame()
                if frame:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                else:
                    time.sleep(0.05)
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    # ── REST API ────────────────────────────────────────────────────────

    @app.route("/api/status")
    def api_status():
        return jsonify(state.get_status())

    @app.route("/api/status/stream")
    def api_sse():
        def gen():
            while state.running:
                yield f"data: {json.dumps(state.get_status())}\n\n"
                time.sleep(1)
        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/cmd/move", methods=["POST"])
    def cmd_move():
        d = (request.get_json(silent=True) or {}).get("dir", "stop")
        state.module_runner.set_command(f"Move: {d}")
        if d in ("forward", "backward"):
            state.motors.move(state.speed, d, "no", 0.5)
        elif d in ("left", "right"):
            state.motors.stop()
        elif d.startswith("forward_"):
            state.motors.move(state.speed, "forward", d.split("_")[1], 0.3)
        elif d.startswith("backward_"):
            state.motors.move(state.speed, "backward", d.split("_")[1], 0.3)
        elif d == "stop":
            state.motors.stop()
            state.module_runner.set_command("Ready")
        else:
            return jsonify({"ok": False}), 400
        return jsonify({"ok": True, "dir": d})

    @app.route("/cmd/speed", methods=["POST"])
    def cmd_speed():
        try:
            state.speed = max(0, min(100, int((request.get_json(silent=True) or {}).get("value", DEFAULT_SPEED))))
            return jsonify({"ok": True, "speed": state.speed})
        except Exception:
            return jsonify({"ok": False}), 400

    @app.route("/cmd/servo", methods=["POST"])
    def cmd_servo():
        d = request.get_json(silent=True) or {}
        sid, ang = int(d.get("id", 0)), int(d.get("angle", 90))
        if 0 <= sid < SERVO_COUNT:
            state.servos.set_angle(sid, max(0, min(180, ang)))
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @app.route("/cmd/servo_home", methods=["POST"])
    def cmd_home():
        state.servos.move_init()
        return jsonify({"ok": True})

    @app.route("/cmd/led", methods=["POST"])
    def cmd_led():
        d = request.get_json(silent=True) or {}
        mode = d.get("mode", "off")
        if mode in ("off","solid","breath","flow","rainbow","police","colorWipe"):
            try:
                color = tuple(max(0, min(255, int(c))) for c in d.get("color", [255,0,0])[:3])
            except Exception:
                color = (255, 0, 0)
            state.leds.set_mode(mode, color)
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @app.route("/cmd/buzzer", methods=["POST"])
    def cmd_buzzer():
        key = {'beep':'beep','alarm':'alarm','birthday':'happy_birthday'}.get(
            (request.get_json(silent=True) or {}).get("melody","beep"))
        if key:
            state.buzzer.play_melody(key)
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @app.route("/cmd/buzzer_stop", methods=["POST"])
    def cmd_buzzer_stop():
        state.buzzer.stop()
        return jsonify({"ok": True})

    @app.route("/cmd/claw", methods=["POST"])
    def cmd_claw():
        if not CRANE_ENABLED:
            return jsonify({"ok": False}), 400
        act = (request.get_json(silent=True) or {}).get("action", "")
        m = {'arm_up':(SERVO_CLAW_ARM,CLAW_ARM_UP),'arm_down':(SERVO_CLAW_ARM,CLAW_ARM_DOWN),
             'grip_open':(SERVO_CLAW_GRIP,CLAW_GRIP_OPEN),'grip_close':(SERVO_CLAW_GRIP,CLAW_GRIP_CLOSED)}
        if act in m:
            state.servos.set_angle(*m[act])
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @app.route("/cmd/cv_mode", methods=["POST"])
    def cmd_cv():
        from Server.camera.camera_opencv import CV_NONE, CV_COLOR, CV_LINE, CV_WATCH
        mode_map = {"none":CV_NONE,"findColor":CV_COLOR,"findlineCV":CV_LINE,"watchDog":CV_WATCH}
        m = mode_map.get((request.get_json(silent=True) or {}).get("mode","none"))
        if m is not None:
            state.init_camera()
            state.camera.set_cv_mode(m)
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @app.route("/api/i2c_scan")
    def api_i2c():
        from Server.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
        devs = i2c_scan()
        addr, who = find_mpu6050_on_bus()
        return jsonify({"ok": True, "devices": [f'0x{a:02X}' for a in devs],
                        "mpu6050_found": addr is not None})

    @app.route("/api/modules")
    def api_modules():
        mods = get_module_list(request.args.get("lang","en"))
        ups = []
        if os.path.isdir(upload_dir):
            for f in sorted(os.listdir(upload_dir)):
                if f.endswith('.py'):
                    ups.append({"id":f"upload_{f}","name":f,"desc":f"Uploaded: {f}"})
        return jsonify({"modules":mods,"uploads":ups,"running":state.module_runner.running_module})

    @app.route("/api/modules/start", methods=["POST"])
    def api_mod_start():
        mid = (request.get_json(silent=True) or {}).get("id","")
        if mid.startswith("upload_"):
            ok, msg = state.module_runner.start_upload(os.path.join(upload_dir, mid[7:]))
        else:
            ok, msg = state.module_runner.start(mid)
        return jsonify({"ok": ok, "message": msg})

    @app.route("/api/modules/stop", methods=["POST"])
    def api_mod_stop():
        state.module_runner.stop()
        return jsonify({"ok": True})

    @app.route("/api/modules/upload", methods=["POST"])
    def api_mod_upload():
        if "file" not in request.files:
            return jsonify({"ok": False}), 400
        f = request.files["file"]
        if not f.filename or not f.filename.endswith(".py"):
            return jsonify({"ok": False}), 400
        f.save(os.path.join(upload_dir, os.path.basename(f.filename)))
        return jsonify({"ok": True})

    @app.route("/docs/index.json")
    def docs_index():
        return send_from_directory(docs_dir, "index.json", mimetype="application/json")

    @app.route("/docs/pinout.json")
    def docs_pinout():
        return send_from_directory(docs_dir, "pinout.json", mimetype="application/json")

    return app
