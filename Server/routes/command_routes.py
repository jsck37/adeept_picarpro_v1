#!/usr/bin/env python3
"""Command blueprint — REST API for robot control.

All routes are registered under the ``/cmd`` url prefix.
"""

from flask import Blueprint, jsonify, request
from Server.config import (
    DEFAULT_SPEED, SERVO_COUNT, CRANE_ENABLED,
    SERVO_CLAW_ARM, SERVO_CLAW_GRIP, CLAW_ARM_UP, CLAW_ARM_DOWN,
    CLAW_GRIP_OPEN, CLAW_GRIP_CLOSED, STEER_MAP,
)


def create_command_blueprint(state):
    """Return a Blueprint that handles robot command endpoints.

    Parameters
    ----------
    state : SharedState
        The shared robot state object.
    """
    bp = Blueprint("cmd", __name__, url_prefix="/cmd")

    @bp.route("/move", methods=["POST"])
    def cmd_move():
        d = (request.get_json(silent=True) or {}).get("dir", "stop")
        if d in ("forward",):
            state.motors.move(state.speed, 'forward', 'no', 0.5)
        elif d in ("backward",):
            state.motors.move(state.speed, 'backward', 'no', 0.5)
        elif d in ("left", "right"):
            state.motors.stop()
        elif d.startswith("forward_"):
            state.motors.move(state.speed, 'forward', d.split("_")[1], 0.3)
        elif d.startswith("backward_"):
            state.motors.move(state.speed, 'backward', d.split("_")[1], 0.3)
        elif d == "stop":
            state.motors.stop()
        state.servos.set_angle(0, STEER_MAP.get(d, 90))  # SERVO_STEERING = 0
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

    @bp.route("/claw", methods=["POST"])
    def cmd_claw():
        if not CRANE_ENABLED:
            return jsonify({"ok": False}), 400
        act = (request.get_json(silent=True) or {}).get("action", "")
        m = {
            'arm_up': (SERVO_CLAW_ARM, CLAW_ARM_UP),
            'arm_down': (SERVO_CLAW_ARM, CLAW_ARM_DOWN),
            'grip_open': (SERVO_CLAW_GRIP, CLAW_GRIP_OPEN),
            'grip_close': (SERVO_CLAW_GRIP, CLAW_GRIP_CLOSED),
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

    return bp
