# CHANGELOG — PiCar-Pro Merged Edition v1.0.0

## MOD v1.1.0 — YOLO на 1 ГБ ОЗУ + порт hugo-Peltier + MPU6050 fix + servo UX

### YOLO/OpenCV на RPi 3B+ (1 ГБ ОЗУ)
* `picarpro/camera/yolo_detector.py` — инференс YOLO через `cv2.dnn` (ONNX),
  БЕЗ PyTorch (экономия ~700 МБ); вход 320×320, NMS, разбор выхода
  ultralytics (1,84,N)/(1,N,85);
* новый CV-режим `yoloDetect` (кнопка «YOLO» в Computer Vision) с оверлеем
  рамок на MJPEG-потоке и ленивой загрузкой модели;
* `tools/setup_swap.sh` — своп 2 ГБ (dphys-swapfile), vm.swappiness=80,
  опциональный zram (zstd); `docs/MEMORY_TUNING.md`;
* `tools/export_yolo_onnx.py` — экспорт .pt → .onnx + classes.txt (на ПК);
  `docs/YOLO_GUIDE.md`.

### Порт hugo-Peltier/Adeept-picarPro
* `picarpro/routes/nav_server.py` — NavTCP на порту **5001** (в оригинале
  5000 конфликтовал с Flask): FORWARD/LEFT/RIGHT/BACKWARD/STOP/SPEED/
  PICK_BLUE/DROP/STATUS/QUIT + watchdog STOP при потере хоста;
* `picarpro/functions/aruco_nav.py` — бортовая ArUco-навигация (порт Demo.py):
  путевые точки DICT_4X4_50, 3-state контроллер, прибытие по площади маркера,
  PICK_BLUE по завершении; поддержка нового (≥4.7) и старого cv2.aruco API;
* `tools/aruco_nav_host.py` — хост-скрипт с надземной камерой (порт Demo.py):
  слияние курса с IMU по UDP (alpha-фильтр), финальное сближение;
* `picarpro/functions/imu_stream.py` — UDP-стриминг gz @ 20 Гц (порт 5005);
* `picarpro/functions/arm_push.py` — расталкивание препятствий клешней
  (порт Phase 2 «Obstacle handling»);
* `picarpro/functions/color_scan.py` — сканирование панорамой + цветовой
  захват (порт colortracking.py: SCAN/TRACK/GRASP, TRACK_STEP=1°);
* web-UI: блок «ArUco Nav & Arm» в Autonomous; команды `aruco_nav`,
  `push`, `color_scan`; статусы в broadcast.

### Исправлена инициализация MPU6050
* корректная reset-последовательность: DEVICE_RESET (0x80) → пробуждение
  с CLKSEL=PLL гироскопа X (0x01) — раньше писался 0x00 (внутренний RC),
  а чип по умолчанию просыпается в SLEEP;
* контроль бита SLEEP после пробуждения, повторные попытки WHO_AM_I
  (3×150 мс — чипу нужно время после подачи питания);
* **фикс утечки потоков**: после 30 ошибок чтения `_reinit` порождал новый
  retry-цикл, а старый `_read_loop` продолжал крутиться с `bus=None` и
  клонировал циклы бесконечно — теперь генерационные токены (`self._gen`)
  останавливают устаревшие циклы; доступ к шине сериализован `_bus_lock`.

### Servo Control: степперы вместо слайдеров
* слайдеры заменены на степперы «−/+» с шагом ровно 1°: тап = 1°,
  удержание = автоповтор (~8°/с), прямой ввод угла;
* **защита лимитов**: min/max заблокированы (disabled, пунктир) по умолчанию;
  редактирование — только по кнопке «🔒 Edit Limits» (на время редактирования
  движение серво блокируется), сохранение — строго по «Save Limits»
  (атомарная запись всех серво в servo_cal.json на Pi + подтверждение
  через servo_get_limits); отмена — «Cancel»;
* сервер валидирует лимиты как и раньше (`_clamp` в ServoController) —
  случайное изменение невозможно ни в UI, ни по прямым командам;
* синхронизация углов из статуса (servo_angles) с защитой от перезаписи
  поля в фокусе.

### Прочее
* boot.py: запуск NavTCP + IMU-стрима, корректная остановка при выходе;
* state.py: новые компоненты (aruco_nav, pusher, color_scan, nav_server,
  imu_stream) + расширенный статус (yolo, nav_tcp, aruco_nav, push,
  color_scan);
