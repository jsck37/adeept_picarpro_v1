"""v1.6.6: сегментация YOLO — парс (N,117) v5-seg и (N,116) v8-seg, маски
(proto @ коэф), статус без матриц, оверлей, выбор модели seg/detect.

Тестовая среда подменяет cv2 на FakeCV2 (без dnn), поэтому здесь cv2
заменяется на numpy-стаб (NMS, nearest-resize); реальная seg-модель из
архива прошивки проверяется отдельным смоуком (scripts/).
"""
import types

import numpy as np
import pytest


def _stub_resize(src, dsize, interpolation=None):
    """Nearest-neighbor resize (dsize=(w,h)) — как cv2.resize для масок."""
    h, w = src.shape[:2]
    ys = (np.arange(dsize[1]) * (h / max(1, dsize[1]))).astype(int)
    xs = (np.arange(dsize[0]) * (w / max(1, dsize[0]))).astype(int)
    return src[ys][:, xs]


def _stub_nms(boxes, scores, score_th, nms_th):
    """NMS как cv2.dnn.NMSBoxes: индексы выживших, shape (k, 1)."""
    b = np.asarray(boxes, dtype=np.float32)
    s = np.asarray(scores, dtype=np.float32)
    keep = s >= score_th
    b, s = b[keep], s[keep]
    order = s.argsort()[::-1]
    kept = []
    while order.size:
        i = order[0]
        kept.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(b[i, 0], b[rest, 0])
        yy1 = np.maximum(b[i, 1], b[rest, 1])
        xx2 = np.minimum(b[i, 0] + b[i, 2], b[rest, 0] + b[rest, 2])
        yy2 = np.minimum(b[i, 1] + b[i, 3], b[rest, 1] + b[rest, 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        union = b[i, 2] * b[i, 3] + b[rest, 2] * b[rest, 3] - inter
        iou = inter / np.maximum(union, 1e-9)
        order = rest[iou <= nms_th]
    if not kept:
        return []
    return np.array(kept, dtype=int).reshape(-1, 1)


def _stub_cv2():
    return types.SimpleNamespace(
        dnn=types.SimpleNamespace(NMSBoxes=staticmethod(_stub_nms)),
        resize=_stub_resize,
        INTER_NEAREST=0,
        FONT_HERSHEY_SIMPLEX=0,
        rectangle=lambda *a, **k: None,
        putText=lambda *a, **k: None,
    )


def _make_detector(monkeypatch, task='seg', seg_path=None):
    import picarpro.camera.yolo_detector as yd
    monkeypatch.setattr(yd, 'YOLO_TASK', task)
    monkeypatch.setattr(yd, 'YOLO_SEG_MODEL_PATH', seg_path or '/nonexistent')
    monkeypatch.setattr(yd, 'YOLO_SEG_MODEL_BUNDLED', seg_path or '/nonexistent')
    monkeypatch.setattr(yd, 'YOLO_MODEL_PATH', '/nonexistent')
    monkeypatch.setattr(yd, 'YOLO_MODEL_BUNDLED', '/nonexistent')
    monkeypatch.setattr(yd, 'cv2', _stub_cv2())
    return yd, yd.YoloDetector()


def _seg_output_v5():
    """(1, N, 117): два объекта v5-seg. coef[0]=3 и proto-канал 0 = +3 ->
    маска у первого; coef[1]=3 при нулевом канале 1 -> маска пустая."""
    out = np.zeros((1, 25200, 117), dtype=np.float32)
    r = out[0, 1000]
    r[0:4] = (320, 320, 100, 100)          # cx,cy,w,h в сети 640
    r[4] = 0.9
    r[5] = 0.8
    r[85] = 3.0                            # coef 0
    r = out[0, 2000]
    r[0:4] = (100, 100, 80, 80)
    r[4] = 0.9
    r[5] = 0.8
    r[86] = 3.0                            # coef 1
    return out


def _proto():
    p = np.zeros((1, 32, 160, 160), dtype=np.float32)
    p[0, 0] = 3.0                          # канал 0 -> sigmoid ~0.95 везде
    return p


class TestSegParse:
    def test_v5_seg_masks(self, monkeypatch):
        yd, det = _make_detector(monkeypatch)
        objs = det._parse_output(_seg_output_v5(), 640, 480, proto=_proto())
        assert len(objs) == 2
        top = objs[0]
        assert top['mask_area'] == 100 * 75      # bbox 640->480 по оси y
        assert top['mask'].shape == (480, 640)
        assert top['mask'].dtype == np.dtype(bool)
        assert 0 < top['mask_pct'] <= 100
        # второй объект: proto-канал 1 нулевой -> маска пустая -> без 'mask'
        assert 'mask' not in objs[1]

    def test_mask_bbox_crop(self, monkeypatch):
        """Маска не выходит за bbox (proto-канал 0 везде выше порога)."""
        yd, det = _make_detector(monkeypatch)
        objs = det._parse_output(_seg_output_v5(), 640, 480, proto=_proto())
        m = objs[0]['mask']
        x, y, w, h = objs[0]['box']
        assert m[:max(0, y), :].sum() == 0
        assert m[y + h:, :].sum() == 0
        assert m[:, :max(0, x)].sum() == 0
        assert m[:, x + w:].sum() == 0
        assert m[y:y + h, x:x + w].sum() == w * h

    def test_detect_output_no_masks(self, monkeypatch):
        """Обычная v5-детекция (85 столбцов) — масок нет даже с proto."""
        yd, det = _make_detector(monkeypatch)
        out = np.zeros((1, 25200, 85), dtype=np.float32)
        out[0, 500, 0:4] = (320, 320, 100, 100)
        out[0, 500, 4] = 0.9
        out[0, 500, 5] = 0.8
        objs = det._parse_output(out, 640, 480, proto=_proto())
        assert objs and all('mask' not in o for o in objs)

    def test_v8_seg_masks(self, monkeypatch):
        """v8-seg: channels first, 116 столбцов (4+80+32), objectness нет."""
        yd, det = _make_detector(monkeypatch)
        out = np.zeros((1, 116, 25200), dtype=np.float32)
        out[0, 0:4, 500] = (320, 320, 100, 100)
        out[0, 4, 500] = 0.9                   # класс 0
        out[0, 84, 500] = 3.0                  # coef 0
        objs = det._parse_output(out, 640, 480, proto=_proto())
        assert len(objs) == 1
        assert objs[0]['mask_area'] == 100 * 75
        assert objs[0]['conf'] == pytest.approx(0.9)

    def test_status_strips_mask(self, monkeypatch):
        yd, det = _make_detector(monkeypatch)
        det.objects = det._parse_output(_seg_output_v5(), 640, 480,
                                        proto=_proto())
        st = det.get_status()
        o0 = st['objects'][0]
        assert 'mask' not in o0
        assert o0['mask_area'] > 0 and o0['mask_pct'] > 0
        assert st['objects'][1].get('mask_area') is None

    def test_draw_paints_mask(self, monkeypatch):
        yd, det = _make_detector(monkeypatch)
        objs = det._parse_output(_seg_output_v5(), 640, 480, proto=_proto())
        disp = np.zeros((480, 640, 3), np.uint8)
        det.draw(disp, objs)
        assert disp.any()
        x, y, w, h = objs[0]['box']
        assert disp[y + h // 2, x + w // 2].any()   # центр bbox окрашен


class TestLoad:
    def test_seg_missing_falls_back_to_detect(self, monkeypatch, tmp_path):
        yd, det = _make_detector(monkeypatch, task='seg')
        assert det._model_path() == (None, None)
        fake = tmp_path / 'yolov5n.onnx'
        fake.write_bytes(b'x')
        monkeypatch.setattr(yd, 'YOLO_MODEL_BUNDLED', str(fake))
        assert det._model_path() == ('detect', str(fake))

    def test_seg_path_preferred_for_seg_task(self, monkeypatch, tmp_path):
        yd, det = _make_detector(monkeypatch, task='seg')
        seg = tmp_path / 'yolov5n-seg.onnx'
        seg.write_bytes(b'x')
        det_seg = tmp_path / 'yolov5n.onnx'
        det_seg.write_bytes(b'x')
        monkeypatch.setattr(yd, 'YOLO_SEG_MODEL_BUNDLED', str(seg))
        monkeypatch.setattr(yd, 'YOLO_MODEL_BUNDLED', str(det_seg))
        assert det._model_path() == ('seg', str(seg))

    def test_detect_task_ignores_seg_model(self, monkeypatch, tmp_path):
        yd, det = _make_detector(monkeypatch, task='detect')
        seg = tmp_path / 'yolov5n-seg.onnx'
        seg.write_bytes(b'x')
        det_m = tmp_path / 'yolov5n.onnx'
        det_m.write_bytes(b'x')
        monkeypatch.setattr(yd, 'YOLO_SEG_MODEL_BUNDLED', str(seg))
        monkeypatch.setattr(yd, 'YOLO_MODEL_BUNDLED', str(det_m))
        assert det._model_path() == ('detect', str(det_m))
