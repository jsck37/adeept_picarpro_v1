#!/usr/bin/env python3
"""Экспорт YOLO-pose в ONNX для режима «Рука» (запускать НА ПК, не на роботе).

    pip install ultralytics onnx
    python3 tools/export_pose.py                      # yolo11n-pose, вход 320
    python3 tools/export_pose.py --model yolo11s-pose --imgsz 416
    python3 tools/export_pose.py --scp pi@192.168.4.1 # и сразу скопировать на робота

Результат (models/yolo11n-pose.onnx) читается на роботе через cv2.dnn —
PyTorch на Raspberry Pi не нужен. --imgsz должен совпадать с POSE_INPUT_SIZE
в config.py. Документация: https://docs.ultralytics.com/tasks/pose
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default='yolo11n-pose',
                    help='весы Ultralytics (скачаются автоматически), по умолчанию yolo11n-pose')
    ap.add_argument('--imgsz', type=int, default=320,
                    help='размер входа; = POSE_INPUT_SIZE в config.py (320 для Pi 3B+)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'models', 'yolo11n-pose.onnx'),
                    help='куда положить .onnx (имя должно совпадать с POSE_MODEL_*)')
    ap.add_argument('--scp', metavar='USER@HOST',
                    help='скопировать результат в /home/pi/models/ на роботе')
    args = ap.parse_args()
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit('Нужен пакет ultralytics: pip install ultralytics onnx')
    model = YOLO(args.model if args.model.endswith('.pt') else args.model + '.pt')
    exported = model.export(format='onnx', imgsz=args.imgsz, opset=12,
                            simplify=False, dynamic=False, half=False)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    shutil.copyfile(exported, args.out)
    print(f'OK: {args.out} (imgsz={args.imgsz})')
    if args.scp:
        dst = f'{args.scp}:/home/pi/models/{os.path.basename(args.out)}'
        subprocess.check_call(['scp', args.out, dst])
        print(f'скопировано: {dst}')


if __name__ == '__main__':
    main()
