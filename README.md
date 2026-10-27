# Adeept PiCar-Pro V1

---

## 🆕 Мод-версия: что добавлено и изменено

* **YOLO-детектирование объектов** (режим «YOLO» в Computer Vision) —
  ONNX-модель через `cv2.dnn`, без PyTorch. Рассчитано на RPi 3B+ с 1 ГБ ОЗУ:
  swap 2 ГБ + zram (`sudo bash tools/setup_swap.sh --zram`), вход 320×320,
  пропуск кадров. Инструкция: **docs/YOLO_GUIDE.md**, настройка памяти:
  **docs/MEMORY_TUNING.md**.
* **Функционал hugo-Peltier/Adeept-picarPro** (docs/ARUCO_NAV.md):
  - ArUco-навигация по путевым точкам — бортовая (камера Pi) и хост-режим
    с надземной камерой (`tools/aruco_nav_host.py`) + NavTCP-сервер (порт 5001,
    команды FORWARD/LEFT/RIGHT/STOP/PICK_BLUE/SPEED/STATUS) + UDP-стриминг
    гироскопа (порт 5005) для слияния курса;
  - Push Mode — расталкивание препятствий клешней по HC-SR04;
  - Color Scan — сканирование панорамой + цветовой захват клешнёй
    (порт colortracking.py).
* **Servo Control без слайдеров**: степперы «−/+» с шагом ровно 1°
  (удержание = автоповтор) и прямым вводом угла. Лимиты заблокированы:
  редактируются только по кнопке «🔒 Edit Limits» и запоминаются строго
  по «Save Limits» (сохранение на сервере в servo_cal.json + валидация
  на обеих сторонах).
* **Починена инициализация MPU6050**: полная reset-последовательность
  (DEVICE_RESET → пробуждение с CLKSEL=PLL — чип заводится в SLEEP!),
  контроль бита SLEEP, повторные попытки WHO_AM_I, фикс утечки потоков
  при реинициализации (генерационные токены циклов чтения).

---

## 📋 Требования

- Raspberry Pi (3B/3B+/4/5 или совместимая модель)
- Карта микроSD (от 8 ГБ)
- Подключение к интернету
- ОС: **Raspberry Pi OS** (64-bit)

---

## Информация о прошивке:

* Flask + WebSocket-сервер с веб-интерфейсом (джойстики, CV-режимы, радар,
  консоль логов, справочник компонентов);
* драйверы: L298N (моторы), PCA9685 (7 каналов серво), HC-SR04, MPU6050,
  SSD1306 OLED, WS2812 LED (SPI), зуммер, фары/поворотники;
* геймпад DualShock 4 по Bluetooth (с валидацией MAC);
* автономные режимы: радар, автопилот, IR-линия, CV-линия, дистанция,
  слежение рукой, **следование за цветом**, **охрана (watchdog)**;
* **стабилизация камеры** по MPU6050;
* **мониторинг батареи** ADS7830 / under-voltage (нужно подключить модуль);
* голосовое управление Sherpa-NCNN (опционально);
* Wi-Fi + резервная точка доступа;
* **256 pytest-тестов**, работающих на ПК без железа, и аппаратный самотест.

---

## ⚙️ Установка

### 1. Подготовка образа ОС
1. Скачайте [Raspberry Pi Imager](https://downloads.raspberrypi.com/imager/imager_latest.exe)
2. Выберите ОС: [Raspberry Pi OS](https://www.raspberrypi.com/software/operating-systems/)
3. Запишите образ на microSD и запустите Raspberry Pi

### 2. Установка ПО

> Поддерживаются Raspberry Pi OS **Bullseye / Bookworm / Trixie** (Debian
> 11–13): установщик сам определяет выпуск ОС и подбирает имена пакетов
> (в Trixie `libtiff5`/`libraspberrypi-bin` переименованы, `picamera2`
> ставится из apt, `RPi.GPIO` — из pip).
> **После установки** для YOLO на 1 ГБ ОЗУ выполните
> `sudo bash tools/setup_swap.sh` (2 ГБ подкачки) и перезагрузитесь.

Подключитесь к Raspberry Pi через SSH или терминал и выполните:

```bash
# Обновление системы
sudo apt update -y && sudo apt upgrade -y

# Установка Git
sudo apt install git -y

# Клонирование репозитория
git clone https://github.com/jsck37/adeept_picarpro_v1

# Запуск установки
sudo python3 adeept_picarpro_v1/setup.py
```

### 3. Проверка службы автозагрузки

```bash
systemctl status picarpro
```

### 4. Запуск веб-интерфейса

1. http://localhost:5000
2. http://10.42.0.1:5000

---

### Переменные окружения (опционально)

| Переменная | Значение |
|---|---|
| `PICARPRO_TOKEN` | включить токен-авторизацию (запрашивается в веб-UI) |
| `PICARPRO_SECRET_KEY` | свой секрет Flask (иначе генерируется автоматически) |
| `PICARPRO_CORS` | белый список CORS-источников через запятую |

## Запуск теста прошивки на ПК

```bash
python3 -m pytest tests/ -v        # 256 тестов
```

Тесты используют фейки RPi.GPIO / smbus2 / spidev / picamera2 / evdev /
adafruit / luma.oled / cv2, которые conftest.py подставляет до импорта
проекта. Реальное железо не требуется.

## Проверка железа на машинке

```bash
sudo python3 tools/selftest.py
```

Кратко двигает серво/моторы, читает датчики, мигает LED, пикает зуммером и
печатает отчёт PASS/FAIL/SKIP по каждому модулю.

## Структура

```
boot.py                     точка входа (systemd: picarpro.service)
config.py                   все настройки: пины, серво, сеть, камера, питание
picarpro/
  hardware/                 моторы, серво, датчики, OLED, LED, зуммер, DS4, АЦП
  camera/                   Picamera2 + CV (линия/рука/цвет/охрана)
  functions/                автономные режимы, стабилизация, голос
  web/                      Flask, WebSocket, авторизация, статика UI
  routes/                   Bluetooth-геймпад
  utils/                    system info, PID/Kalman
tools/selftest.py           аппаратный самотест
tests/                      pytest-набор с фейками железа
docs/                       справочник компонентов и распиновки (JSON)
wifi_hotspot_manager.sh     Wi-Fi / резервная точка доступа (из v2)
```