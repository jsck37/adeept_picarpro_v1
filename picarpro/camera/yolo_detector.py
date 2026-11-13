"""YOLO-детектор/сегментатор на OpenCV DNN — лёгкий инференс для Raspberry Pi.

Ключевые решения под 1 ГБ RAM (см. docs/MEMORY_TUNING.md):
* НЕТ PyTorch/ultralytics в рантайме: модель один раз экспортируется в ONNX
  (tools/export_yolo_onnx.py) и загружается через cv2.dnn — экономия ~700 МБ;
* вход 640x640 (YOLO_INPUT_SIZE) — как у официального ONNX релиза v7.0;
  замер: детекция ~150 МБ пик RSS, сегментация ~190 МБ — в 500 МБ свободной
  ОЗУ укладываются;
* инференс выполняется в отдельном потоке CVThread (camera_opencv) и только
  для каждого YOLO_FRAME_SKIP-го кадра — поток видео (MJPEG) не блокируется;
* клон frame не создаётся на каждый кадр: передаётся уже скопированный кадр.

v1.6.6: СЕГМЕНТАЦИЯ (YOLO_TASK='seg'). Модель yolov5n-seg имеет ДВА выхода:
output0 (1, 25200, 117) — детекции + 32 маски-коэффициента, output1
(1, 32, 160, 160) — proto-маски. Маска объекта = sigmoid(proto @ коэф),
обрезается по bbox и растягивается в кадр (схема YOLACT, как в ultralytics).
Маски рисуются на видео полупрозрачным цветом по классу; в статус уходят
только счётчики площади (не сами маски).

Поддержка: YOLOv5n(-seg) / YOLOv8n (детекции; любые YOLO с ONNX-экспортом
opset 12, без динамических осей — см. tools/fetch_yolo.py).
"""
import os
import time

from picarpro.logger import logger
from config import (
    YOLO_MODEL_PATH, YOLO_INPUT_SIZE, YOLO_CONFIDENCE, YOLO_NMS_THRESHOLD,
    YOLO_FRAME_SKIP, YOLO_CLASSES_FILE,
    YOLO_TASK, YOLO_MODEL_BUNDLED,
    YOLO_SEG_MODEL_PATH, YOLO_SEG_MODEL_BUNDLED,
    YOLO_MASK_THRESHOLD, YOLO_MAX_MASKS,
)

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False
    cv2 = None
    np = None

# BGR-палитра оверлея масок (по class_id, циклически)
_MASK_PALETTE = (
    (60, 76, 231),    # red
    (60, 180, 75),    # green
    (230, 120, 20),   # blue-ish
    (180, 229, 255),  # yellow
    (180, 105, 255),  # magenta
    (102, 217, 239),  # amber
    (0, 150, 128),    # olive
    (239, 114, 85),   # sky
    (85, 42, 128),    # purple
    (190, 190, 0),    # navy-ish
)


def _load_classes(path):
    if path and os.path.isfile(path):
        try:
            with open(path) as f:
                return [ln.strip() for ln in f if ln.strip()]
        except Exception:
            pass
    return []


