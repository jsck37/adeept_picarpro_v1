#!/usr/bin/env python3
"""Video stream blueprint — MJPEG feed."""

import time
from flask import Blueprint, Response
from Server.camera.camera_opencv import Camera


def create_video_blueprint(state):
    """Return a Blueprint that provides the MJPEG video feed.

    Parameters
    ----------
    state : SharedState
        The shared robot state object.
    """
    bp = Blueprint("video", __name__)

    @bp.route("/video_feed")
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

    return bp
