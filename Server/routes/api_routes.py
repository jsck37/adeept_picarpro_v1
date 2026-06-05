#!/usr/bin/env python3
"""API blueprint — status, SSE stream, and hardware scan.

All routes are registered under the ``/api`` url prefix.
"""

import json, time
from flask import Blueprint, Response, jsonify


def create_api_blueprint(state):
    """Return a Blueprint that provides status and info endpoints.

    Parameters
    ----------
    state : SharedState
        The shared robot state object.
    """
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

    return bp
