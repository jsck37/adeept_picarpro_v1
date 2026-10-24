#!/usr/bin/env python3
"""ArUco-навигация с надземной камеры (хост-ПК) — порт Demo.py из репозитория
hugo-Peltier/Adeept-picarPro.

Что изменено относительно оригинала:
* порт Pi: 5001 (NAV_TCP_PORT) — в оригинале 5000 конфликтовал с Flask;
* поддержана команда SPEED (управление скоростью с хоста);
* PI_IP задаётся первым аргументом или env-переменной PICARPRO_HOST;
* автоматическое подключение с ретраями, безопасный STOP при выходе.

Запуск на хост-ПК (не на Pi):
    pip install opencv-contrib-python numpy
    python3 tools/aruco_nav_host.py 192.168.1.50

Маркеры: DICT_4X4_50. ID 0 — робот, ID из WAYPOINTS — путевые точки.
IMU-слияние: пакеты gz приходят с Pi по UDP (IMU_UDP_PORT), Pi отправляет их
автоматически, как только хост подключается к NavTCP.
"""
import json
import math
import socket
import sys
import os
import time

import cv2
import numpy as np

# --- конфигурация (порт из Demo.py) -----------------------------------------
CAMERA_INDEX = 0
ROBOT_ID = 0
WAYPOINTS = [1, 2, 3]
ARUCO_DICT = cv2.aruco.DICT_4X4_50

ANGLE_THRESHOLD_DEG = 12
ROBOT_FRONT_OFFSET_PX = 55

REACHED_FRAMES_REQUIRED = 3
WAYPOINT_PAUSE = 0.8
TARGET_HOLD_TIMEOUT = 0.25

REACH_CENTER_PX = 40
REACH_FRONT_PX = 50
REACH_ALIGNED_FRONT_PX = 90
REACH_ALIGNED_ANGLE_DEG = 18
REACH_ALIGNED_LATERAL_PX = 75

FINAL_APPROACH_FRONT_PX = 100
FINAL_APPROACH_CENTER_PX = 110
FINAL_APPROACH_FORWARD_MIN = -20
FINAL_APPROACH_FORWARD_MAX = 130
FINAL_APPROACH_LATERAL_PX = 90
FINAL_APPROACH_ANGLE_DEG = 10

JUST_LOST_TARGET_TIMEOUT = 0.7
ROBOT_LOST_HOLD_TIME = 0.35

PI_IP = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    'PICARPRO_HOST', '192.168.137.211')
PI_PORT = 5001                # NAV_TCP_PORT (config.py), не Flask-порт!
SEND_INTERVAL = 0.20
RETRY_INTERVAL = 2.0

IMU_UDP_PORT = 5005
IMU_ALPHA = 0.8
IMU_TIMEOUT = 1.0


def marker_center(pts):
    return np.mean(pts, axis=0)


def normalize(v):
    n = np.linalg.norm(v)
    if n < 1e-6:
        return np.array([1.0, 0.0], dtype=np.float32)
    return v / n


