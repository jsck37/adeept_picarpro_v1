# ArUco-навигация и работа руки — порт hugo-Peltier/Adeept-picarPro

В проект интегрирован функционал репозитория
[hugo-Peltier/Adeept-picarPro](https://github.com/hugo-Peltier/Adeept-picarPro):
автономная навигация по ArUco-маркерам, расталкивание препятствий клешней и
цветовой захват. Все режимы доступны и из web-UI (карточка
Autonomous → «ArUco Nav & Arm»), и по командам WebSocket/HTTP.

## Режимы

| Режим | Команда | Что делает |
|---|---|---|
| ArUco Nav (бортовой) | `aruco_nav` `{action: start/stop/status}` | камера Pi ищет маркер текущей путевой точки (DICT_4X4_50), доворачивает и едет; прибытие — по площади маркера |
| ArUco Nav (хост-ПК) | `python3 tools/aruco_nav_host.py <IP>` | надземная камера видит робота (ID 0) и все точки; хост шлёт FORWARD/LEFT/RIGHT/STOP по TCP |
| NavTCP | порт **5001** | приёмник команд хоста; протокол — построчный: `FORWARD`, `LEFT`, `RIGHT`, `BACKWARD`, `STOP`, `SPEED <0-100>`, `PICK_BLUE`, `DROP`, `STATUS`, `QUIT` |
| Push Mode | `push` `{action: start/stop/status}` | если HC-SR04 видит препятствие ближе 0.3 м — стоп + сгребающее движение клешней |
| Color Scan | `color_scan` `{action: start/stop/status}` | панорама качается, ищет цветной объект (HSV-фильтр как у findColor), доворот по 1°, схват клешней |
| IMU-стрим | UDP порт 5005 | пакеты `{"t": сек, "gz": рад/с}` @ 20 Гц — уходят на IP хоста автоматически после подключения к NavTCP |

## Настройки (config.py)

```python
ARUCO_WAYPOINTS = [1, 2]        # ID маркеров-точек по порядку обхода
ARUCO_ROBOT_ID = 0              # маркер робота (для хост-режима)
ARUCO_ANGLE_THRESHOLD_DEG = 12  # мёртвая зона курса (3-state контроллер)
ARUCO_SPEED = 35
ARUCO_REACH_AREA = 9000         # «прибыл» — площадь маркера в кадре (бортовой)
NAV_TCP_PORT = 5001             # НЕ 5000 — там Flask!
IMU_UDP_PORT = 5005
OBSTACLE_PUSH_DISTANCE_M = 0.30
COLOR_SCAN_MIN_AREA = 1500      # как MIN_AREA в colortracking.py
```

## Маркеры

Печатайте из словаря **DICT_4X4_50** (ID 0 — робот, 1, 2, 3 — точки).
Маркеры кладите плашмя, освещение равномерное, размер ≥ 5–7 см для бортовой
камеры.

## Схема хост-режима (как в оригинале)

```
[надземная камера]──►[Хост-ПК: aruco_nav_host.py]──TCP 5001──►[NavTCP на Pi]
                          ▲  угол/дистанция                        │
                          └────────UDP 5005 gz (IMU-слияние)───────┘
```

Хост считает угол и дистанцию до следующей путевой точки, шлёт простые
команды; Pi исполняет их моторами и возвращает gyro-z по UDP — когда маркер
робота перекрыт, хост держит курс по интегрированному гироскопу (alpha-слияние
из Demo.py).

## Отличия от оригинала

* NavTCP слушает **5001**, а не 5000 — в оригинале конфликт с Flask;
* добавлены `SPEED`, `DROP`, `STATUS` и watchdog: хост пропал → STOP;
* бортовой режим не требует надземной камеры (прибытие по площади маркера);
* PICK_BLUE выполняется на Pi по завершении миссии (`PICK_BLUE_ON_FINISH`);
* cv2.aruco поддерживает и новые (≥4.7 ArucoDetector), и старые API.
