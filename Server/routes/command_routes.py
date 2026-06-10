from flask import Blueprint, jsonify, request
from config import (
    DEFAULT_SPEED, SERVO_COUNT,
    SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED,
    CRANE_GRIP_LOW, CRANE_GRIP_MID, CRANE_GRIP_HIGH,
    STEER_MAP, SERVO_STEERING,
)


def create_command_blueprint(state):
    bp = Blueprint("cmd", __name__, url_prefix="/cmd")

    @bp.route("/move", methods=["POST"])
    def cmd_move():
        d = (request.get_json(silent=True) or {}).get("dir", "stop")
        if d == "forward":
            state.motors.move(state.speed, 'forward', 'no', 0.5)
        elif d == "backward":
            state.motors.move(state.speed, 'backward', 'no', 0.5)
        elif d in ("left", "right"):
            state.motors.move(state.speed, 'forward', d, 0.3)
        elif d.startswith("forward_"):
            state.motors.move(state.speed, 'forward', d.split("_")[1], 0.3)
        elif d.startswith("backward_"):
            state.motors.move(state.speed, 'backward', d.split("_")[1], 0.3)
        elif d == "stop":
            state.motors.stop()
        state.servos.set_angle(SERVO_STEERING, STEER_MAP.get(d, 90))
        return jsonify({"ok": True, "dir": d})

    @bp.route("/speed", methods=["POST"])
    def cmd_speed():
        try:
            state.speed = max(0, min(100, int(
                (request.get_json(silent=True) or {}).get("value", DEFAULT_SPEED)
            )))
            return jsonify({"ok": True, "speed": state.speed})
        except Exception:
            return jsonify({"ok": False}), 400

    @bp.route("/servo", methods=["POST"])
    def cmd_servo():
        d = request.get_json(silent=True) or {}
        sid, ang = int(d.get("id", 0)), int(d.get("angle", 90))
        if 0 <= sid < SERVO_COUNT:
            state.servos.set_angle(sid, max(0, min(180, ang)))
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @bp.route("/servo_home", methods=["POST"])
    def cmd_home():
        state.servos.move_init()
        return jsonify({"ok": True})

    @bp.route("/led", methods=["POST"])
    def cmd_led():
        d = request.get_json(silent=True) or {}
        mode = d.get("mode", "off")
        if mode in ("off", "solid", "breath", "flow", "rainbow", "police", "colorWipe"):
            try:
                color = tuple(max(0, min(255, int(c))) for c in d.get("color", [255, 0, 0])[:3])
            except Exception:
                color = (255, 0, 0)
            state.leds.set_mode(mode, color)
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @bp.route("/buzzer", methods=["POST"])
    def cmd_buzzer():
        key = {'beep': 'beep', 'birthday': 'happy_birthday'}.get(
            (request.get_json(silent=True) or {}).get("melody", "beep"))
        if key:
            state.buzzer.play_melody(key)
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @bp.route("/buzzer_stop", methods=["POST"])
    def cmd_buzzer_stop():
        state.buzzer.stop()
        return jsonify({"ok": True})

    @bp.route("/crane", methods=["POST"])
    def cmd_crane():
        act = (request.get_json(silent=True) or {}).get("action", "")
        m = {
            'arm_open': (SERVO_CRANE_ARM, CRANE_ARM_OPEN),
            'arm_close': (SERVO_CRANE_ARM, CRANE_ARM_CLOSED),
            'grip_low': (SERVO_CRANE_GRIP, CRANE_GRIP_LOW),
            'grip_mid': (SERVO_CRANE_GRIP, CRANE_GRIP_MID),
            'grip_high': (SERVO_CRANE_GRIP, CRANE_GRIP_HIGH),
        }
        if act in m:
            state.servos.set_angle(*m[act])
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @bp.route("/cv_mode", methods=["POST"])
    def cmd_cv():
        from Server.camera.camera_opencv import CV_NONE, CV_LINE, CV_HAND
        mode_map = {
            "none": CV_NONE,
            "findlineCV": CV_LINE,
            "trackHand": CV_HAND,
        }
        m = mode_map.get((request.get_json(silent=True) or {}).get("mode", "none"))
        if m is not None:
            state.init_camera()
            state.camera.set_cv_mode(m)
            return jsonify({"ok": True})
        return jsonify({"ok": False}), 400

    @bp.route("/voice", methods=["POST"])
    def cmd_voice():
        from Server.commands import process_command
        d = request.get_json(silent=True) or {}
        r = process_command(state, {'cmd': 'voice', 'params': d})
        return jsonify(r)

    return bp