* config.py: секции YOLO_*, ARUCO_*, NAV_TCP_PORT=5001, IMU_UDP_PORT=5005,
  OBSTACLE_PUSH_*, COLOR_SCAN_*;
* commands.py: команды `yolo_status`, `aruco_nav`, `push`, `color_scan`,
  cv-режим `yoloDetect`;
* docs: YOLO_GUIDE.md, MEMORY_TUNING.md, ARUCO_NAV.md; README дополнен.

---

Полное слияние трёх версий прошивки Adeept с приоритетом мода **jsck37**:

| Источник | Роль в слиянии |
|---|---|
| [jsck37/adeept_picarpro_v1](https://github.com/jsck37/adeept_picarpro_v1) | **База**: архитектура пакета, config.py, драйверы железа, веб-UI, протокол JSON-команд |
| [Adeept_PiCarPro (v1)](https://github.com/adeept/Adeept_PiCarPro) | Источник CV-режимов findColor/watchDog, фильтров PID/Kalman |
| [Adeept_PiCar-Pro (v2)](https://github.com/adeept/Adeept_PiCar-Pro) | Источник модулей Voltage (ADS7830), steadyCamera, wifi_hotspot_manager |

Требования: Python 3.9+, Raspberry Pi (стандартный набор железа v1).

---

## 1. Радикальная реорганизация структуры

```
Старая (jsck37)                    Новая (merged)
boot.py, config.py          ->    без изменений (точка входа и конфиг)
Server/WebServer.py         ->    picarpro/web/app.py + picarpro/web/websocket.py + picarpro/web/auth.py
Server/commands.py          ->    picarpro/commands.py
Server/state.py             ->    picarpro/state.py
Server/logger.py            ->    picarpro/logger.py
Server/network.py           ->    picarpro/network.py
Server/hardware/*.py        ->    picarpro/hardware/*.py (+ voltage.py)
Server/camera/              ->    picarpro/camera/
Server/functions/           ->    picarpro/functions/ (+ steady_camera.py)
Server/routes/              ->    picarpro/routes/bluetooth.py
Server/utils/               ->    picarpro/utils/ (+ filters.py)
Server/dist/                ->    picarpro/web/static/
—                                  picarpro/__init__.py (версия)
—                                  tools/selftest.py (аппаратный самотест)
—                                  tests/ (256 pytest-тестов, работают на ПК без железа)
—                                  wifi_hotspot_manager.sh (порт из v2)
```

Все импорты переведены с `Server.*` на пакет `picarpro.*`; строковые
`__import__` заменены на `importlib.import_module` с прямыми именами классов.

## 2. Безопасность

1. **MAC-инъекция в bluetoothctl** (`routes/bluetooth.py`): MAC из запроса
   уходил в stdin bluetoothctl без валидации — перевод строки в MAC позволял
   внедрить произвольные команды (`remove *`, `quit`). Добавлена строгая
   валидация `^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$` (`validate_mac`), все
   точки входа проверяют формат.
2. **CORS `*`** (`web/app.py`): wildcard CORS удалён. Белый список задаётся
   `PICARPRO_CORS` (по умолчанию CORS полностью выключен — UI одностраничный
   и same-origin).
3. **Опциональная токен-авторизация** (`web/auth.py`): при заданном
   `PICARPRO_TOKEN` все `/api/*` и `/cmd/*` и WebSocket требуют токен
   (заголовок `X-Auth-Token`, `?token=`, либо первое WS-сообщение
   `{"cmd":"auth"}`). Сравнение через `hmac.compare_digest` (timing-safe).
4. **SECRET_KEY** больше не константа `'picarpro'`: из окружения либо
   автогенерация `secrets.token_hex(32)` с сохранением в
   `instance/secret_key` (chmod 600).
5. **Заголовки безопасности**: `X-Content-Type-Options`, `X-Frame-Options:
   DENY`, `Referrer-Policy`, CSP без inline-скриптов.
6. **`MAX_CONTENT_LENGTH`** = 1 MiB на запрос.
7. **Path traversal** в `/docs/components/<fn>` — блокировка `..`.
8. **Утечка стека** в WS-ответах об ошибках обрезана до 200 символов.
9. Из унаследованных уязвимостей оригиналов НЕ перенесены: логин
   `admin:123456` по открытому WS, пароль точки доступа `12345678` как
   единственная защита, APPServer без аутентификации, `os.system` с
   конкатенацией, захардкоженный ключ DeepSeek в Examples v2
   (`sk-cb36...` — ключ отозван не был, упомянут в README).

## 3. Ошибки памяти / ресурсов

1. **Голосовой запуск сломан полностью** (`functions/voice_command.py:57-63`
   в jsck37): дескриптор `open(...)` открывался в `with`-блоке и ЗАКРЫВАЛСЯ
   до `Popen(stdout=f)` — Popen получал закрытый файл, голосовое управление
   никогда не стартовало. Фикс: дескриптор хранится в `self._sherpa_log`,
   закрывается в `_stop_sherpa`.
2. **Subprocess-шторм IP** (`network.py`): `get_ip()` вызывал до 4
   subprocess `ip addr show` на каждый тик статуса (раз в секунду на
   каждого клиента). Добавлен кэш на 30 с (`reset_ip_cache` для тестов).
3. **Sleep в обработчике запроса** (`utils/system_info.py`):
   `get_cpu_usage()` спал 0.3 с внутри каждого запроса статуса. CPU/RAM
   теперь собирает фоновый поток-сэмплер с кэшем (2 с), ответы отдаются
   мгновенно.
4. **Утечка потоков сглаживания крана** (`hardware/ds4.py`): каждое нажатие
   кнопки порождало новый поток `_smooth_crane`. Частые нажатия = dozens
   threads. Теперь цель хранится в `_smooth_targets` — активный поток
   подхватывает новую цель, новые потоки не создаются.
5. **Гонка set()+clear()** (`camera/camera_opencv.py`):
   `_frame_ready.set(); _frame_ready.clear()` подряд — ожидающий поток мог
   никогда не проснуться (кадры «зависали»). Событие теперь остаётся
   установленным до следующего кадра.
6. **Пересоздание I2C/PCA9685** из оригиналов (RPIservo.py:63-69 v1/v2 —
   новый объект на каждое движение сервы) в jsck37-архитектуре уже было
   исправлено (один контроллер с локом) — поведение сохранено и покрыто
   тестами.
7. **video_feed без камеры** возвращал вечный пустой MJPEG-поток — теперь
   честный `503 Camera not available`.
8. **ADS7830-монитор** проверяет наличие АЦП на шине до создания ридера —
   без АЦП не держит открытый SMBus.

## 4. Ошибки логики (найдено и исправлено при слиянии)

1. **`trackLine` выпадал из списка режимов** — при переносе списков
   `MODES_WITH_ULTRA + MODES_WITH_CAMERA` строка `trackLine` отсутствовала в
   `ALL_MODES`, и режим IR-линии отбрасывался валидатором. Исправлено,
   покрыто тестом.
2. **`safe_int` везде** (`commands.py`): `int(params)` без try ронял
   обработчик WS-команд на мусорном вводе (`servo id='abc'` → ValueError).
   Все числовые параметры парсятся безопасно с ограничением диапазона;
   `servo_set_limits` дополнительно разворачивает min>max.
3. **Неизвестные команды** теперь возвращают явную ошибку
   `Unknown command: ...` вместо молчаливого `ok: false`.
4. **Дублирование дифференциального привода** (`ds4.py` ↔ `motors.py`):
   `_motors_move_with_turn`/`_motors_single_turn` копировали логику
   `MotorController.move` с прямой записью в GPIO. Заменены общим методом
   `MotorController.move_differential(speed, dir, left, right)`.
5. **Направление findColor**: в v2 APPMode кадр конвертировался BGR2RGB
   вместо HSV перед inRange (camera_opencv v2:350-353) — режим не находил
   ничего. В порте HSV-конвертация корректна, m00 защищён от деления на 0.
6. **Кулдаун watchdog**: в оригинале тревога могла срабатывать каждый кадр;
   добавлены порог площади + кулдаун `WATCHDOG_COOLDOWN_S`.
7. **`watch_dog`/`findColor` callbacks отвязываются** в `stop()` — не
   остаётся висящих ссылок на автономный контроллер после смены режима.
8. **Команда `auto`** возвращает осмысленную ошибку при отказе
   (`Ultrasonic sensor not available` и т.п.) вместо `ok:false` без причин.

## 5. Новые функции (порт из v1/v2)

| Функция | Источник | Реализация |
|---|---|---|
| `findColor` — следование за цветным объектом | v1/v2 findColor | CV-режим `findColor` + автономный режим с панорамированием камеры в поиске |
| `watchDog` — охранная сигнализация | v1/v2 Camera_WatchDog | CV-детектор движения + зуммер (мелодия `alarm`) + police-LED |
| `steady` — стабилизация камеры | v2 steadyCamera | `functions/steady_camera.py`: pitch от MPU6050 через фильтр Калмана → компенсация наклона SERVO_CAM_TILT |
| Мониторинг батареи | v2 Voltage.py | `hardware/voltage.py`: ADS7830 (0x48) с медианным фильтром, процент заряда, тревога < 6.3 В; без АЦП — dmesg/vcgencmd under-voltage мониторинг jsck37 |
| Фильтры PID + Kalman | v1 PID.py / Kalman_Filter.py | `utils/filters.py` с исправлением anti-windup и «замороженного» выхода; Kalman применён к дистанции HC-SR04 в automatic/keepDistance |
| Hotspot-менеджер | v2 wifi_hotspot_manager.sh | Перенесён, параметризуется из config.py, ставится systemd-сервисом `picarpro-hotspot` |
| Аппаратный самотест | замена Examples | `tools/selftest.py`: 12 проверок всех модулей железа на машинке |
| Кнопки UI | — | Watchdog, Color Track, Steady Cam, индикатор BAT в статус-баре |

## 6. Веб-интерфейс

* переписаны все роуты Flask (см. §2): auth-guard, security headers, CORS,
  ошибки вместо 500;
* добавлена поддержка токена: `localStorage`, WS `?token=`, prompt при 401;
* новые элементы: Watchdog / Color Track / Steady Cam, индикатор батареи
  (виден только при наличии АЦП).

## 7. Выброшено (сознательно, режим «Аккуратно»)

| Компонент оригиналов | Причина |
|---|---|
| GUIServer.py (TCP :10223) + FPV.py (ZMQ :5555) + Client/*.py (Tkinter) | Легаси-путь PC-клиента; веб-UI покрывает 100% функций, работает на любом устройстве. В v2-клиенте 12+ подтверждённых багов (см. отчёт аудита) |
| APPServer.py (протокол мобильного APP) | Несовместим с JSON-протоколом мода jsck37; сам по себе — копия WebServer без аутентификации |
| Examples/ (15 папок) | Заменены: тесты pytest (256 шт., логика) + tools/selftest.py (железо на машинке) |
| create_ap (v1) | Заменён nmcli-скриптом из v2 |
| chmod 777 startup.sh, правка rc.local | Заменены корректным systemd-сервисом |

## 8. Тесты

**256 тестов, все зелёные, работают на обычном ПК без железа** (feйки
RPi.GPIO / smbus2 / spidev / picamera2 / evdev / adafruit / luma / cv2
устанавливаются conftest.py в sys.modules до импорта проекта):

```
tests/test_filters.py       PID anti-windup, Kalman сходимость (10)
tests/test_config.py        уникальность пинов, диапазоны, лимиты (13)
tests/test_motors.py        направления, ШИМ, дифференциал, стоп (13)
tests/test_servos.py        клампинг, калибровка, PCA9685 duty (16)
tests/test_ultrasonic.py    временная диаграмма эха, таймауты (8)
tests/test_switch.py        огни, фара, поворотники (9)
tests/test_buzzer_leds.py   мелодии, SPI-кодирование WS2812, анимации (16)
tests/test_mpu6050.py       скан I2C, парсинг, roll/pitch, reinit (9)
tests/test_voltage.py       ADS7830, процент, тревога (6)
tests/test_oled_voice.py    OLED-кадры, голос: фикс закрытого fd (10)
tests/test_camera_cv.py     линии, руки, findColor, watchdog, камера (17)
tests/test_autonomous.py    радар, IR-линия, дистанция, findColor, watchdog, steady (16)
tests/test_ds4.py           оси, deadzone, турбо, кран, anti-thread-flood (12)
tests/test_commands.py      весь диспетчер + мусорный ввод (30)
tests/test_state.py         статус, hw-флаги, выключение (8)
tests/test_webapp.py        роуты, заголовки, auth, инъекции (25)
tests/test_bluetooth.py     валидация MAC, конфиг, сессия (11)
tests/test_network.py       кэш IP, редирект, сэмплеры (8)
tests/test_websocket.py     протокол WS, auth, broadcast (9)
```

Запуск: `python3 -m pytest tests/ -v` — на ПК; `tools/selftest.py` — на машинке.

## 9. Установка

Без изменений по духу jsck37, но с двумя сервисами:

```bash
sudo python3 setup.py        # deps + picarpro.service + picarpro-hotspot.service
sudo systemctl start picarpro
```

Переменные окружения (опционально): `PICARPRO_TOKEN` (включить авторизацию),
`PICARPRO_SECRET_KEY`, `PICARPRO_CORS` (белый список через запятую).
