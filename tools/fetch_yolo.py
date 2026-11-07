#!/usr/bin/env python3
"""Подготовка YOLO для робота ОДНОЙ командой (запускать на ПК, не на Pi):

    pip install ultralytics
    python3 tools/fetch_yolo.py                       # yolov8n, вход 320 -> ./models/
    python3 tools/fetch_yolo.py --scp pi@10.42.0.1    # и сразу скопировать на робота

Скачивает официальные веса (.pt), экспортирует в ONNX (opset 12, без
динамических осей — как ждёт cv2.dnn), пишет classes.txt. Готовую пару
.onnx + classes.txt можно также положить на Pi вручную: setup.py подхватит
их из YOLO_MODEL_PATH (см. config.py).
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yolo_common import COCO80, WEIGHTS_URL, download_file, write_classes  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default='yolov8n', help='yolov8n | yolov8s ... (по умолчанию yolov8n)')
    ap.add_argument('--imgsz', type=int, default=320, help='вход сети (320 — для Pi 3B+)')
    ap.add_argument('--out', default='models', help='куда сложить результат')
    ap.add_argument('--scp', default='', help='user@host — скопировать результат на Pi')
    ap.add_argument('--remote-dir', default='/home/pi/models', help='каталог на Pi для --scp')
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    pt = os.path.join(a.out, f'{a.model}.pt')
    if not os.path.exists(pt):
        print(f'Скачиваю веса {a.model}.pt ...')
        if not download_file(WEIGHTS_URL.format(name=a.model), pt):
            sys.exit('Не удалось скачать веса (нет интернета?)')
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit('Нужен ultralytics: pip install ultralytics (только на ПК!)')
    model = YOLO(pt)
    model.export(format='onnx', imgsz=a.imgsz, simplify=True, opset=12,
                 dynamic=False, half=False)
    onnx = os.path.splitext(pt)[0] + '.onnx'
    names = [model.names.get(i, f'class{i}') for i in range(max(model.names) + 1)]
    write_classes(os.path.join(a.out, 'classes.txt'), names or COCO80)
    print(f'OK: {onnx}, {os.path.join(a.out, "classes.txt")}')

    if a.scp:
        subprocess.run(['ssh', a.scp, f'mkdir -p {a.remote_dir}'], check=False)
        r = subprocess.run(['scp', onnx, os.path.join(a.out, 'classes.txt'),
                            f'{a.scp}:{a.remote_dir}/'])
        print('Скопировано на робота.' if r.returncode == 0 else 'scp не удался — скопируйте файлы вручную.')


if __name__ == '__main__':
    main()
