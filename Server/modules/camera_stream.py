#!/usr/bin/env python3
"""Camera Stream — Flask MJPEG streaming server (standalone)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import FLASK_PORT


def main():
    print(f"[Camera] Starting MJPEG stream on port {FLASK_PORT}...")

    try:
        from flask import Flask, Response
        from Server.camera.camera_opencv import Camera
    except ImportError as e:
        print(f"  Error: {e}")
        return

    app = Flask(__name__)
    cam = Camera()

    @app.route('/video_feed')
    def video_feed():
        def generate():
            while True:
                frame = Camera.get_frame()
                if frame:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route('/')
    def index():
        return '''<html><body style="background:#000;margin:0">
        <img src="/video_feed" style="width:100%;height:100%;object-fit:contain">
        </body></html>'''

    try:
        app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        cam.shutdown()
        print("[Camera] Done.")


if __name__ == '__main__':
    main()
