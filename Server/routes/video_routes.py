import time
from flask import Blueprint, Response
from Server.camera.camera_opencv import Camera


def create_video_blueprint(state):
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
