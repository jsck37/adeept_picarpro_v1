"""
Standalone video viewer for PiCar Pro.
Receives MJPEG stream from Flask server or ZMQ from FPV mode.
"""

import sys
import time

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    print("OpenCV not available. Install: pip3 install opencv-python")


def view_mjpeg(url="http://192.168.4.1:5000/video_feed"):
    """
    View MJPEG stream from Flask server.
    
    Args:
        url: MJPEG stream URL
    """
    if not HAS_CV2:
        return

    print(f"Connecting to: {url}")
    cap = cv2.VideoCapture(url)

    if not cap.isOpened():
        print("Failed to open stream")
        return

    print("Stream opened. Press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Lost connection, retrying...")
            time.sleep(1)
            cap.release()
            cap = cv2.VideoCapture(url)
            continue

        cv2.imshow("PiCar Pro - FPV", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


def view_zmq(port=5555):
    """
    View ZMQ video stream from FPV mode.
    
    Args:
        port: ZMQ port number
    """
    if not HAS_CV2:
        return

    try:
        import zmq
        import pybase64
    except ImportError:
        print("zmq and pybase64 required for ZMQ mode")
        return

    context = zmq.Context()
    socket = context.socket(zmq.PAIR)
    socket.bind(f"tcp://*:{port}")

    print(f"Waiting for ZMQ connection on port {port}...")

    while True:
        try:
            message = socket.recv_string()
            frame_data = pybase64.b64decode(message)
            import numpy as np
            nparr = np.frombuffer(frame_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is not None:
                cv2.imshow("PiCar Pro - FPV (ZMQ)", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(0.1)

    socket.close()
    context.term()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    if len(sys.argv) > 1:
        if sys.argv[1] == '--zmq':
            port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
            view_zmq(port)
        else:
            view_mjpeg(sys.argv[1])
    else:
        view_mjpeg()
