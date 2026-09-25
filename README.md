# PiCar-Pro — Merged Edition

Единая прошивка для Adeept PiCar-Pro: **слияние первой версии
(Adeept_PiCarPro), второй версии (Adeept_PiCar-Pro) и мода jsck37** с
приоритетом мода. Python 3.9+.

> Полный список правок, исправленных багов и уязвимостей — в
> [CHANGELOG.md](CHANGELOG.md).

## Что внутри

* Flask + WebSocket-сервер с веб-интерфейсом (джойстики, CV-режимы, радар,
  консоль логов, справочник компонентов);
* драйверы: L298N (моторы), PCA9685 (7 каналов серво), HC-SR04, MPU6050,
  SSD1306 OLED, WS2812 LED (SPI), зуммер, фары/поворотники;
* геймпад DualShock 4 по Bluetooth (с валидацией MAC);
* автономные режимы: радар, автопилот, IR-линия, CV-линия, дистанция,
  слежение рукой, **следование за цветом**, **охрана (watchdog)**;
* **стабилизация камеры** по MPU6050 (из v2);
* **мониторинг батареи** ADS7830 / under-voltage (слияние v2 + jsck37);
* голосовое управление Sherpa-NCNN (опционально);
* Wi-Fi + резервная точка доступа (из v2);
* **256 pytest-тестов**, работающих на ПК без железа, и аппаратный самотест.

## Установка на Raspberry Pi

```bash
sudo apt update -y && sudo apt install -y git
git clone <репозиторий> picarpro
cd picarpro
sudo python3 setup.py            # зависимости + systemd-сервисы
sudo systemctl start picarpro
```

После установки: веб-интерфейс на `http://<ip-машинки>` (порт 80 редиректит
на 5000), WebSocket — порт 8888.

### Переменные окружения (опционально)

| Переменная | Значение |
|---|---|
| `PICARPRO_TOKEN` | включить токен-авторизацию (запрашивается в веб-UI) |
| `PICARPRO_SECRET_KEY` | свой секрет Flask (иначе генерируется автоматически) |
| `PICARPRO_CORS` | белый список CORS-источников через запятую |

## Запуск тестов (на ПК, железо не нужно)

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

## Безопасность

Прошивка по умолчанию не имеет пароля (доверенная домашняя сеть). Для
публичных сетей включите токен:

```bash
sudo systemctl edit picarpro    # Environment=PICARPRO_TOKEN=ваш-токен
```

**Внимание:** в оригинальном репозитории Adeept v2 в
`Examples/14_Example_Of_AI/TalkToAI.py` опубликован рабочий API-ключ
DeepSeek (`sk-cb36...`) — если вы им пользовались, отзовите его в личном
кабинете DeepSeek.

## Благодарности

* [Adeept](https://github.com/adeept) — оригинальные прошивки PiCarPro v1
  и PiCar-Pro v2;
* [jsck37](https://github.com/jsck37/adeept_picarpro_v1) — модульная
  архитектура, русская документация, современный стек, на которой всё
  построено.
