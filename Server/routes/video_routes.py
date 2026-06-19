import time
from flask import Blueprint, Response


def create_video_blueprint(state):
    bp = Blueprint("video", __name__)

    @bp.route("/video_feed")
    def video_feed():
        state.init_camera()

        def gen():
            while state.running:
                frame = state.camera.get_frame() if state.camera else None
                if frame:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                else:
                    time.sleep(0.05)

        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return bp
