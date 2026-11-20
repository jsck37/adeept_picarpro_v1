"""Поиск рук через Ultralytics YOLO-pose (v1.6.10, режим Hand).

https://docs.ultralytics.com/tasks/pose — модель YOLO11-pose отдаёт для каждого
человека рамку и 17 ключевых точек COCO. Кисти (запястья) — точки 9 (левая) и
10 (правая). Прежний режим искал руку по цвету кожи (HSV) и на реальной сцене
срабатывал на лицо, дерево, стены.

На роботе PyTorch не нужен: модель один раз экспортируется в ONNX
(`yolo export model=yolo11n-pose.pt format=onnx imgsz=320`, см. docs/POSE_GUIDE.md),
а здесь запускается через cv2.dnn — так же, как сегментация YOLO.

«Самая передняя рука» — ближайшая к камере. Настоящей глубины у моно-камеры нет,
поэтому используется оценка по масштабу: чем ближе человек, тем больше он в
кадре (высота рамки). Для двух рук одного человека предпочтение — поднятой
(запястье выше локтя), затем более уверенной точке.
"""
import os
import threading
import time

from picarpro.logger import logger
from config import (
    POSE_MODEL_PATH, POSE_MODEL_BUNDLED, POSE_INPUT_SIZE, POSE_CONF,
    POSE_NMS, POSE_KP_CONF, POSE_FRAME_SKIP, POSE_PREFER_RAISED,
)

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:                      # pragma: no cover
    cv2 = None
    np = None
    _HAS_CV2 = False

# индексы ключевых точек COCO
KP_L_SHOULDER, KP_R_SHOULDER = 5, 6
KP_L_ELBOW, KP_R_ELBOW = 7, 8
KP_L_WRIST, KP_R_WRIST = 9, 10
N_KP = 17
# «кости» рук для отрисовки: плечо-локоть-запястье
ARM_BONES = ((KP_L_SHOULDER, KP_L_ELBOW), (KP_L_ELBOW, KP_L_WRIST),
             (KP_R_SHOULDER, KP_R_ELBOW), (KP_R_ELBOW, KP_R_WRIST),
             (KP_L_SHOULDER, KP_R_SHOULDER))
_COLS = 4 + 1 + N_KP * 3        # cx, cy, w, h, conf, 17 × (x, y, conf) = 56


def pick_front_hand(persons, kp_conf=POSE_KP_CONF, prefer_raised=POSE_PREFER_RAISED):
    """Выбрать самую переднюю руку среди найденных людей.

    persons — список dict из PoseDetector.infer(). Возвращает dict
    {'x','y','side','conf','scale','raised','person'} или None.
    Выбор детерминирован: сначала масштаб человека (ближе = больше), затем
    «поднятая» рука, затем уверенность точки.
    """
    best, best_key = None, None
    for pi, p in enumerate(persons):
        kps = p['kps']
        scale = float(p['box'][3])                  # высота рамки человека, px
        for side, w_i, e_i in (('left', KP_L_WRIST, KP_L_ELBOW),
                               ('right', KP_R_WRIST, KP_R_ELBOW)):
            wx, wy, wc = kps[w_i]
            if wc < kp_conf:
                continue
            ex, ey, ec = kps[e_i]
            raised = bool(ec >= kp_conf and wy < ey)
            key = (round(scale / 8.0),               # грубое квантование: два человека
                   1 if (raised and prefer_raised) else 0,   # примерно одной дальности
                   wc)                                # сравниваются по поднятой руке
            if best_key is None or key > best_key:
                best_key = key
                best = {'x': float(wx), 'y': float(wy), 'side': side,
                        'conf': float(wc), 'scale': scale, 'raised': raised,
                        'person': pi}
    return best