class YoloDetector:
    """Обёртка cv2.dnn вокруг YOLO ONNX (детекции и сегментация)."""

    def __init__(self):
        self._net = None
        self._classes = _load_classes(YOLO_CLASSES_FILE)
        self._frame_counter = 0
        self._last_infer_ts = 0.0
        self.last_infer_ms = 0.0
        self.objects = []          # [{'label','conf','box':[x,y,w,h],'cx','cy','area'[,'mask','mask_area','mask_pct']}]
        self._objects_lock = None  # threading.Lock присвоит camera_opencv
        self._out_names = []       # >1 выход => seg-модель
        self._seg = False
        self.task = 'detect'

    @property
    def loaded(self):
        return self._net is not None

    def _model_path(self):
        """Путь к ONNX: сначала /home/pi/models, затем модели из архива
        прошивки (models/). YOLO_TASK='seg' — seg-модель с откатом на
        детекционную, если сегментационной нет."""
        candidates = []
        if YOLO_TASK == 'seg':
            candidates += [('seg', YOLO_SEG_MODEL_PATH),
                           ('seg', YOLO_SEG_MODEL_BUNDLED)]
        candidates += [('detect', YOLO_MODEL_PATH),
                       ('detect', YOLO_MODEL_BUNDLED)]
        for task, path in candidates:
            if path and os.path.isfile(path):
                return task, path
        return None, None

    def load(self):
        """Ленивая загрузка модели (вызывается при первом включении режима)."""
        if self._net is not None or not _HAS_CV2:
            return self.loaded
        task, path = self._model_path()
        if not path:
            logger.warning(
                '[YOLO] model not found — положите yolov5n-seg.onnx / '
                'yolov5n.onnx в /home/pi/models (или запустите setup.py), '
                'см. docs/YOLO_GUIDE.md')
            return False
        try:
            self._net = cv2.dnn.readNetFromONNX(path)
            self._out_names = self._net.getUnconnectedOutLayersNames()
            self._seg = len(self._out_names) > 1      # + proto-выход
            self.task = 'seg' if self._seg else 'detect'
            # на RPi выгоднее распределять по ядрам CPU, без OpenVINO
            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            logger.info(
                f'[YOLO] loaded {path} '
                f'(task {self.task}, input {YOLO_INPUT_SIZE}x{YOLO_INPUT_SIZE}, '
                f'{len(self._classes)} classes)')
        except Exception as e:
            logger.error(f'[YOLO] load failed: {e}')
            self._net = None
            self._seg = False
            self.task = 'detect'
        return self.loaded

    def should_infer(self):
        """Пропуск кадров: инференс только каждый YOLO_FRAME_SKIP-й кадр."""
        self._frame_counter += 1
        if self._frame_counter < YOLO_FRAME_SKIP:
            return False
        self._frame_counter = 0
        return True

    def infer(self, frame_rgb):
        """Прогнать кадр (RGB, любой размер) через YOLO и обновить objects."""
        if not self.loaded or not _HAS_CV2:
            return self.objects
        t0 = time.time()
        h, w = frame_rgb.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame_rgb, 1.0 / 255.0, (YOLO_INPUT_SIZE, YOLO_INPUT_SIZE),
            swapRB=False, crop=False)   # кадр уже RGB (см. camera_opencv)
        try:
            self._net.setInput(blob)
            if self._seg:
                outs = self._net.forward(self._out_names)
                det, proto = outs[0], outs[1]
            else:
                det = self._net.forward()
                proto = None
        except Exception as e:
            logger.error(f'[YOLO] inference failed: {e}')
            return self.objects
        objs = self._parse_output(det, w, h, proto=proto)
        self.last_infer_ms = (time.time() - t0) * 1000.0
        self._last_infer_ts = time.time()
        if self._objects_lock is not None:
            with self._objects_lock:
                self.objects = objs
        else:
            self.objects = objs
        return objs

    def _parse_output(self, out, orig_w, orig_h, proto=None):
        """Разбор выхода ultralytics-YOLO ONNX: (1, 84, N) [v8-детекции],
        (1, N, 85) [v5] или (1, N, 117) [v5-seg: +32 маски-коэффициента].

        proto — (1, 32, 160, 160) от seg-модели; для каждого найденного
        объекта строится маска кадра (bool) + счётчики площади."""
        if out is None or len(out) == 0:
            return []
        pred = np.squeeze(out[0])  # (84, N) [v8/v11] или (N, 85/117) [v5]
        channels_first = pred.shape[0] < pred.shape[1]
        if channels_first:
            pred = pred.T          # -> (N, C)
        # v5-seg: 4+1+80+32 = 117 столбцов (channels last);
        # v8-seg: 4+80+32 = 116 столбцов (channels first)
        seg = (not channels_first and pred.shape[1] > 100) or \
              (channels_first and pred.shape[1] >= 116)
        boxes_xywh = pred[:, 0:4]
        if channels_first:
            # YOLOv8/v11: [cx, cy, w, h, класс_0 ... класс_79] — столбца
            # objectness НЕТ. Прежний код принимал оценку класса 0 за
            # objectness и умножал её на максимум остальных — на стандартной
            # модели yolov8n.onnx выдавалась ерунда.
            if seg:
                cls_scores = pred[:, 4:84]        # v8-seg: +32 коэф с 84
            else:
                cls_scores = pred[:, 4:]
            scores = cls_scores.max(axis=1)
            class_ids = cls_scores.argmax(axis=1)
        else:
            # YOLOv5: [cx, cy, w, h, objectness, класс_0 ... (, маски_0..31)]
            obj_conf = pred[:, 4]
            cls_scores = pred[:, 5:85] if seg else pred[:, 5:]
            if cls_scores.shape[1] > 0:
                scores = obj_conf * cls_scores.max(axis=1)
                class_ids = cls_scores.argmax(axis=1)
            else:
                scores = obj_conf
                class_ids = np.zeros(len(pred), dtype=int)

        mask = scores >= YOLO_CONFIDENCE
        if not mask.any():
            return []
        boxes_xywh = boxes_xywh[mask]
        scores = scores[mask]
        class_ids = class_ids[mask]
        coef_col = 84 if channels_first else 85
        coefs = pred[mask, coef_col:coef_col + 32] if seg else None

        # cxcywh -> xywh: отдельно в пикселях СЕТИ (для масок) и исходного
        # кадра (для рамок; blob растягивает кадр — оси масштабируются сами)
        net_w = net_h = float(YOLO_INPUT_SIZE)
        scale_x = orig_w / net_w
        scale_y = orig_h / net_h
        boxes_net = np.empty((len(boxes_xywh), 4), dtype=np.float32)
        boxes_net[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
        boxes_net[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
        boxes_net[:, 2] = boxes_xywh[:, 2]
        boxes_net[:, 3] = boxes_xywh[:, 3]
        boxes = np.empty((len(boxes_xywh), 4), dtype=np.float32)
        boxes[:, 0] = boxes_net[:, 0] * scale_x
        boxes[:, 1] = boxes_net[:, 1] * scale_y
        boxes[:, 2] = boxes_net[:, 2] * scale_x
        boxes[:, 3] = boxes_net[:, 3] * scale_y

        indices = cv2.dnn.NMSBoxes(
            boxes.tolist(), scores.tolist(),
            YOLO_CONFIDENCE, YOLO_NMS_THRESHOLD)
        objs = []
        n_masks = 0
        if indices is not None and len(indices) > 0:
            frame_area = float(orig_w * orig_h)
            for i in np.array(indices).flatten():
                x, y, bw, bh = [int(v) for v in boxes[i]]
                conf = float(scores[i])
                cid = int(class_ids[i])
                label = (self._classes[cid]
                         if 0 <= cid < len(self._classes) else f'id{cid}')
                obj = {
                    'label': label, 'conf': round(conf, 2),
                    'box': [x, y, bw, bh],
                    'cx': x + bw // 2, 'cy': y + bh // 2,
                    'area': bw * bh,
                }
                if seg and proto is not None and n_masks < YOLO_MAX_MASKS:
                    m = self._build_mask(proto, coefs[i], boxes_net[i],
                                         orig_w, orig_h)
                    if m is not None:
                        obj['mask'] = m
                        obj['mask_area'] = int(m.sum())
                        obj['mask_pct'] = round(100.0 * obj['mask_area']
                                                / frame_area, 1)
                        n_masks += 1
                objs.append(obj)
        objs.sort(key=lambda o: -o['area'])
        return objs

    @staticmethod
    def _build_mask(proto, coef, box_net, orig_w, orig_h):
        """Маска одного объекта: sigmoid(proto @ коэф), crop по bbox,
        растяжка в пиксели кадра. None, если маска пустая."""
        if proto is None or len(proto) == 0:
            return None
        p = proto[0]                       # (32, ph, pw)
        ph, pw = p.shape[1], p.shape[2]
        p = p.reshape(32, -1).T            # (ph*pw, 32)
        m = 1.0 / (1.0 + np.exp(-(p @ coef.astype(np.float32))))
        m = m.reshape(ph, pw) > YOLO_MASK_THRESHOLD   # строго, как в ultralytics
        # bbox из сети (640) -> proto (160): масштаб proto/INPUT
        psx, psy = pw / float(YOLO_INPUT_SIZE), ph / float(YOLO_INPUT_SIZE)
        nx, ny, nw, nh = box_net
        mx0 = max(0, int(nx * psx))
        my0 = max(0, int(ny * psy))
        mx1 = min(pw, int((nx + nw) * psx) + 1)
        my1 = min(ph, int((ny + nh) * psy) + 1)
        if mx1 <= mx0 or my1 <= my0:
            return None
        sub = m[my0:my1, mx0:mx1]
        if not sub.any():
            return None
        if nw <= 0 or nh <= 0:
            return None
        # растяжка в размер bbox на исходном кадре (blob растягивал кадр)
        sx, sy = orig_w / float(YOLO_INPUT_SIZE), orig_h / float(YOLO_INPUT_SIZE)
        big = cv2.resize(sub.astype(np.uint8),
                         (max(1, int(nw * sx)), max(1, int(nh * sy))),
                         interpolation=cv2.INTER_NEAREST).astype(bool)
        ox = max(0, int(nx * sx))
        oy = max(0, int(ny * sy))
        ox1 = min(orig_w, ox + big.shape[1])
        oy1 = min(orig_h, oy + big.shape[0])
        if ox1 <= ox or oy1 <= oy:
            return None
        mask = np.zeros((orig_h, orig_w), dtype=bool)
        mask[oy:oy1, ox:ox1] = big[:oy1 - oy, :ox1 - ox]
        return mask

    def draw(self, disp_bgr, objs, color=(0, 255, 255)):
        """Нарисовать маски (полупрозрачно), рамки и подписи на кадре (BGR)."""
        if not _HAS_CV2:
            return disp_bgr
        for o in objs:
            m = o.get('mask')
            if m is not None and m.any():
                cid = self._classes.index(o['label']) \
                    if o['label'] in self._classes else 0
                col = _MASK_PALETTE[cid % len(_MASK_PALETTE)]
                disp_bgr[m] = (disp_bgr[m] * 0.5
                               + np.array(col, dtype=np.float32) * 0.5
                               ).astype(disp_bgr.dtype)
        for o in objs:
            x, y, bw, bh = o['box']
            cv2.rectangle(disp_bgr, (x, y), (x + bw, y + bh), color, 2)
            text = f"{o['label']} {o['conf']:.2f}"
            ty = max(y - 6, 14)
            cv2.putText(disp_bgr, text, (x, ty),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        return disp_bgr

    def get_status(self):
        with (self._objects_lock if self._objects_lock is not None
              else _NullCtx()):
            objs = list(self.objects)
        out = [{
            'label': o['label'], 'conf': o['conf'], 'box': o['box'],
        } for o in objs[:8]]
        for d, o in zip(out, objs[:8]):
            if 'mask_area' in o:
                d['mask_area'] = o['mask_area']
                d['mask_pct'] = o.get('mask_pct')
        return {
            'loaded': self.loaded,
            'task': self.task,
            'objects': out,
            'infer_ms': round(self.last_infer_ms, 1),
            'age_s': round(time.time() - self._last_infer_ts, 1)
            if self._last_infer_ts else None,
        }


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False
