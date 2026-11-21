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
`/home/pi/models/` — интернет не обязателен. Вместе с моделью создаётся
`classes.txt` (COCO-80). Пропуск этапа: `PICARPRO_SKIP_YOLO=1`.

**v1.6.10:** остался один режим YOLO — **«YOLO Mask»** (сегментация).
Режимы «YOLO» (рамки) и «YOLO Sem» (карта классов) удалены как бесполезные,
вместе с детекционной моделью `yolov5n.onnx`. Режим «Рука» теперь работает на
отдельной модели YOLO-pose — см. `docs/POSE_GUIDE.md`.

| Модель | Задача | Вход | Размер | Пик RSS (замер cv2.dnn) |
|---|---|---|---|---|
| `yolov5n-seg.onnx` | сегментация (маски) | 320 | 7.7 МБ | ~35 МБ |

v1.6.12: официальные веса v7.0 (`yolov5n-seg.pt`) реэкспортированы под вход
320 (оригинальный экспортёр yolov5 v7.0: opset 12, simplify, без динамических
осей). Замер на ПК через cv2.dnn: инференс 80 мс @640 → 24 мс @320, пик RSS
110 МБ → 34 МБ — примерно в 3.5 раза быстрее и в 3 раза меньше ОЗУ; обе
модели (seg + pose) вместе держат ~50 МБ. Модель ищется по цепочке:
`YOLO_SEG_MODEL_PATH` (/home/pi/models) → `YOLO_SEG_MODEL_BUNDLED` (models/
в прошивке — с v1.6.12 ставится и веб-обновлением); нет модели — в логе
предупреждение, режим ничего не рисует. Имена классов: `/home/pi/models/
classes.txt` → комплектный `models/classes.txt` (в архиве). Порог маски —
`YOLO_MASK_THRESHOLD` (0.5), максимум масок на кадр — `YOLO_MAX_MASKS` (8).
Реализация — схема YOLACT, как в ultralytics: маска = sigmoid(proto @
коэффициенты), обрезка по bbox, растяжка в кадр; поддержаны и v5-seg
(117 столбцов), и v8-seg (116, channels first) выходы ONNX.

## Сегментация (v1.6.6)

Режим «YOLO Mask» в веб-панели рисует на видео рамки и полупрозрачные маски
объектов (цвет по классу), в статусе (`yolo_status` / `d.yolo`) у каждого
объекта — `mask_area` (px) и `mask_pct` (% кадра).

## Шаг 2. Своп на Pi

```bash
sudo bash tools/setup_swap.sh --zram
```

(см. docs/MEMORY_TUNING.md — детали и почему zram)

## Шаг 3. Конфиг (уже настроен в config.py)

```python
YOLO_SEG_MODEL_PATH = '/home/pi/models/yolov5n-seg.onnx'
YOLO_CLASSES_FILE = '/home/pi/models/classes.txt'
YOLO_INPUT_SIZE = 320     # как у комплектной модели (v1.6.12)!
YOLO_CONFIDENCE = 0.45
YOLO_MASK_THRESHOLD = 0.5
YOLO_MAX_MASKS = 8
YOLO_FRAME_SKIP = 1       # инференс на каждом кадре CV-потока
```

## Шаг 4. Запуск

Web-UI → Computer Vision → **YOLO Mask**. На видео появятся рамки и маски с
классами и уверенностью; в статусе (`d.yolo`) — объекты и время инференса.

## Другой вход / дообученная модель (на ПК, не на Pi!)

Комплектная модель (v1.6.12) уже экспортирована с входом 320. Если нужен
другой вход или дообученная модель — экспорт делается на ПК:

```bash
pip install ultralytics
python3 tools/fetch_yolo.py --model yolov5n --imgsz 320
# или дообученная модель:
python3 tools/export_yolo_onnx.py best.pt 320
scp models/yolov5n-seg.onnx classes.txt pi@<IP>:/home/pi/models/
```

и в `config.py` поставьте тот же `YOLO_INPUT_SIZE` (должен совпадать с
экспортом). Сегментационную модель экспортируют так же, но с seg-весами
(`yolov5n-seg.pt`); ультралайтовый CLI:

```bash
yolo export model=yolov5n-seg.pt format=onnx opset=12 imgsz=320 dynamic=False
```

Экспорт с динамическими осями или свежими opset (ultralytics 8.x
по умолчанию) cv2.dnn читает НЕ всегда — держитесь opset 12 и
`dynamic=False` (так делает `tools/fetch_yolo.py`; комплектная модель
в v1.6.12 экспортирована оригинальным экспортёром yolov5 v7.0 —
`python export.py --weights yolov5n-seg.pt --img 320 --include onnx
--opset 12 --simplify`).

## Ожидаемая производительность (RPi 3B+, 4×Cortex-A53 1.2 ГГц)

| Модель | Вход | Время инференса | Эффективные FPS |
|---|---|---|---|
| YOLOv5n-seg, комплект (v1.6.12) | 320 | ~250–500 мс | ~2–4 |
| YOLOv5n-seg @640 (было до v1.6.12) | 640 | ~1000–2000 мс | ~0.5–1 |

Оценка по замеру на ПК через cv2.dnn (80 → 24 мс, ×3.4). Поток видео
инференс не тормозит (детект в отдельном CVThread, устаревшие кадры
отбрасываются) — MJPEG-стрим идёт с полными 30 fps камеры, оверлей YOLO
обновляется 2–4 раза в секунду.

## Слежение за объектом (опционально)

`on_yolo_found(objects)` в `picarpro/camera/camera_opencv.py` отдаёт список
объектов (отсортирован по площади). Крупнейший объект — `objects[0]`,
его центр `{'cx', 'cy'}` можно подать на серво панорамы по образцу
`on_color_found` в `picarpro/functions/autonomous.py`.
