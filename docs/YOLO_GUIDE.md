# YOLO на Raspberry Pi 3B+ (1 ГБ ОЗУ) — руководство

## Почему так

PyTorch/ultralytics в рантайме на Pi 3B+ не работают: одна только библиотека
torch занимает ~700–800 МБ, а OOM-killer при 1 ГБ ОЗУ убивает процесс почти
сразу. Поэтому:

* **инференс — через `cv2.dnn` и ONNX-модель** (torch вообще не нужен);
* **своп 2 ГБ + zram** (`tools/setup_swap.sh`) — страховка от OOM;
* **вход 320×320** вместо 640×640 — в ~4 раза меньше вычислений;
* **инференс каждый N-й кадр** (`YOLO_FRAME_SKIP`) — MJPEG-стрим не тормозит;
* инференс живёт в отдельном потоке CVThread (`picarpro/camera/yolo_detector.py`).

## Шаг 1. Экспорт модели (на ПК, не на Pi!)

```bash
pip install ultralytics
python3 tools/export_yolo_onnx.py yolov8n.pt 320
# или дообученная модель:
python3 tools/export_yolo_onnx.py best.pt 320
```

Получите `yolov8n.onnx` + `classes.txt`. Скопируйте на Pi:

```bash
scp yolov8n.onnx classes.txt pi@<IP>:/home/pi/models/
```

## Шаг 2. Своп на Pi

```bash
sudo bash tools/setup_swap.sh --zram
```

(см. docs/MEMORY_TUNING.md — детали и почему zram)

## Шаг 3. Конфиг

В `config.py`:

```python
YOLO_MODEL_PATH = '/home/pi/models/yolov8n.onnx'
YOLO_CLASSES_FILE = '/home/pi/models/classes.txt'
YOLO_INPUT_SIZE = 320      # как при экспорте!
YOLO_CONFIDENCE = 0.45
YOLO_FRAME_SKIP = 3
```

## Шаг 4. Запуск

Web-UI → Computer Vision → **YOLO**. На видео появятся рамки с классами и
уверенностью; в статусе (`d.yolo`) — объекты и время инференса.

## Ожидаемая производительность (RPi 3B+, 4×Cortex-A53 1.2 ГГц)

| Модель | Вход | Время инференса | Эффективные FPS |
|---|---|---|---|
| YOLOv8n | 320 | ~600–1100 мс | ~1–1.5 |
| YOLOv5n | 320 | ~550–1000 мс | ~1–1.5 |
| YOLOv8n | 640 | ~3500–6000 мс | ~0.2 (не рекомендуется) |

Для слежения/патрулирования этого достаточно; для быстрой навигации
используйте ArUco-режим (порт из hugo-Peltier) — он в разы дешевле.

## Слежение за объектом (опционально)

`on_yolo_found(objects)` в `picarpro/camera/camera_opencv.py` отдаёт список
объектов (отсортирован по площади). Крупнейший объект — `objects[0]`,
его центр `{'cx', 'cy'}` можно подать на серво панорамы по образцу
`on_color_found` в `picarpro/functions/autonomous.py`.
