#!/usr/bin/env python3
"""Color Detection — OpenCV color tracking with bounding box."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import FLASK_PORT


def main():
    print("[CV Color] Starting color detection stream...")

    try:
        import cv2
        import numpy as np
        from flask import Flask, Response
        from Server.camera.camera_opencv import Camera, CV_MODE_FIND_COLOR
    except ImportError as e:
        print(f"  Error: {e}")
        return

    app = Flask(__name__)
    cam = Camera()
    cam.set_cv_mode(CV_MODE_FIND_COLOR)

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
        <h2 style="color:#0f0;text-align:center">Color Detection Active</h2>
        <img src="/video_feed" style="width:100%;height:auto">
        </body></html>'''

    try:
        app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        cam.shutdown()
        print("[CV Color] Done.")


if __name__ == '__main__':
    main()
