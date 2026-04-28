#!/usr/bin/env python3
"""Motion Detection — OpenCV watchdog for motion detection."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import CAMERA_RESOLUTION, FLASK_PORT


def main():
    print("[CV Motion] Starting motion detection...")
    print("  Press Ctrl+C to stop.")

    try:
        import cv2
        import numpy as np
        from flask import Flask, Response
    except ImportError as e:
        print(f"  Error: {e}")
        return

    try:
        from picamera2 import Picamera2
        picam = Picamera2()
        config = picam.create_preview_configuration(
            main={"size": CAMERA_RESOLUTION, "format": "RGB888"}
        )
        picam.configure(config)
        picam.start()
    except Exception as e:
        print(f"  Camera error: {e}")
        return

    bg_subtractor = cv2.createBackgroundSubtractorMOG2(
        history=500, varThreshold=25, detectShadows=True
    )

    app = Flask(__name__)
    _running = True

    @app.route('/video_feed')
    def video_feed():
        def generate():
            while _running:
                frame = picam.capture_array()
                if frame is None:
                    continue

                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)

                fg_mask = bg_subtractor.apply(gray)
                _, thresh = cv2.threshold(fg_mask, 25, 255, cv2.THRESH_BINARY)
                thresh = cv2.dilate(thresh, None, iterations=2)

                contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                if contours:
                    cv2.putText(frame_bgr, "MOTION DETECTED", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

                encode_params = [cv2.IMWRITE_JPEG_QUALITY, 70]
                _, jpeg = cv2.imencode('.jpg', frame_bgr, encode_params)
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route('/')
    def index():
        return '''<html><body style="background:#000;margin:0">
        <h2 style="color:#f00;text-align:center">Motion Watchdog</h2>
        <img src="/video_feed" style="width:100%;height:auto">
        </body></html>'''

    try:
        app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        picam.stop()
        print("[CV Motion] Done.")


if __name__ == '__main__':
    main()
