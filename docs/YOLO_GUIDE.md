# YOLO на Raspberry Pi 3B+ (1 ГБ ОЗУ) — руководство

## Почему так

PyTorch/ultralytics в рантайме на Pi 3B+ не работают: одна только библиотека
torch занимает ~700–800 МБ, а OOM-killer при 1 ГБ ОЗУ убивает процесс почти
сразу. Поэтому:

* **инференс — через `cv2.dnn` и ONNX-модель** (torch вообще не нужен);
* **своп 2 ГБ + zram** (`tools/setup_swap.sh`) — страховка от OOM;
* **инференс каждый N-й кадр** (`YOLO_FRAME_SKIP`) — MJPEG-стрим не тормозит;
* инференс живёт в отдельном потоке CVThread (`picarpro/camera/yolo_detector.py`).

## Шаг 1. Модель — скачивается автоматически (v1.6.5)

`setup.py` на этапе [8/8] сам скачивает **готовый ONNX** с официального
релиза ultralytics/yolov5 v7.0 — экспортировать ничего не нужно:

* модель: `yolov5n.onnx` (YOLOv5 **nano**, 1.9M параметров, 3.8 МБ, вход 640×640);
* URL: `https://github.com/ultralytics/yolov5/releases/download/v7.0/yolov5n.onnx`;
* целостность проверяется по SHA-256 (`YOLO_MODEL_SHA256` в `config.py`);
* вместе с моделью создаётся `classes.txt` (COCO-80);
* пропуск этапа: `PICARPRO_SKIP_YOLO=1`.

Память (замер cv2.dnn, CPU, чтение + инференс 640): **пик ~150 МБ RSS** —
режим YOLO работает при ~500 МБ свободной ОЗУ вместе с MJPEG-стримом.

## Шаг 2. Своп на Pi

```bash
sudo bash tools/setup_swap.sh --zram
```

(см. docs/MEMORY_TUNING.md — детали и почему zram)

## Шаг 3. Конфиг (уже настроен в config.py)

```python
YOLO_MODEL_PATH = '/home/pi/models/yolov5n.onnx'
YOLO_CLASSES_FILE = '/home/pi/models/classes.txt'
YOLO_INPUT_SIZE = 640      # как у официального v7.0 ONNX!
YOLO_CONFIDENCE = 0.45
YOLO_FRAME_SKIP = 3
```

## Шаг 4. Запуск

Web-UI → Computer Vision → **YOLO**. На видео появятся рамки с классами и
уверенностью; в статусе (`d.yolo`) — объекты и время инференса.

## Своя модель / вход 320 (на ПК, не на Pi!)

Официальный v7.0 ONNX экспортирован с входом 640. Если нужен более быстрый
320-вход или дообученная модель — экспорт делается на ПК:

```bash
pip install ultralytics
python3 tools/fetch_yolo.py --model yolov5n --imgsz 320
# или дообученная модель:
python3 tools/export_yolo_onnx.py best.pt 320
scp models/yolov5n.onnx classes.txt pi@<IP>:/home/pi/models/
```

и в `config.py` поставьте `YOLO_INPUT_SIZE = 320` (должен совпадать с
экспортом). Экспорт с динамическими осями или свежими opset (ultralytics 8.x
по умолчанию) cv2.dnn читает НЕ всегда — держитесь opset 12 и
`dynamic=False` (так делает `tools/fetch_yolo.py`).

## Ожидаемая производительность (RPi 3B+, 4×Cortex-A53 1.2 ГГц)

| Модель | Вход | Время инференса | Эффективные FPS |
|---|---|---|---|
| YOLOv5n (v7.0 ONNX) | 640 | ~1500–3000 мс | ~0.4–0.7 |
| YOLOv5n, свой экспорт | 320 | ~550–1000 мс | ~1–1.5 |
| YOLOv8n, свой экспорт | 320 | ~600–1100 мс | ~1–1.5 |

Инференс 640 медленнее, но поток видео не тормозит (детект в отдельном
потоке); детекти обновляются раз в ~1–3 с. Для слежения/патрулирования этого
достаточно; для быстрой навигации используйте ArUco-режим (порт из
hugo-Peltier) — он в разы дешевле.

## Слежение за объектом (опционально)

`on_yolo_found(objects)` в `picarpro/camera/camera_opencv.py` отдаёт список
объектов (отсортирован по площади). Крупнейший объект — `objects[0]`,
его центр `{'cx', 'cy'}` можно подать на серво панорамы по образцу
`on_color_found` в `picarpro/functions/autonomous.py`.
