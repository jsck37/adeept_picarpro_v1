#!/usr/bin/env python3
"""Экспорт YOLO (.pt, ultralytics) в ONNX для инференса на RPi 3B+ через cv2.dnn.

Запускается НА ПК (не на Pi — torch на 1 ГБ RAM не влезет):

    pip install ultralytics
    python3 tools/export_yolo_onnx.py yolov8n.pt 320
    # или свою дообученную модель:
    python3 tools/export_yolo_onnx.py best.pt 320

Результат: <имя>.onnx + classes.txt — скопируйте оба файла на Pi,
пути пропишите в config.py (YOLO_MODEL_PATH, YOLO_CLASSES_FILE).
"""
import sys
from pathlib import Path


def main():
    weights = sys.argv[1] if len(sys.argv) > 1 else 'yolov8n.pt'
    imgsz = int(sys.argv[2]) if len(sys.argv) > 2 else 320

    try:
        from ultralytics import YOLO
    except ImportError:
        print('Нужен ultralytics: pip install ultralytics (только на ПК!)')
        sys.exit(1)

    model = YOLO(weights)
    onnx_path = Path(weights).with_suffix('.onnx')
    model.export(format='onnx', imgsz=imgsz, simplify=True, opset=12,
                 dynamic=False, half=False)
    print(f'OK: {onnx_path}')

    # classes.txt — имена классов по порядку id
    names = model.names  # {id: name}
    classes_path = onnx_path.with_name('classes.txt')
    with open(classes_path, 'w') as f:
        for i in range(max(names) + 1):
            f.write(names.get(i, f'class{i}') + '\n')
    print(f'OK: {classes_path}')
    print('\nНа Pi пропишите в config.py:')
    print(f'  YOLO_MODEL_PATH = "/home/pi/models/{onnx_path.name}"')
    print(f'  YOLO_CLASSES_FILE = "/home/pi/models/classes.txt"')
    print('Проверьте docs/YOLO_GUIDE.md — там порядок загрузки и ожидаемая FPS.')


if __name__ == '__main__':
    main()
