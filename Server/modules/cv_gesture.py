#!/usr/bin/env python3
"""Gesture Detection — OpenCV hand gesture detection.
Optimized: RGB->HSV directly (skips intermediate BGR conversion).
Added MJPEG stream output for headless Pi (no cv2.imshow needed).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import CAMERA_RESOLUTION, FLASK_PORT


def main():
    print("[CV Gesture] Starting hand gesture detection...")
    print("  Press Ctrl+C to stop.")
    print("  Web stream at http://0.0.0.0:5000/")

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
        time.sleep(0.5)  # Camera warmup
    except Exception as e:
        print(f"  Camera error: {e}")
        return

    # HSV skin color range
    skin_lower = np.array([0, 48, 80])
    skin_upper = np.array([20, 255, 255])

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

            # Optimize: convert RGB->HSV directly for skin detection
            # (skip intermediate BGR conversion — cv2.COLOR_RGB2HSV is faster)
            hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)

            # Skin mask
            mask = cv2.inRange(hsv, skin_lower, skin_upper)
            mask = cv2.GaussianBlur(mask, (5, 5), 0)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Convert RGB->BGR only once for display/overlay/encoding
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            for cnt in contours:
                if cv2.contourArea(cnt) < 5000:
                    continue

                # Convexity defects for finger counting
                hull = cv2.convexHull(cnt, returnPoints=False)
                if len(hull) < 3:
                    continue

                try:
                    defects = cv2.convexityDefects(cnt, hull)
                    if defects is None:
                        continue

                    fingers = 0
                    for i in range(defects.shape[0]):
                        s, e, f, d = defects[i, 0]
                        start = tuple(cnt[s][0])
                        end = tuple(cnt[e][0])
                        far = tuple(cnt[f][0])

                        # Calculate angle
                        a = np.linalg.norm(np.array(start) - np.array(far))
                        b = np.linalg.norm(np.array(end) - np.array(far))
                        c = np.linalg.norm(np.array(start) - np.array(end))

                        if b * a != 0:
                            angle = np.arccos((b**2 + a**2 - c**2) / (2 * b * a))
                            if angle < np.pi / 2:
                                fingers += 1

                    cv2.drawContours(frame_bgr, [cnt], -1, (0, 255, 0), 2)
                    x, y, w, h = cv2.boundingRect(cnt)
                    cv2.putText(frame_bgr, f"Fingers: {fingers}", (x, y - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                except cv2.error:
                    pass

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
        <h2 style="color:#0f0;text-align:center">Gesture Detection</h2>
        <img src="/video_feed" style="width:100%;height:auto">
        </body></html>'''

    try:
        app.run(host='0.0.0.0', port=FLASK_PORT, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        picam.stop()
        print("[CV Gesture] Done.")


if __name__ == '__main__':
    main()
