"""Бортовая ArUco-навигация — порт логики Demo.py из репозитория
hugo-Peltier/Adeept-picarPro, адаптированный под камеру самой Pi.

Отличия от хост-версии (tools/aruco_nav_host.py):
* надземная камера видит и робота (ID 0), и все путевые точки сразу;
  бортовая камера видит только маркер ПЕРЕД собой, поэтому:
  - маркер робота не используется,
  - курсовой угол считается от центра кадра до центра маркера,
  - прибытие определяется по площади маркера (близко = большой);
* IMU-слияние не требуется (нет внешнего наблюдателя); при потере маркера
  последний курс удерживается ARUCO_TARGET_HOLD_S секунд, потом STOP;
* по достижении последней путевой точки выполняется схват PICK_BLUE
  (клешня закрывается) — как в оригинальной миссии.

Маркеры: словарь DICT_4X4_50, ID путевых точек — ARUCO_WAYPOINTS.
Режим включается командой 'aruco_nav' (см. commands.py) или из web-UI.
"""
import math
import threading
import time

from picarpro.logger import logger
from config import (
    ARUCO_DICT_NAME, ARUCO_ROBOT_ID, ARUCO_WAYPOINTS,
    ARUCO_ANGLE_THRESHOLD_DEG, ARUCO_SPEED, ARUCO_REACH_AREA,
    ARUCO_REACHED_FRAMES, ARUCO_TARGET_HOLD_S, ARUCO_WAYPOINT_PAUSE_S,
    ARUCO_CAM_FOV_DEG, PICK_BLUE_ON_FINISH,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED, SERVO_CRANE_ARM,
)

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False
    cv2 = None
    np = None


def _make_detector():
    """cv2.aruco-детектор с поддержкой и новых (>=4.7), и старых API."""
    if not _HAS_CV2 or not hasattr(cv2, 'aruco'):
        return None
    try:
        d = getattr(cv2.aruco, ARUCO_DICT_NAME)
        dictionary = cv2.aruco.getPredefinedDictionary(d)
        if hasattr(cv2.aruco, 'ArucoDetector'):          # opencv >= 4.7
            params = cv2.aruco.DetectorParameters()
            return cv2.aruco.ArucoDetector(dictionary, params)
        return dictionary                                 # старый API
    except Exception as e:
        logger.error(f'[ArUco] detector init failed: {e}')
        return None


