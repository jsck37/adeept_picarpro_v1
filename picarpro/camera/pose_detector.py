"""Поиск рук через Ultralytics YOLO-pose (v1.6.10, режим Hand).

https://docs.ultralytics.com/tasks/pose — модель YOLO11-pose отдаёт для каждого
человека рамку и 17 ключевых точек COCO. Прежний режим искал руку по цвету кожи
(HSV) и на реальной сцене срабатывал на лицо, дерево, стены.

v1.6.13 — слежение за ЛАДОНЬЮ, а не за рукой целиком:
* COCO-17 не содержит точек пальцев, поэтому центр ладони оценивается
  геометрически: от запястья по направлению «локоть -> запястье» на
  POSE_PALM_EXTEND долю длины предплечья (ладонь — это ~1/3 предплечья от
  запястья до середины). Камера при этом наводится на середину кисти, даже
  когда рука машет предплечьем.
* Точка дополнительно «привязывается» к реальному пятну кожи (HSV-маска в
  малом кадре вокруг расчётной точки, POSE_PALM_SNAP) — если пятно найдено,
  целью становится его центр масс: слежение не уплывает на рукав.
* Дообученная модель поддерживается без правок кода: размерность выхода
  распознаётся автоматически. Раскладка 'coco17' (17 точек, запястья 9/10)
  или 'hand21' (21 точка кисти в стиле MediaPipe: 0 — запястье, центр
  ладони = среднее MCP-точек 5/9/13/17). Как дообучить — tools/finetune_pose.py
  и docs/POSE_GUIDE.md.

На роботе PyTorch не нужен: модель один раз экспортируется в ONNX
(`yolo export model=yolo11n-pose.pt format=onnx imgsz=320`, см. docs/POSE_GUIDE.md),
а здесь запускается через cv2.dnn — так же, как сегментация YOLO.

«Самая передняя рука» — ближайшая к камере. Настоящей глубины у моно-камеры нет,
поэтому используется оценка по масштабу: чем ближе человек, тем больше он в
кадре (высота рамки). Для двух рук одного человека предпочтение — поднятой
(запястье выше локтя), затем более уверенной точке.
"""
import math
import os
import threading
import time