class PoseDetector:
    """Обёртка cv2.dnn вокруг YOLO-pose ONNX (формат Ultralytics, выход 1×56×N)."""

    def __init__(self):
        self._net = None
        self._frame_counter = 0
        self.last_infer_ms = 0.0
        self._last_infer_ts = 0.0
        self.persons = []                # результат последнего инференса
        self.hand = None                 # самая передняя рука или None
        self._lock = threading.Lock()
        self._size = POSE_INPUT_SIZE
        self.model_path = None

    @property
    def loaded(self):
        return self._net is not None

    def _find_model(self):
        for path in (POSE_MODEL_PATH, POSE_MODEL_BUNDLED):
            if path and os.path.isfile(path):
                return path
        return None

    def load(self):
        """Ленивая загрузка при первом включении режима Hand."""
        if self._net is not None or not _HAS_CV2:
            return self.loaded
        path = self._find_model()
        if not path:
            logger.warning(
                '[Pose] model not found — положите yolo11n-pose.onnx в '
                '/home/pi/models (или запустите setup.py), см. docs/POSE_GUIDE.md')
            return False
        try:
            net = cv2.dnn.readNetFromONNX(path)
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self._net = net
            self.model_path = path
            logger.info(f'[Pose] loaded {path} (input {self._size}x{self._size})')
        except Exception as e:
            logger.error(f'[Pose] load failed: {e}')
            self._net = None
        return self.loaded

    def should_infer(self):
        """Инференс каждый POSE_FRAME_SKIP-й кадр (разгрузка CPU)."""
        self._frame_counter += 1
        return self._frame_counter % max(1, POSE_FRAME_SKIP) == 0

    # ---- инференс ---------------------------------------------------------
    def infer(self, frame_rgb):
        """Найти людей и самую переднюю руку. Кадр — RGB, как у камеры."""
        if self._net is None:
            return []
        t0 = time.monotonic()
        h0, w0 = frame_rgb.shape[:2]
        S = self._size
        r = min(S / w0, S / h0)
        nw, nh = int(round(w0 * r)), int(round(h0 * r))
        resized = cv2.resize(frame_rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((S, S, 3), 114, dtype=np.uint8)
        dx, dy = (S - nw) // 2, (S - nh) // 2
        canvas[dy:dy + nh, dx:dx + nw] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 255.0, (S, S), swapRB=False)
        self._net.setInput(blob)
        out = self._net.forward()
        persons = self._decode(out, r, dx, dy, w0, h0)
        hand = pick_front_hand(persons)
        self.last_infer_ms = (time.monotonic() - t0) * 1000.0
        self._last_infer_ts = time.time()
        with self._lock:
            self.persons = persons
            self.hand = hand
        return persons

    @staticmethod
    def _decode(out, r, dx, dy, w0, h0, conf_thr=POSE_CONF, nms_thr=POSE_NMS):
        """Выход Ultralytics-pose: (1, 56, N) -> список людей в пикселях кадра."""
        a = np.asarray(out)
        if a.ndim == 3:
            a = a[0]
        if a.shape[0] == _COLS and a.shape[1] != _COLS:
            a = a.T                                   # -> (N, 56)
        if a.ndim != 2 or a.shape[1] < _COLS:
            return []
        sel = a[:, 4] >= conf_thr
        a = a[sel]
        if a.shape[0] == 0:
            return []
        cx, cy, bw, bh = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, bw, bh], axis=1)
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), a[:, 4].tolist(), conf_thr, nms_thr)
        idx = np.array(idx).reshape(-1).tolist() if len(idx) else []
        persons = []
        for i in idx:
            row = a[i]
            x0 = (row[0] - row[2] / 2 - dx) / r
            y0 = (row[1] - row[3] / 2 - dy) / r
            bw_, bh_ = row[2] / r, row[3] / r
            kp = row[5:5 + N_KP * 3].reshape(N_KP, 3).copy()
            kp[:, 0] = np.clip((kp[:, 0] - dx) / r, 0, w0 - 1)
            kp[:, 1] = np.clip((kp[:, 1] - dy) / r, 0, h0 - 1)
            persons.append({
                'conf': float(row[4]),
                'box': [float(max(0.0, x0)), float(max(0.0, y0)),
                        float(bw_), float(bh_)],
                'kps': [(float(x), float(y), float(c)) for x, y, c in kp],
            })
        persons.sort(key=lambda p: -p['box'][3])
        return persons

    # ---- отрисовка --------------------------------------------------------
    def draw(self, disp_bgr, persons, hand):
        """Скелет рук, рамки людей и метка выбранной руки (на BGR-кадре)."""
        if not _HAS_CV2:
            return disp_bgr
        for pi, p in enumerate(persons):
            x, y, bw, bh = [int(v) for v in p['box']]
            tracked = hand is not None and hand['person'] == pi
            cv2.rectangle(disp_bgr, (x, y), (x + int(bw), y + int(bh)),
                          (0, 255, 255) if tracked else (120, 120, 120), 1)
            for a, b in ARM_BONES:
                xa, ya, ca = p['kps'][a]
                xb, yb, cb = p['kps'][b]
                if ca >= POSE_KP_CONF and cb >= POSE_KP_CONF:
                    cv2.line(disp_bgr, (int(xa), int(ya)), (int(xb), int(yb)),
                             (255, 200, 0), 2)
            for w_i in (KP_L_WRIST, KP_R_WRIST):
                xw, yw, cw = p['kps'][w_i]
                if cw >= POSE_KP_CONF:
                    cv2.circle(disp_bgr, (int(xw), int(yw)), 5, (255, 200, 0), -1)
        if hand is not None:
            hx, hy = int(hand['x']), int(hand['y'])
            cv2.circle(disp_bgr, (hx, hy), 20, (0, 0, 255), 2)
            cv2.line(disp_bgr, (hx - 28, hy), (hx + 28, hy), (0, 0, 255), 2)
            cv2.line(disp_bgr, (hx, hy - 28), (hx, hy + 28), (0, 0, 255), 2)
            cv2.putText(disp_bgr, f"HAND {hand['side']} ({hx},{hy})", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        else:
            cv2.putText(disp_bgr, 'SEARCHING...', (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(disp_bgr, f'POSE {len(persons)} ({self.last_infer_ms:.0f} ms)',
                    (10, disp_bgr.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 255, 255), 1)
        return disp_bgr

    def get_status(self):
        with self._lock:
            hand = dict(self.hand) if self.hand else None
            n = len(self.persons)
        return {
            'loaded': self.loaded,
            'persons': n,
            'hand': ({'x': round(hand['x']), 'y': round(hand['y']),
                      'side': hand['side']} if hand else None),
            'infer_ms': round(self.last_infer_ms, 1),
        }