def normalize_angle_rad(a):
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def cross2d(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def signed_angle_deg(v1, v2):
    v1n = normalize(v1)
    v2n = normalize(v2)
    dot = float(np.clip(np.dot(v1n, v2n), -1.0, 1.0))
    det = cross2d(v1n, v2n)
    return math.degrees(math.atan2(det, dot))


def send_command(sock, cmd):
    sock.sendall((cmd + "\n").encode("utf-8"))


def robot_front_vector_from_pts(pts):
    top_mid = (pts[0] + pts[1]) / 2.0
    center = np.mean(pts, axis=0)
    return top_mid - center, center


def heading_from_theta(theta):
    return np.array([math.cos(theta), math.sin(theta)], dtype=np.float32)


def main():
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    parameters = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"Ошибка открытия камеры {CAMERA_INDEX}")
        raise SystemExit

    sock = None
    last_connect_try = 0.0
    last_sent_command = None
    last_sent_time = 0.0

    imu_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    imu_sock.bind(('0.0.0.0', IMU_UDP_PORT))
    imu_sock.setblocking(False)

    theta_imu = 0.0
    imu_initialized = False
    last_imu_packet_time = None
    last_imu_receive_walltime = 0.0

    current_waypoint_index = 0
    mission_finished = False

    last_seen_target_id = None
    last_seen_target_time = 0.0
    last_seen_target_distance = None

    reached_counter = 0
    last_tracking_command = "STOP"
    last_tracking_time = 0.0

    target_was_close = False

    last_robot_seen_time = 0.0
    last_robot_center = None
    last_heading = None

    print("Q — выход")
    print(f"Путевые точки: {WAYPOINTS}; Pi: {PI_IP}:{PI_PORT}")

    def go_to_next_waypoint():
        nonlocal current_waypoint_index, mission_finished
        nonlocal last_seen_target_id, last_seen_target_time
        nonlocal last_seen_target_distance, last_sent_command
        nonlocal reached_counter, target_was_close
        nonlocal last_tracking_command, last_tracking_time

        reached_id = WAYPOINTS[current_waypoint_index]
        print(f">>> WAYPOINT {reached_id} ДОСТИГНУТ")
        current_waypoint_index += 1
        last_sent_command = None
        reached_counter = 0
        last_seen_target_id = None
        last_seen_target_time = 0.0
        last_seen_target_distance = None
        target_was_close = False
        last_tracking_command = "STOP"
        last_tracking_time = 0.0
        if current_waypoint_index >= len(WAYPOINTS):
            mission_finished = True
            print(">>> МИССИЯ ЗАВЕРШЕНА")
            return reached_id, True
        print(f">>> СЛЕДУЮЩАЯ ТОЧКА {WAYPOINTS[current_waypoint_index]}")
        return reached_id, False

    try:
        while True:
            now = time.time()

            if sock is None and (now - last_connect_try) > RETRY_INTERVAL:
                last_connect_try = now
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.connect((PI_IP, PI_PORT))
                    send_command(sock, f"SPEED 35")
                    print(f"Подключено к Pi {PI_IP}:{PI_PORT}")
                except Exception as e:
                    print(f"Нет подключения: {e}")
                    if sock:
                        sock.close()
                    sock = None

            # --- приём IMU-пакетов (слияние курса, как в Demo.py) -----------
            while True:
                try:
                    data, _ = imu_sock.recvfrom(4096)
                    msg = json.loads(data.decode("utf-8"))
                    packet_time = float(msg["t"])
                    gz = float(msg["gz"])
                    if last_imu_packet_time is None:
                        last_imu_packet_time = packet_time
                    else:
                        dt_imu = packet_time - last_imu_packet_time
                        last_imu_packet_time = packet_time
                        if 0.0 < dt_imu < 0.2:
                            theta_imu = normalize_angle_rad(
                                theta_imu + gz * dt_imu)
                    last_imu_receive_walltime = now
                except BlockingIOError:
                    break
                except Exception:
                    break

            imu_alive = (now - last_imu_receive_walltime) < IMU_TIMEOUT

            ret, frame = cap.read()
            if not ret:
                print("Ошибка чтения кадра")
                break
            frame = cv2.resize(frame, (960, 720))

            corners, ids, _ = detector.detectMarkers(frame)
            markers = {}
            if ids is not None:
                ids = ids.flatten()
                for i, corner in enumerate(corners):
                    pts = corner.reshape((4, 2)).astype(np.float32)
                    pts_int = pts.astype(int)
                    center = marker_center(pts).astype(int)
                    marker_id = int(ids[i])
                    markers[marker_id] = pts
                    for j in range(4):
                        cv2.line(frame, tuple(pts_int[j]),
                                 tuple(pts_int[(j + 1) % 4]), (0, 255, 0), 2)
                    cv2.circle(frame, tuple(center), 5, (0, 0, 255), -1)
                    label = (f"ROBOT {marker_id}"
                             if marker_id == ROBOT_ID else
                             (f"WP {marker_id}"
                              if marker_id in WAYPOINTS else f"ID {marker_id}"))
                    cv2.putText(frame, label, (center[0] + 10, center[1]),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (255, 0, 0) if marker_id == ROBOT_ID
                                else (0, 255, 255), 2)

            command = "STOP"
            angle_error = None

            if (not mission_finished and ROBOT_ID in markers
                    and current_waypoint_index < len(WAYPOINTS)):
                robot_pts = markers[ROBOT_ID]
                current_target_id = WAYPOINTS[current_waypoint_index]

                front_vec_cam, robot_center = robot_front_vector_from_pts(
                    robot_pts)
                heading_cam = normalize(front_vec_cam)
                theta_cam = math.atan2(heading_cam[1], heading_cam[0])

                if not imu_initialized:
                    theta_imu = theta_cam
                    imu_initialized = True

                if imu_alive and imu_initialized:
                    err = normalize_angle_rad(theta_cam - theta_imu)
                    theta_fused = normalize_angle_rad(
                        theta_imu + (1.0 - IMU_ALPHA) * err)
                    theta_imu = theta_fused
                else:
                    theta_fused = theta_cam
                    theta_imu = theta_cam

                heading = heading_from_theta(theta_fused)
                if np.dot(heading, heading_cam) < 0.0:
                    heading = heading_cam
                    theta_fused = theta_cam
                    theta_imu = theta_cam

                front_point = robot_center + heading * ROBOT_FRONT_OFFSET_PX
                last_robot_seen_time = now
                last_robot_center = robot_center.copy()
                last_heading = heading.copy()

                cv2.arrowedLine(frame, tuple(robot_center.astype(int)),
                                tuple((robot_center + heading * 120)
                                      .astype(int)), (255, 0, 255), 3)

                if current_target_id in markers:
                    target_center = marker_center(markers[current_target_id])
                    to_target_dir = normalize(target_center - robot_center)
                    to_target_front = target_center - front_point

                    distance = float(np.linalg.norm(
                        target_center - robot_center))
                    front_distance = float(np.linalg.norm(to_target_front))
                    angle_error = signed_angle_deg(heading, to_target_dir)
                    forward_proj = float(np.dot(to_target_front, heading))
                    lateral_error = float(abs(cross2d(
                        heading, to_target_front)))

                    last_seen_target_id = current_target_id
                    last_seen_target_time = now
                    last_seen_target_distance = min(distance, front_distance)

                    if (front_distance < FINAL_APPROACH_FRONT_PX
                            or distance < FINAL_APPROACH_CENTER_PX
                            or (FINAL_APPROACH_FORWARD_MIN < forward_proj
                                < FINAL_APPROACH_FORWARD_MAX
                                and lateral_error < FINAL_APPROACH_LATERAL_PX
                                and abs(angle_error)
                                < FINAL_APPROACH_ANGLE_DEG)):
                        target_was_close = True

                    reach_center = distance < REACH_CENTER_PX
                    reach_front = front_distance < REACH_FRONT_PX
                    reach_aligned = (
                        front_distance < REACH_ALIGNED_FRONT_PX
                        and abs(angle_error) < REACH_ALIGNED_ANGLE_DEG
                        and lateral_error < REACH_ALIGNED_LATERAL_PX)

                    if reach_center or reach_front or reach_aligned:
                        reached_counter += 1
                    else:
                        reached_counter = 0

                    if reached_counter >= REACHED_FRAMES_REQUIRED:
                        command = "STOP"
                        if sock is not None:
                            try:
                                send_command(sock, "STOP")
                                last_sent_command = "STOP"
                                last_sent_time = time.time()
                            except Exception:
                                sock = None
                        _, finished = go_to_next_waypoint()
                        if not finished:
                            time.sleep(WAYPOINT_PAUSE)
                    else:
                        if angle_error > ANGLE_THRESHOLD_DEG:
                            command = "RIGHT"
                        elif angle_error < -ANGLE_THRESHOLD_DEG:
                            command = "LEFT"
                        else:
                            command = "FORWARD"
                        last_tracking_command = command
                        last_tracking_time = now
                else:
                    just_lost = (last_seen_target_id == current_target_id and
                                 (now - last_seen_target_time)
                                 < JUST_LOST_TARGET_TIMEOUT)
                    if target_was_close and just_lost:
                        print(f">>> WP {current_target_id} ВАЛИДАЦИЯ "
                              "(маркер пропал в финальном сближении)")
                        command = "STOP"
                        if sock is not None:
                            try:
                                send_command(sock, "STOP")
                                last_sent_command = "STOP"
                            except Exception:
                                sock = None
                        _, finished = go_to_next_waypoint()
                        if not finished:
                            time.sleep(WAYPOINT_PAUSE)
                    else:
                        if (now - last_tracking_time) < TARGET_HOLD_TIMEOUT:
                            command = last_tracking_command
                        else:
                            command = "STOP"
                    reached_counter = 0

            elif (not mission_finished
                  and current_waypoint_index < len(WAYPOINTS)):
                # робот не виден — держим курс по IMU (как в Demo.py)
                current_target_id = WAYPOINTS[current_waypoint_index]
                robot_recently_lost = (now - last_robot_seen_time) \
                    < ROBOT_LOST_HOLD_TIME
                if (imu_alive and imu_initialized and robot_recently_lost
                        and last_robot_center is not None):
                    heading = heading_from_theta(theta_imu)
                    if last_heading is not None and \
                            np.dot(heading, last_heading) < 0.0:
                        heading = last_heading.copy()
                    robot_center = last_robot_center
                    if current_target_id in markers:
                        target_center = marker_center(
                            markers[current_target_id])
                        to_target_dir = normalize(
                            target_center - robot_center)
                        angle_error = signed_angle_deg(heading, to_target_dir)
                        if angle_error > ANGLE_THRESHOLD_DEG:
                            command = "RIGHT"
                        elif angle_error < -ANGLE_THRESHOLD_DEG:
                            command = "LEFT"
                        else:
                            command = "FORWARD"
                        last_tracking_command = command
                        last_tracking_time = now
                    else:
                        command = "STOP" if (now - last_tracking_time) \
                            >= TARGET_HOLD_TIMEOUT else last_tracking_command
                else:
                    command = "STOP"
                    reached_counter = 0

            if sock is not None:
                try:
                    if (command != last_sent_command) or \
                            ((now - last_sent_time) > SEND_INTERVAL):
                        send_command(sock, command)
                        last_sent_command = command
                        last_sent_time = now
                except Exception:
                    try:
                        sock.close()
                    except Exception:
                        pass
                    sock = None
                    last_sent_command = None

            status = (f"WP {current_waypoint_index}/{len(WAYPOINTS)} "
                      f"IMU:{imu_alive} err:{angle_error}")
            cv2.putText(frame, status, (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            if mission_finished:
                cv2.putText(frame, "MISSION DONE", (20, 80),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 3)

            cv2.imshow("NAV (hugo-Peltier port)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        if sock is not None:
            try:
                send_command(sock, "STOP")
                sock.close()
            except Exception:
                pass
        imu_sock.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
