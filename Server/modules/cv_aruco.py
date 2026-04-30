#!/usr/bin/env python3
"""ArUco Navigation — Detect ArUco markers and navigate waypoints.

Inspired by hugo-Peltier/Adeept-picarPro.
Detects ArUco markers using cv2.aruco, shows robot position and heading.

Optimized: RGB->GRAY directly (skips intermediate BGR conversion).
Added MJPEG stream output for headless Pi (no cv2.imshow needed).
"""

import sys
import os
import time
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import CAMERA_RESOLUTION, FLASK_PORT


def main():
    print("[CV ArUco] Starting ArUco marker detection...")
    print("  Press Ctrl+C to stop.")
    print("  Web stream at http://0.0.0.0:5000/")

    try:
        import cv2
        import numpy as np
        from flask import Flask, Response
    except ImportError as e:
        print(f"  Error: {e}")
        return

    # Check ArUco availability
    try:
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        aruco_params = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(aruco_dict, aruco_params)
    except AttributeError:
        # Older OpenCV
        aruco_dict = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)
        aruco_params = cv2.aruco.DetectorParameters_create()
        detector = None

    try:
        from picamera2 import Picamera2
        picam = Picamera2()
        config = picam.create_preview_configuration(
            main={"size": CAMERA_RESOLUTION, "format": "RGB888"}
        )
        picam.configure(config)
        picam.start()
        time.sleep(0.5)  # Camera warmup
    except Exception as e:
        print(f"  Camera error: {e}")
        return

    app = Flask(__name__)
    _running = True
    _latest_jpeg = None

    def process_frames():
        """Background frame processing loop."""
        nonlocal _latest_jpeg
        while _running:
            frame = picam.capture_array()
            if frame is None:
                continue

            # Optimize: convert RGB->GRAY directly for detection (skip BGR intermediate)
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

            # Detect markers
            if detector is not None:
                corners, ids, rejected = detector.detectMarkers(gray)
            else:
                corners, ids, rejected = cv2.aruco.detectMarkers(
                    gray, aruco_dict, parameters=aruco_params
                )

            # Convert RGB->BGR only for display overlays and encoding
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            if ids is not None and len(ids) > 0:
                cv2.aruco.drawDetectedMarkers(frame_bgr, corners, ids)

                for i, marker_id in enumerate(ids):
                    # Get center of marker
                    c = corners[i][0]
                    cx = int(c[:, 0].mean())
                    cy = int(c[:, 1].mean())

                    # Draw center
                    cv2.circle(frame_bgr, (cx, cy), 5, (0, 255, 0), -1)
                    cv2.putText(frame_bgr, f"ID:{marker_id[0]}", (cx + 10, cy),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                    # Calculate heading (angle from top edge of marker)
                    top_left = c[0]
                    top_right = c[1]
                    angle = math.degrees(
                        math.atan2(top_right[1] - top_left[1],
                                   top_right[0] - top_left[0])
                    )
                    cv2.putText(frame_bgr, f"Angle:{angle:.1f}", (cx + 10, cy + 20),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)

                info_text = f"Markers: {len(ids)}"
            else:
                info_text = "No markers"

            cv2.putText(frame_bgr, info_text, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(frame_bgr, "ArUco DICT_4X4_50", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            # JPEG encode for MJPEG stream
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, 75]
            _, jpeg = cv2.imencode('.jpg', frame_bgr, encode_params)
            _latest_jpeg = jpeg.tobytes()

    # Start processing in background thread
    import threading
    proc_thread = threading.Thread(target=process_frames, daemon=True)
    proc_thread.start()

    @app.route('/video_feed')
    def video_feed():
        def generate():
            while _running:
                if _latest_jpeg:
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + _latest_jpeg + b'\r\n')
                time.sleep(0.033)  # ~30fps
        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route('/')
    def index():
        return '''<html><body style="background:#000;margin:0">
        <h2 style="color:#0f0;text-align:center">ArUco Navigation</h2>
        <img src="/video_feed" style="width:100%;height:auto">
        </body></html>'''

    try:
        app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        picam.stop()
        print("[CV ArUco] Done.")


if __name__ == '__main__':
    main()
