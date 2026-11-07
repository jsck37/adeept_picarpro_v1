"""Сканирование рукой + цветовой захват — порт colortracking.py из репозитория
hugo-Peltier/Adeept-picarPro, адаптированный под серво PiCarPro v1 (merged).

Оригинал (4-сервовая рука Adeept Pro): база SERVO1 качается 0..180 шагом 2°,
камера ищет цветной объект (HSV), при обнаружении робот доворачивает по 1°
(TRACK_STEP=1), когда объект в мёртвой зоне CENTER_TOL — схват закрытием gripe.

Адаптация под merged-конфигурацию:
* «база» = SERVO_CAM_PAN (панорама камеры, канал 1);
* схват = SERVO_CRANE_ARM (клешня, канал 6);
* HSV-цель берётся из общего фильтра цвета (hand_color / presets),
  как в оригинале MIN_AREA -> COLOR_SCAN_MIN_AREA.

Команда: 'color_scan' {action: 'start'/'stop'}.
"""
import threading

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False
    cv2 = None

from picarpro.logger import logger
from picarpro.camera import vision
from config import (
    COLOR_SCAN_SERVO, COLOR_SCAN_GRIP_SERVO,
    COLOR_SCAN_MIN_AREA, COLOR_SCAN_CENTER_TOL_PX,
    COLOR_SCAN_STEP, COLOR_SCAN_DELAY, COLOR_SCAN_TRACK_STEP,
    COLOR_SCAN_GRIP_CLOSED, COLOR_SCAN_SCAN_RANGE, CV_PROC_SCALE,
)

# синий по умолчанию — как HSV_LOWER/UPPER в colortracking.py
_DEFAULT_LOW = np.array([85, 60, 60], dtype=np.uint8)
_DEFAULT_HIGH = np.array([115, 255, 255], dtype=np.uint8)


class ColorArmScanner:
    def __init__(self, state, camera):
        self.state = state
        self.camera = camera
        self._active = False
        self._stop = threading.Event()
        self._thread = None
        self._demand_held = False   # v1.2.0: держим камеру в боевом ритме
        self.last_mode = 'IDLE'
        self.last_offset = 0
        self.grasp_done = False

    def _demand_open(self):
        if not self._demand_held and self.camera is not None:
            try:
                self.camera.raw_demand_open()
                self._demand_held = True
            except Exception:
                pass

    def _demand_close(self):
        if self._demand_held and self.camera is not None:
            try:
                self.camera.raw_demand_close()
            except Exception:
                pass
            self._demand_held = False

    @property
    def active(self):
        return self._active

    def get_status(self):
        return {'active': self._active, 'mode': self.last_mode,
                'offset_px': self.last_offset,
                'grasp_done': self.grasp_done}

    def start(self):
        if not _HAS_CV2:
            return False, 'opencv не установлен'
        if self._active:
            return True, 'already running'
        if not (self.state.servos and self.state.servos._pwm_initialized):
            return False, 'серво недоступны'
        self.state.init_camera()
        if not self.state.camera:
            return False, 'камера недоступна'
        self.grasp_done = False
        self._stop = threading.Event()    # свой Event на запуск (нет гонки stop->start)
        self._active = True
        self._demand_open()   # v1.2.0: сырые кадры нужны в полном темпе
        self._thread = threading.Thread(target=self._loop, args=(self._stop,),
                                        daemon=True)
        self._thread.start()
        logger.info('[ColorScan] started (SCAN mode)')
        return True, 'ok'

    def stop(self):
        self._active = False
        self._stop.set()
        self._demand_close()
        self.last_mode = 'IDLE'
        logger.info('[ColorScan] stopped')
        return True, 'ok'

    def _find_target(self, frame_rgb):
        """Вернуть (cx, area) крупнейшего контура цели (в координатах
        полного кадра) или (None, 0). Цвет — общий фильтр trackHand/findColor."""
        cam = self.state.camera
        low_high = cam.get_hand_color() if cam else None
        if low_high:
            low = np.array(low_high[0], dtype=np.uint8)
            high = np.array(low_high[1], dtype=np.uint8)
        else:
            low, high = _DEFAULT_LOW, _DEFAULT_HIGH
        s = CV_PROC_SCALE if CV_PROC_SCALE < 0.999 else 1.0
        mask = vision.hsv_mask(vision.downscale(frame_rgb, s), low, high)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        blob = vision.largest_blob(mask, COLOR_SCAN_MIN_AREA * s * s)
        if blob is None:
            return None, 0
        return int(blob[0] / s), int(blob[2] / (s * s))

    def _loop(self, stop):
        servos = self.state.servos
        scan_pos = None      # задаётся после первого кадра
        scan_dir = 1
        last_id = 0
        try:
            while not stop.is_set():
                # ждём НОВЫЙ кадр: раньше один и тот же кадр копировался и
                # обрабатывался по 2-3 раза за период камеры
                last_id, frame = (self.camera.wait_raw_frame(last_id, 0.5)
                                  if self.camera else (last_id, None))
                if frame is None:
                    continue
                h, w = frame.shape[:2]
                cx_frame = w // 2

                # инициализация позиции сканирования — от текущего угла серво
                if scan_pos is None:
                    scan_pos = int(servos.get_angle(COLOR_SCAN_SERVO))
                    scan_pos = max(COLOR_SCAN_SCAN_RANGE[0],
                                   min(COLOR_SCAN_SCAN_RANGE[1], scan_pos))

                found = self._find_target(frame)
                if found[0] is not None:
                    cx, area = found
                    offset = cx - cx_frame
                    self.last_offset = offset

                    if abs(offset) <= COLOR_SCAN_CENTER_TOL_PX:
                        # цель в мёртвой зоне — СХВАТ (порт grasp)
                        self.last_mode = 'GRASP'
                        logger.info(f'[ColorScan] grasp (area={area})')
                        servos.smooth_move(COLOR_SCAN_GRIP_SERVO,
                                           COLOR_SCAN_GRIP_CLOSED,
                                           steps=12, delay=0.05)
                        self.state.crane_arm_closed = True
                        self.grasp_done = True
                        self._active = False
                        self.last_mode = 'DONE'
                        break
                    else:
                        # доворот по 1° в сторону цели (TRACK_STEP=1)
                        self.last_mode = 'TRACK'
                        step = COLOR_SCAN_TRACK_STEP if offset > 0 \
                            else -COLOR_SCAN_TRACK_STEP
                        scan_pos = max(COLOR_SCAN_SCAN_RANGE[0],
                                       min(COLOR_SCAN_SCAN_RANGE[1],
                                           scan_pos + step))
                        servos.set_angle(COLOR_SCAN_SERVO, scan_pos)
                        stop.wait(COLOR_SCAN_DELAY)
                else:
                    self.last_mode = 'SCAN'
                    # маятниковое сканирование шагом COLOR_SCAN_STEP
                    scan_pos += scan_dir * COLOR_SCAN_STEP
                    lo, hi = COLOR_SCAN_SCAN_RANGE
                    if scan_pos >= hi:
                        scan_pos, scan_dir = hi, -1
                    elif scan_pos <= lo:
                        scan_pos, scan_dir = lo, 1
                    servos.set_angle(COLOR_SCAN_SERVO, scan_pos)
                    stop.wait(COLOR_SCAN_DELAY)
        finally:
            self._active = False
            self._demand_close()   # v1.2.0: скан завершён — камере можно idle
            if self.last_mode != 'DONE':
                self.last_mode = 'IDLE'
