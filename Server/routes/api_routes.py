import json, time
from flask import Blueprint, Response, jsonify
from Server.utils.log_buffer import log_buffer


def create_api_blueprint(state):
    bp = Blueprint("api", __name__, url_prefix="/api")

    @bp.route("/status")
    def api_status():
        return jsonify(state.get_status())

    @bp.route("/status/stream")
    def api_sse():
        def gen():
            while state.running:
                yield f"data: {json.dumps(state.get_status())}\n\n"
                time.sleep(1)

        return Response(
            gen(), mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @bp.route("/i2c_scan")
    def api_i2c():
        from Server.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
        devs = i2c_scan()
        addr, who = find_mpu6050_on_bus()
        return jsonify({
            "ok": True,
            "devices": [f'0x{a:02X}' for a in devs],
            "mpu6050_found": addr is not None,
        })

    @bp.route("/logs")
    def api_logs():
        lines = log_buffer.get_lines(last_n=200)
        return jsonify({"ok": True, "lines": [[ts, txt] for ts, txt in lines]})

    return bp
