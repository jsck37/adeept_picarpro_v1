#!/usr/bin/env python3
"""Gesture Detection — OpenCV hand gesture detection."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from Server.config import CAMERA_RESOLUTION


def main():
    print("[CV Gesture] Starting hand gesture detection...")
    print("  Press Ctrl+C to stop.")

    try:
        import cv2
        import numpy as np
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

    # HSV skin color range
    skin_lower = np.array([0, 48, 80])
    skin_upper = np.array([20, 255, 255])

    try:
        while True:
            frame = picam.capture_array()
            if frame is None:
                continue

            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

            # Skin mask
            mask = cv2.inRange(hsv, skin_lower, skin_upper)
            mask = cv2.GaussianBlur(mask, (5, 5), 0)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

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

            cv2.imshow("Gesture Detection", frame_bgr)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        pass
    finally:
        picam.stop()
        cv2.destroyAllWindows()
        print("[CV Gesture] Done.")


if __name__ == '__main__':
    main()