class ArucoNavigator:
    def __init__(self, state, camera):
        self.state = state
        self.camera = camera
        self._detector = _make_detector()
        self._running = False
        self._active = False
        self._thread = None
        self._lock = threading.Lock()
        # публичный статус
        self.waypoint_index = 0
        self.finished = False
        self.last_angle_err = 0.0
        self.last_marker_area = 0
        self.last_command = 'STOP'
        self.last_seen_ts = 0.0

    @property
    def active(self):
        return self._active

    def get_status(self):
        wps = ARUCO_WAYPOINTS
        cur = wps[self.waypoint_index] if self.waypoint_index < len(wps) else None
        return {
            'active': self._active,
            'finished': self.finished,
            'waypoint': cur,
            'waypoint_index': self.waypoint_index,
            'waypoints_total': len(wps),
            'angle_err': round(self.last_angle_err, 1),
            'marker_area': int(self.last_marker_area),
            'last_cmd': self.last_command,
            'detector_ok': self._detector is not None,
        }

    def start(self):
        if not _HAS_CV2:
            return False, 'opencv не установлен'
        if self._detector is None:
            return False, ('cv2.aruco недоступен — установите '
                           'opencv-contrib-python или opencv>=4.7')
        if self._active:
            return True, 'already running'
        self.finished = False
        self.waypoint_index = 0
        self._running = True
        self._active = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f'[ArUco] nav started, waypoints={ARUCO_WAYPOINTS}')
        return True, 'ok'

    def stop(self):
        self._active = False
        self._running = False
        try:
            if self.state.motors:
                self.state.motors.stop()
        except Exception:
            pass
        logger.info('[ArUco] nav stopped')

    def _loop(self):
        wps = ARUCO_WAYPOINTS
        reached_counter = 0
        last_cmd = 'STOP'
        last_cmd_ts = 0.0
        try:
            while self._running and self._active:
                now = time.time()
                if self.waypoint_index >= len(wps):
                    self._finish()
                    break
                target_id = wps[self.waypoint_index]
                frame = self.camera.get_raw_frame() if self.camera else None
                if frame is None:
                    time.sleep(0.05)
                    continue

                center_x = self._detect_marker_x(frame, target_id)
                h, w = frame.shape[:2]
                cmd = 'STOP'

                if center_x is not None:
                    dx = center_x - w // 2
                    focal = (w / 2.0) / math.tan(
                        math.radians(ARUCO_CAM_FOV_DEG) / 2.0)
                    angle_err = math.degrees(math.atan2(dx, focal))
                    self.last_angle_err = angle_err
                    self.last_seen_ts = now
                    area = self.last_marker_area

                    if area >= ARUCO_REACH_AREA:
                        reached_counter += 1
                        cmd = 'STOP'
                        if reached_counter >= ARUCO_REACHED_FRAMES:
                            logger.info(
                                f'[ArUco] waypoint {target_id} reached '
                                f'(area={area})')
                            self.waypoint_index += 1
                            reached_counter = 0
                            if self.waypoint_index >= len(wps):
                                self._finish()
                                break
                            time.sleep(ARUCO_WAYPOINT_PAUSE_S)
                    else:
                        reached_counter = 0
                        if angle_err > ARUCO_ANGLE_THRESHOLD_DEG:
                            cmd = 'RIGHT'
                        elif angle_err < -ARUCO_ANGLE_THRESHOLD_DEG:
                            cmd = 'LEFT'
                        else:
                            cmd = 'FORWARD'
                    last_cmd, last_cmd_ts = cmd, now
                else:
                    # маркер потерян: удерживаем курс, потом стоп (как в Demo)
                    if (now - last_cmd_ts) < ARUCO_TARGET_HOLD_S \
                            and last_cmd != 'STOP':
                        cmd = last_cmd
                    else:
                        cmd = 'STOP'
                    self.last_angle_err = 0.0

                self.last_command = cmd
                self._drive(cmd)
                time.sleep(0.05)
        finally:
            self._drive('STOP')
            self._active = False

    def _detect_marker_x(self, frame_rgb, target_id):
        """Вернуть X центра целевого маркера (px) или None."""
        try:
            bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            if hasattr(self._detector, 'detectMarkers'):
                corners, ids, _ = self._detector.detectMarkers(bgr)
            else:  # старый API: dictionary.detectMarkers
                corners, ids, _ = cv2.aruco.detectMarkers(
                    bgr, self._detector)
            self.last_marker_area = 0
            if ids is None:
                return None
            ids = ids.flatten()
            for i, corner in enumerate(corners):
                if int(ids[i]) == target_id:
                    pts = corner.reshape((4, 2)).astype(np.float32)
                    cx, cy = np.mean(pts, axis=0)
                    # площадь квадрата маркера — метрика близости
                    side = float(np.linalg.norm(pts[0] - pts[1]) +
                                 np.linalg.norm(pts[1] - pts[2]) +
                                 np.linalg.norm(pts[2] - pts[3]) +
                                 np.linalg.norm(pts[3] - pts[0])) / 4.0
                    self.last_marker_area = int(side * side)
                    return float(cx)
            return None
        except Exception as e:
            logger.warning(f'[ArUco] detect error: {e}')
            return None

    def _drive(self, cmd):
        motors = self.state.motors
        if not motors:
            return
        try:
            if cmd == 'FORWARD':
                motors.move(ARUCO_SPEED, 'forward', 'no', 0.5)
            elif cmd == 'LEFT':
                motors.move(ARUCO_SPEED, 'forward', 'left', 0.3)
            elif cmd == 'RIGHT':
                motors.move(ARUCO_SPEED, 'forward', 'right', 0.3)
            else:
                motors.stop()
        except Exception as e:
            logger.warning(f'[ArUco] drive error: {e}')

    def _finish(self):
        self.finished = True
        self._active = False
        self._drive('STOP')
        if PICK_BLUE_ON_FINISH and self.state.servos:
            # порт PICK_BLUE из Demo.py: по завершении миссии — схват
            try:
                self.state.servos.set_angle(SERVO_CRANE_ARM, CRANE_ARM_CLOSED)
                self.state.crane_arm_closed = True
                logger.info('[ArUco] mission finished — PICK_BLUE done')
            except Exception:
                pass
        else:
            try:
                if self.state.servos:
                    self.state.servos.set_angle(
                        SERVO_CRANE_ARM, CRANE_ARM_OPEN)
            except Exception:
                pass
        logger.info('[ArUco] MISSION FINISHED')

    # избегаем случайного использования ROBOT_ID в бортовом режиме
    ARUCO_ROBOT_ID = ARUCO_ROBOT_ID  # noqa (для документации)
