"""YOLO-детектор на OpenCV DNN — лёгкий инференс для Raspberry Pi 3B+ (1 ГБ ОЗУ).

Ключевые решения под 1 ГБ RAM (см. docs/MEMORY_TUNING.md):
* НЕТ PyTorch/ultralytics в рантайме: модель один раз экспортируется в ONNX
  (tools/export_yolo_onnx.py) и загружается через cv2.dnn — экономия ~700 МБ;
* вход 320x320 (YOLO_INPUT_SIZE), а не 640 — в 4 раза меньше вычислений;
* инференс выполняется в отдельном потоке CVThread (camera_opencv) и только
  для каждого YOLO_FRAME_SKIP-го кадра — поток видео (MJPEG) не блокируется;
* клон frame не создаётся на каждый кадр: передаётся уже скопированный кадр.

Поддержка: YOLOv5n / YOLOv8n (и любые YOLO с ONNX-экспортом формата
images -> 1x3xHxW -> [boxes, scores] в парадигме ultralytics export).
"""
import os
import time
from picarpro.logger import logger
from config import (
    YOLO_MODEL_PATH, YOLO_INPUT_SIZE, YOLO_CONFIDENCE, YOLO_NMS_THRESHOLD,
    YOLO_FRAME_SKIP, YOLO_CLASSES_FILE,
)

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False
    cv2 = None
    np = None


def _load_classes(path):
    if path and os.path.isfile(path):
        try:
            with open(path) as f:
                return [ln.strip() for ln in f if ln.strip()]
        except Exception:
            pass
    return []


class YoloDetector:
    """Обёртка cv2.dnn вокруг YOLO ONNX. Потокобезопасна (используется
    только из CVThread, но dnn::Net сам по себе потокобезопасен на чтение)."""

    def __init__(self):
        self._net = None
        self._classes = _load_classes(YOLO_CLASSES_FILE)
        self._frame_counter = 0
        self._last_infer_ts = 0.0
        self.last_infer_ms = 0.0
        self.objects = []          # [{'label','conf','box':[x,y,w,h],'cx','cy','area'}]
        self._objects_lock = None  # threading.Lock присвоит camera_opencv

    @property
    def loaded(self):
        return self._net is not None

    def load(self):
        """Ленивая загрузка модели (вызывается при первом включении режима)."""
        if self._net is not None or not _HAS_CV2:
            return self.loaded
        if not YOLO_MODEL_PATH or not os.path.isfile(YOLO_MODEL_PATH):
            logger.warning(
                f'[YOLO] model not found: {YOLO_MODEL_PATH} — '
                'см. docs/YOLO_GUIDE.md (экспорт в ONNX)')
            return False
        try:
            self._net = cv2.dnn.readNetFromONNX(YOLO_MODEL_PATH)
            # на RPi выгоднее распределять по ядрам CPU, без OpenVINO
            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            logger.info(
                f'[YOLO] loaded {YOLO_MODEL_PATH} '
                f'(input {YOLO_INPUT_SIZE}x{YOLO_INPUT_SIZE}, '
                f'{len(self._classes)} classes)')
        except Exception as e:
            logger.error(f'[YOLO] load failed: {e}')
            self._net = None
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
            swapRB=True, crop=False)
        try:
            self._net.setInput(blob)
            out = self._net.forward()
        except Exception as e:
            logger.error(f'[YOLO] inference failed: {e}')
            return self.objects
        objs = self._parse_output(out, w, h)
        self.last_infer_ms = (time.time() - t0) * 1000.0
        self._last_infer_ts = time.time()
        if self._objects_lock is not None:
            with self._objects_lock:
                self.objects = objs
        else:
            self.objects = objs
        return objs

    def _parse_output(self, out, orig_w, orig_h):
        """Разбор выхода ultralytics-YOLO ONNX: (1, 84, N) или (1, N, 85)."""
        if out is None or len(out) == 0:
            return []
        pred = np.squeeze(out[0])  # (85, N) или (N, 85)
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T          # -> (N, 85)
        num_classes = pred.shape[1] - 5
        boxes_xywh = pred[:, 0:4]
        obj_conf = pred[:, 4]
        cls_scores = pred[:, 5:]
        scores = obj_conf * cls_scores.max(axis=1) if num_classes > 0 else obj_conf
        class_ids = (cls_scores.argmax(axis=1) if num_classes > 0
                     else np.zeros(len(pred), dtype=int))

        mask = scores >= YOLO_CONFIDENCE
        if not mask.any():
            return []
        boxes_xywh = boxes_xywh[mask]
        scores = scores[mask]
        class_ids = class_ids[mask]

        # cxcywh -> xywh в пикселях исходного кадра
        scale_x = orig_w / float(YOLO_INPUT_SIZE)
        scale_y = orig_h / float(YOLO_INPUT_SIZE)
        boxes = np.empty((len(boxes_xywh), 4), dtype=np.float32)
        boxes[:, 0] = (boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2) * scale_x
        boxes[:, 1] = (boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2) * scale_y
        boxes[:, 2] = boxes_xywh[:, 2] * scale_x
        boxes[:, 3] = boxes_xywh[:, 3] * scale_y

        indices = cv2.dnn.NMSBoxes(
            boxes.tolist(), scores.tolist(),
            YOLO_CONFIDENCE, YOLO_NMS_THRESHOLD)
        objs = []
        if indices is not None and len(indices) > 0:
            for i in np.array(indices).flatten():
                x, y, bw, bh = [int(v) for v in boxes[i]]
                conf = float(scores[i])
                cid = int(class_ids[i])
                label = (self._classes[cid]
                         if 0 <= cid < len(self._classes) else f'id{cid}')
                objs.append({
                    'label': label, 'conf': round(conf, 2),
                    'box': [x, y, bw, bh],
                    'cx': x + bw // 2, 'cy': y + bh // 2,
                    'area': bw * bh,
                })
        objs.sort(key=lambda o: -o['area'])
        return objs

    def draw(self, disp_bgr, objs, color=(0, 255, 255)):
        """Нарисовать рамки и подписи на дисплейном кадре (BGR)."""
        if not _HAS_CV2:
            return disp_bgr
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
        return {
            'loaded': self.loaded,
            'objects': [{'label': o['label'], 'conf': o['conf'],
                         'box': o['box']} for o in objs[:8]],
            'infer_ms': round(self.last_infer_ms, 1),
            'age_s': round(time.time() - self._last_infer_ts, 1)
            if self._last_infer_ts else None,
        }


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False
