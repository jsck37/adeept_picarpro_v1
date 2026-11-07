"""Общее для setup.py и tools/fetch_yolo.py: имена классов COCO и загрузка файла."""
import hashlib
import os
import time
import urllib.request

COCO80 = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck',
    'boat', 'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench',
    'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra',
    'giraffe', 'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee',
    'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat', 'baseball glove',
    'skateboard', 'surfboard', 'tennis racket', 'bottle', 'wine glass', 'cup',
    'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich', 'orange',
    'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
    'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse',
    'remote', 'keyboard', 'cell phone', 'microwave', 'oven', 'toaster', 'sink',
    'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear',
    'hair drier', 'toothbrush',
]

# Официальные веса Ultralytics (ONNX в релизах не публикуется — его делают
# экспортом из .pt, см. tools/fetch_yolo.py).
WEIGHTS_URL = 'https://github.com/ultralytics/assets/releases/download/v8.3.0/{name}.pt'


def write_classes(path, names=None):
    names = names or COCO80
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(names) + '\n')


def download_file(url, dest, sha256=None, retries=3, timeout=30, progress=None):
    """Скачать url в dest: через .part, с повторами и проверкой SHA-256.
    Возвращает True/False; при неудаче частичный файл удаляется."""
    part = dest + '.part'
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    for attempt in range(1, retries + 1):
        try:
            h = hashlib.sha256()
            req = urllib.request.Request(url, headers={'User-Agent': 'picarpro-setup'})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(part, 'wb') as f:
                total = int(r.headers.get('Content-Length') or 0)
                done = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            if sha256 and h.hexdigest().lower() != sha256.lower():
                raise ValueError('SHA-256 не совпал')
            os.replace(part, dest)
            return True
        except Exception as e:
            if os.path.exists(part):
                os.remove(part)
            if attempt == retries:
                print(f'  ! загрузка не удалась: {e}')
                return False
            time.sleep(2 * attempt)
    return False