from picarpro.logger import logger
from picarpro.camera import vision
from config import (
    POSE_MODEL_PATH, POSE_MODEL_BUNDLED, POSE_INPUT_SIZE, POSE_CONF,
    POSE_NMS, POSE_KP_CONF, POSE_FRAME_SKIP, POSE_PREFER_RAISED,
    POSE_PALM_EXTEND, POSE_PALM_SNAP, POSE_PALM_SNAP_MIN_AREA,
    POSE_KPT_LAYOUT,
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

# ---- раскладки ключевых точек (v1.6.13) -----------------------------------
# 'coco17' — стандартная модель YOLO-pose (человек целиком);
# 'hand21' — дообученная модель кисти в стиле MediaPipe: 0 — запястье,
#            5/9/13/17 — основания (MCP) указательного/среднего/безымянного/
#            мизинца — их среднее и есть центр ладони.
_LAYOUTS = {
    'coco17': {'arms': (('left', KP_L_WRIST, KP_L_ELBOW),
                        ('right', KP_R_WRIST, KP_R_ELBOW)),
               'palm_mcps': (), 'min_kp': 11},
    'hand21': {'arms': (('hand', 0, None),),
               'palm_mcps': (5, 9, 13, 17), 'min_kp': 18},
}


def detect_layout(n_kps):
    """Раскладка по числу точек выхода модели ('auto' в config)."""
    if n_kps >= _LAYOUTS['hand21']['min_kp']:
        return 'hand21' if n_kps >= 21 else 'coco17'
    return 'coco17'


def _layout(n_kps):
    """Раскладка с учётом POSE_KPT_LAYOUT ('auto' — по числу точек)."""
    name = POSE_KPT_LAYOUT if POSE_KPT_LAYOUT in _LAYOUTS else 'auto'
    if name == 'auto':
        name = detect_layout(n_kps)
    return _LAYOUTS.get(name, _LAYOUTS['coco17'])


def palm_target(kps, wrist_i, elbow_i, extend=POSE_PALM_EXTEND):
    """Центр ладони по COCO-раскладке: запястье + extend доли вектора
    «локоть -> запястье» (ладонь ≈ треть предплечья от запястья).
    Без уверенного локтя — само запястье (лучше, чем прыжок в пустоту)."""
    wx, wy, _wc = kps[wrist_i]
    if elbow_i is not None and elbow_i < len(kps):
        ex, ey, ec = kps[elbow_i]
        if ec >= POSE_KP_CONF and (ex != wx or ey != wy):
            return (wx + (wx - ex) * extend, wy + (wy - ey) * extend)
    return float(wx), float(wy)


def palm_point(kps, lay):
    """Цель слежения (центр ладони) для раскладки lay.

    Возвращает (x, y) или None, если ни одна опорная точка не уверена."""
    if lay['palm_mcps']:
        pts = [kps[i] for i in lay['palm_mcps'] if i < len(kps)
               and kps[i][2] >= POSE_KP_CONF]
        if pts:
            n = float(len(pts))
            return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
        # MCP не видны — хотя бы запястье
        w = lay['arms'][0][1]
        return (float(kps[w][0]), float(kps[w][1])) if kps[w][2] >= POSE_KP_CONF \
            else None
    best = None
    for _side, w_i, e_i in lay['arms']:
        if kps[w_i][2] < POSE_KP_CONF:
            continue
        px, py = palm_target(kps, w_i, e_i)
        conf = kps[w_i][2]
        if best is None or conf > best[2]:
            best = (px, py, conf)
    return (best[0], best[1]) if best else None



def pick_front_hand(persons, kp_conf=POSE_KP_CONF, prefer_raised=POSE_PREFER_RAISED):
    """Выбрать самую переднюю руку среди найденных людей.

    persons — список dict из PoseDetector.infer(). Возвращает dict
    {'x','y','wrist','side','conf','scale','raised','person'} или None.
    Цель (x, y) — ЦЕНТР ЛАДОНИ (v1.6.13), а не запястье: для coco17 — точка,
    продленная от запястья по направлению «локоть -> запястье» на
    POSE_PALM_EXTEND долю предплечья (без уверенного локтя — запястье);
    для раскладки hand21 (дообученная модель кисти) — среднее MCP-точек.
    Выбор детерминирован: сначала масштаб человека (ближе = больше), затем
    «поднятая» рука, затем уверенность точки.
    """
    best, best_key = None, None
    for pi, p in enumerate(persons):
        kps = p['kps']
        lay = _layout(len(kps))
        scale = float(p['box'][3])                  # высота рамки человека, px
        for side, w_i, e_i in lay['arms']:
            wx, wy, wc = kps[w_i]
            if wc < kp_conf:
                continue
            ex, ey, ec = (kps[e_i] if e_i is not None and e_i < len(kps)
                          else (0.0, 0.0, 0.0))
            raised = bool(ec >= kp_conf and wy < ey)
            key = (round(scale / 8.0),               # грубое квантование: два человека
                   1 if (raised and prefer_raised) else 0,   # примерно одной дальности
                   wc)                                # сравниваются по поднятой руке
            if best_key is None or key > best_key:
                best_key = key
                if lay['palm_mcps']:
                    px, py = palm_point(kps, lay) or (float(wx), float(wy))
                else:
                    px, py = palm_target(kps, w_i, e_i)
                best = {'x': float(px), 'y': float(py),
                        'wrist': (float(wx), float(wy)),
                        'side': side,
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
        """Ленивая загрузка при первом включении режима Hand.

        Как и у YOLO-детектора (v1.6.12): кандидаты по порядку, вход
        проверяется probe-прогоном нулевого блоба POSE_INPUT_SIZE — модель
        с другим входом отбрасывается в пользу следующего кандидата, чтобы
        инференс не падал с 'Blob dimensions do not match'.
        """
        if self._net is not None or not _HAS_CV2:
            return self.loaded
        found = False
        for path in (POSE_MODEL_PATH, POSE_MODEL_BUNDLED):
            if not path or not os.path.isfile(path):
                continue
            try:
                net = cv2.dnn.readNetFromONNX(path)
                probe = np.zeros(
                    (1, 3, POSE_INPUT_SIZE, POSE_INPUT_SIZE),
                    dtype=np.float32)
                net.setInput(probe)
                net.forward()
            except Exception as e:
                logger.warning(f'[Pose] {path} не подходит: {e}')
                continue
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self._net = net
            self.model_path = path
            logger.info(f'[Pose] loaded {path} (input {self._size}x{self._size})')
            found = True
            break
        if not found:
            logger.warning(
                '[Pose] model not found — положите yolo11n-pose.onnx '
                f'(вход {POSE_INPUT_SIZE}) в /home/pi/models '
                '(или запустите setup.py), см. docs/POSE_GUIDE.md')
        return self.loaded

    def should_infer(self):
        """Инференс каждый POSE_FRAME_SKIP-й кадр (разгрузка CPU)."""
        self._frame_counter += 1
        return self._frame_counter % max(1, POSE_FRAME_SKIP) == 0

    # ---- инференс ---------------------------------------------------------
    def infer(self, frame_rgb, skin_bounds=None):
        """Найти людей и самую переднюю руку. Кадр — RGB, как у камеры.

        skin_bounds — (low, high) HSV-границы кожи (от CVThread) для
        привязки цели к реальному пятну ладони (POSE_PALM_SNAP, v1.6.13).
        """
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
        if hand and skin_bounds is not None and POSE_PALM_SNAP:
            hand = self._snap_to_skin(frame_rgb, hand, skin_bounds)
        self.last_infer_ms = (time.monotonic() - t0) * 1000.0
        self._last_infer_ts = time.time()
        with self._lock:
            self.persons = persons
            self.hand = hand
        return persons

    def _snap_to_skin(self, frame_rgb, hand, bounds):
        """Привязать расчётный центр ладони к реальному HSV-пятну кожи.

        В малом квадрате вокруг геометрической точки ищется самый большой
        «кожаный» блоб; если найден — целью становится его центр масс
        (слежение не уплывает на рукав и не отстаёт от поворота кисти).
        """
        if not _HAS_CV2 or hand is None:
            return hand
        low, high = bounds
        x, y = hand['x'], hand['y']
        # радиус поиска — от длины предплечья, но в разумных пределах
        wx, wy = hand.get('wrist', (x, y))
        fore = math.hypot(x - wx, y - wy) / max(POSE_PALM_EXTEND, 1e-3) \
            if POSE_PALM_EXTEND else 0.0
        r = int(max(12.0, min(48.0, fore * 0.45)))
        h0, w0 = frame_rgb.shape[:2]
        x0, x1 = max(0, int(x) - r), min(w0, int(x) + r)
        y0, y1 = max(0, int(y) - r), min(h0, int(y) + r)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return hand
        crop = frame_rgb[y0:y1, x0:x1]
        mask = vision.hsv_mask(crop, np.asarray(low, dtype=np.uint8),
                               np.asarray(high, dtype=np.uint8))
        blob = vision.largest_blob(mask, POSE_PALM_SNAP_MIN_AREA)
        if blob is None:
            return hand
        cx, cy = blob[0] + x0, blob[1] + y0
        snapped = dict(hand)
        snapped['x'] = float(cx)
        snapped['y'] = float(cy)
        snapped['palm_geo'] = (float(x), float(y))
        return snapped

    @staticmethod
    def _decode(out, r, dx, dy, w0, h0, conf_thr=POSE_CONF, nms_thr=POSE_NMS):
        """Выход Ultralytics-pose ONNX -> список людей в пикселях кадра.

        Раскладка определяется автоматически (v1.6.13): (1, C, N) или
        (N, C), C = 5 + K*3 — 4 координаты рамки, conf человека и K точек
        (x, y, conf). K=17 — человек COCO; K=21 — дообученная модель кисти
        (hand21). Прежний код жёстко требовал 56 столбцов и падал на любой
        дообученной модели с другим числом точек.
        """
        a = np.asarray(out)
        if a.ndim == 3:
            a = a[0]
        # ориентация выхода: столбцы = признаки (N, C), C = 5 + K*3.
        # Признаковое измерение мало и делится на схему «5 + 3K», anchors
        # (N) — тысячи строк: определяем по обоим осям.
        if a.ndim != 2:
            return []
        c0 = (a.shape[0] - 5) % 3 == 0 and a.shape[0] >= 8
        c1 = (a.shape[1] - 5) % 3 == 0 and a.shape[1] >= 8
        if c0 and c1:
            # обе оси формально валидны (мелкий вход): признаки — на МЕНЬШЕЙ
            # оси (anchors всегда тысячи), иначе это (N, C)
            if a.shape[0] < a.shape[1]:
                a = a.T
        elif c0 and not c1:
            a = a.T                                   # (C, N) -> (N, C)
        elif not c0 and not c1:
            return []
        # c1 без c0 — уже (N, C)
        if a.ndim != 2 or (a.shape[1] - 5) % 3 != 0 or a.shape[1] < 8:
            return []
        n_kp = (a.shape[1] - 5) // 3
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
            kp = row[5:5 + n_kp * 3].reshape(n_kp, 3).copy()
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
        """Скелет рук, рамки людей и метка выбранной ЛАДОНИ (на BGR-кадре).

        v1.6.13: маркер цели — центр ладони (hand['x','y']), запястье —
        маленькая точка. «Кости» плечо-локоть-запястье рисуются только для
        COCO-раскладки (у дообученной модели кисти индексы другие)."""
        if not _HAS_CV2:
            return disp_bgr
        for pi, p in enumerate(persons):
            x, y, bw, bh = [int(v) for v in p['box']]
            tracked = hand is not None and hand['person'] == pi
            cv2.rectangle(disp_bgr, (x, y), (x + int(bw), y + int(bh)),
                          (0, 255, 255) if tracked else (120, 120, 120), 1)
            if len(p['kps']) >= 11:                    # coco17: скелет руки
                for a, b in ARM_BONES:
                    xa, ya, ca = p['kps'][a]
                    xb, yb, cb = p['kps'][b]
                    if ca >= POSE_KP_CONF and cb >= POSE_KP_CONF:
                        cv2.line(disp_bgr, (int(xa), int(ya)), (int(xb), int(yb)),
                                 (255, 200, 0), 2)
            for w_i in (KP_L_WRIST, KP_R_WRIST):
                if w_i >= len(p['kps']):
                    continue
                xw, yw, cw = p['kps'][w_i]
                if cw >= POSE_KP_CONF:
                    cv2.circle(disp_bgr, (int(xw), int(yw)), 5, (255, 200, 0), -1)
        if hand is not None:
            hx, hy = int(hand['x']), int(hand['y'])
            cv2.circle(disp_bgr, (hx, hy), 4, (0, 0, 255), -1)   # центр ладони
            cv2.circle(disp_bgr, (hx, hy), 20, (0, 0, 255), 2)
            cv2.line(disp_bgr, (hx - 28, hy), (hx + 28, hy), (0, 0, 255), 2)
            cv2.line(disp_bgr, (hx, hy - 28), (hx, hy + 28), (0, 0, 255), 2)
            wx, wy = hand.get('wrist', (hx, hy))
            cv2.circle(disp_bgr, (int(wx), int(wy)), 3, (255, 200, 0), -1)
            cv2.putText(disp_bgr,
                        f"PALM {hand['side']} ({hx},{hy})", (10, 30),
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
                      'side': hand['side'],
                      'palm': True} if hand else None),
            'infer_ms': round(self.last_infer_ms, 1),
        }
