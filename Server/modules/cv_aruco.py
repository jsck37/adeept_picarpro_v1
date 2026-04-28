#!/usr/bin/env python3
"""ArUco Navigation — Detect ArUco markers and navigate waypoints.

Inspired by hugo-Peltier/Adeept-picarPro.
Detects ArUco markers using cv2.aruco, shows robot position and heading.
"""

import sys
import os
import time
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import CAMERA_RESOLUTION


def main():
    print("[CV ArUco] Starting ArUco marker detection...")
    print("  Press Ctrl+C to stop, 'q' to quit window.")

    try:
        import cv2
        import numpy as np
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
        time.sleep(1)  # Camera warmup
    except Exception as e:
        print(f"  Camera error: {e}")
        return

    try:
        while True:
            frame = picam.capture_array()
            if frame is None:
                continue

            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

            # Detect markers
            if detector is not None:
                corners, ids, rejected = detector.detectMarkers(gray)
            else:
                corners, ids, rejected = cv2.aruco.detectMarkers(
                    gray, aruco_dict, parameters=aruco_params
                )

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

            cv2.imshow("ArUco Navigation", frame_bgr)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        pass
    finally:
        picam.stop()
        cv2.destroyAllWindows()
        print("[CV ArUco] Done.")


if __name__ == '__main__':
    main()
