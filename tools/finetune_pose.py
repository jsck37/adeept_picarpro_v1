#!/usr/bin/env python3
"""Дообучение YOLO-pose под режим «Hand» (v1.6.13) — НА ПК, не на роботе.

Зачем: комплектная модель yolo11n-pose обучена на людях (COCO-17) и не имеет
точек пальцев, поэтому центр ладони на роботе оценивается геометрически
(запястье + доля предплечья, см. picarpro/camera/pose_detector.py). Если
нужна точность «по пальцам» — дообучите модель на датасете с разметкой
кисти (21 ключевая точка, раскладка MediaPipe) и положите ONNX на робота:
декодер распознаёт число точек автоматически (раскладка 'hand21').

Требования к машине обучения: ПК с NVIDIA GPU (8+ ГБ VRAM) или Colab;
на самой Pi обучение невозможно (800 МБ ОЗУ).

Использование:
    pip install ultralytics onnx onnxsim
    python tools/finetune_pose.py --data hand-kpts.yaml --epochs 100

Формат датасета — стандартный Ultralytics pose YAML:
    path: /data/hand-kpts
    train: images/train
    val: images/val
    names: {0: hand}
    kpt_shape: [21, 3]     # 21 точка, (x, y, видимость)

Готовые размечанные наборы «hand keypoints» ищите на Roboflow/Kaggle
(экспорт «Ultralytics YOLO Pose»). Можно и переклеить: разметить 300-800
кадров своей ладони в CVAT/Label Studio (того же формата) — для слежения
камеры этого достаточно: нароботная задача одна, не 80 классов.

Результат: best.onnx (вход 320) — скопируйте в /home/pi/models/
yolo11n-pose.onnx (или положите рядом и укажите POSE_MODEL_PATH в
config_local.py). На роботе PyTorch НЕ нужен — инференс через cv2.dnn.
"""
import argparse
import os
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser(
        description='Дообучение YOLO-pose (кисть, 21 kpt) и экспорт в ONNX')
    ap.add_argument('--data', required=True,
                    help='YAML датасета в формате Ultralytics pose')
    ap.add_argument('--model', default='yolo11n-pose.pt',
                    help='базовые веса (yolo11n-pose.pt / yolo11s-pose.pt)')
    ap.add_argument('--imgsz', type=int, default=320,
                    help='вход сети; НА РОБОТЕ ОЖИДАЕТСЯ 320 (POSE_INPUT_SIZE)')
    ap.add_argument('--epochs', type=int, default=100)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--device', default='0',
                    help='GPU (0) / cpu — обучение идёт только на ПК')
    ap.add_argument('--name', default='hand21_finetune')
    ap.add_argument('--no-export', action='store_true',
                    help='не экспортировать ONNX после обучения')
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit('Нужен пакет ultralytics:  pip install ultralytics '
                 '(запускать на ПК с GPU, не на роботе)')

    model = YOLO(args.model)
    results = model.train(
        data=args.data, imgsz=args.imgsz, epochs=args.epochs,
        batch=args.batch, device=args.device, name=args.name,
        # кисть маленькая — усиляем мелкие объекты
        close_mosaic=10, mosaic=1.0, degrees=15, scale=0.5,
        fliplr=0.5,             # лево/право зеркалятся — аугментация уместна
        patience=30, exist_ok=True)
    best = getattr(results, 'save_dir', None)
    print(f'\nОбучение завершено: {best}/weights/best.pt'
          if best else '\nОбучение завершено')

    if args.no_export:
        return
    weights = os.path.join(str(best), 'weights', 'best.pt') if best else args.model
    print(f'Экспорт ONNX (imgsz={args.imgsz}, opset 12, без динамических осей)...')
    subprocess.check_call([sys.executable, '-m', 'ultralytics', 'export',
                           'model=' + weights, 'format=onnx',
                           f'imgsz={args.imgsz}', 'opset=12', 'simplify=True'])
    print(f'\nГотово. Скопируйте {weights.rsplit(".", 1)[0]}.onnx на робота:\n'
          f'  scp {weights.rsplit(".", 1)[0]}.onnx pi@10.42.0.1:/home/pi/models/yolo11n-pose.onnx\n'
          'и перезапустите сервис:  sudo systemctl restart picarpro')


if __name__ == '__main__':
    main()
