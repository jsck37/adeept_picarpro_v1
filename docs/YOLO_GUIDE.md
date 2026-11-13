# YOLO на Raspberry Pi 3B+ (1 ГБ ОЗУ) — руководство

## Почему так

PyTorch/ultralytics в рантайме на Pi 3B+ не работают: одна только библиотека
torch занимает ~700–800 МБ, а OOM-killer при 1 ГБ ОЗУ убивает процесс почти
сразу. Поэтому:

* **инференс — через `cv2.dnn` и ONNX-модель** (torch вообще не нужен);
* **своп 2 ГБ + zram** (`tools/setup_swap.sh`) — страховка от OOM;
* **инференс каждый N-й кадр** (`YOLO_FRAME_SKIP`) — MJPEG-стрим не тормозит;
* инференс живёт в отдельном потоке CVThread (`picarpro/camera/yolo_detector.py`).

## Шаг 1. Модели — поставляются с прошивкой (v1.6.6)

`setup.py` копирует модели из архива прошивки (`models/`) в
`/home/pi/models/` — интернет не обязателен; детекционная модель при
отсутствии также может скачаться с официального релиза ultralytics/yolov5
v7.0 (`YOLO_MODEL_URL`, проверка SHA-256). Вместе с ней создаётся
`classes.txt` (COCO-80). Пропуск этапа: `PICARPRO_SKIP_YOLO=1`.

| Модель | Задача | Вход | Размер | Пик RSS (замер cv2.dnn) |
|---|---|---|---|---|
| `yolov5n.onnx` | детекции (рамки) | 640 | 3.8 МБ | ~150 МБ |
| `yolov5n-seg.onnx` | сегментация (маски) | 640 | 8 МБ | ~190 МБ |

Обе проверены через cv2.dnn на реальных кадрах (bus.jpg: 4 человека, маски
совпадают с силуэтами). Режим YOLO работает при ~500 МБ свободной ОЗУ
вместе с MJPEG-стримом.

## Сегментация (v1.6.6)

`config.py`: `YOLO_TASK = 'seg'` (по умолчанию) — режим YOLO в веб-панели
рисует на видео полупрозрачные маски объектов (цвет по классу), в статусе
(`yolo_status` / `d.yolo`) у каждого объекта — `mask_area` (px) и
`mask_pct` (% кадра). `YOLO_TASK = 'detect'` — только рамки (чуть легче).

Модель ищется по цепочке: `YOLO_SEG_MODEL_PATH` (/home/pi/models) →
`YOLO_SEG_MODEL_BUNDLED` (models/ в прошивке); если seg-модели нет —
автоматический откат на детекционную. Порог маски — `YOLO_MASK_THRESHOLD`
(0.5), максимум масок на кадр — `YOLO_MAX_MASKS` (8). Реализация — схема
YOLACT, как в ultralytics: маска = sigmoid(proto @ коэффициенты), обрезка
по bbox, растяжка в кадр; поддержаны и v5-seg (117 столбцов), и
v8-seg (116, channels first) выходы ONNX.

## Шаг 2. Своп на Pi

```bash
sudo bash tools/setup_swap.sh --zram
```

(см. docs/MEMORY_TUNING.md — детали и почему zram)

## Шаг 3. Конфиг (уже настроен в config.py)

```python
YOLO_TASK = 'seg'          # 'seg' — маски, 'detect' — рамки
YOLO_MODEL_PATH = '/home/pi/models/yolov5n.onnx'
YOLO_SEG_MODEL_PATH = '/home/pi/models/yolov5n-seg.onnx'
YOLO_CLASSES_FILE = '/home/pi/models/classes.txt'
YOLO_INPUT_SIZE = 640      # как у официального v7.0 ONNX!
YOLO_CONFIDENCE = 0.45
YOLO_MASK_THRESHOLD = 0.5
YOLO_MAX_MASKS = 8
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
экспортом). Сегментационную модель экспортируют так же, но с seg-весами
(`yolov5n-seg.pt`); ультралайтовый CLI:

```bash
yolo export model=yolov5n-seg.pt format=onnx opset=12 imgsz=640 dynamic=False
```

Экспорт с динамическими осями или свежими opset (ultralytics 8.x
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
достаточно; автономные режимы «Obstacle Grab» (захват и перенос препятствия)
и «Maze U» (лабиринт) используют детектор так же экономно.

## Слежение за объектом (опционально)

`on_yolo_found(objects)` в `picarpro/camera/camera_opencv.py` отдаёт список
объектов (отсортирован по площади). Крупнейший объект — `objects[0]`,
его центр `{'cx', 'cy'}` можно подать на серво панорамы по образцу
`on_color_found` в `picarpro/functions/autonomous.py`.
