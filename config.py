import os
from pathlib import Path

# =============================================================================
# PiCar-Pro — единая конфигурация (слияние jsck37 + Adeept v1 + Adeept v2)
# =============================================================================
# Все настройки железа, сети, камеры и функций собраны здесь.
# Импорт:  from config import SERVO_STEERING, CRANE_ARM_OPEN
# =============================================================================

# Корень проекта (каталог, где лежит этот файл)
BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# I2C шина — на Raspberry Pi 3B+/4/5 это шина №1 (GPIO2=SDA, GPIO3=SCL)
# Используется: PCA9685 (серво), SSD1306 (OLED), MPU6050 (IMU), ADS7830 (АЦП)
# ---------------------------------------------------------------------------
I2C_BUS = 1

# PCA9685 — 16-канальный PWM-драйвер сервоприводов
PCA9685_SERVO_ADDR = 0x40
PCA9685_SERVO_FREQ = 50

# SSD1306 OLED — 128x64 монохромный дисплей
OLED_I2C_ADDR = 0x3C
OLED_WIDTH = 128
OLED_HEIGHT = 64

# MPU6050 — 6-осевой IMU (адрес 0x68 или 0x69 при AD0=HIGH; сканируются оба)
MPU6050_ADDR = 0x68

# ---------------------------------------------------------------------------
# L298N мотор-драйвер — GPIO пины
#   Мотор A = правая сторона, Мотор B = левая сторона
# ---------------------------------------------------------------------------
MOTOR_A_EN = 4
MOTOR_A_IN1 = 26
MOTOR_A_IN2 = 21
MOTOR_B_EN = 17
MOTOR_B_IN1 = 27
MOTOR_B_IN2 = 18
MOTOR_PWM_FREQ = 1000

# ---------------------------------------------------------------------------
# Сервоприводы
# ---------------------------------------------------------------------------
SERVO_COUNT = 7
SERVO_MIN_PULSE = 500
SERVO_MAX_PULSE = 2400
SERVO_INIT_ANGLE = 90

SERVO_STEERING = 0     # рулевое управление
SERVO_CAM_PAN = 1      # панорама камеры
SERVO_CAM_TILT = 2     # наклон камеры
SERVO_CRANE_ARM = 6    # клешня (открыть/закрыть)
SERVO_CRANE_GRIP = 5   # наклон клешни

SERVO_INIT_ANGLES = {
    0: 90, 1: 90, 2: 90, 3: 90, 4: 90,
    5: None,  # -> CRANE_GRIP_HIGH
    6: None,  # -> CRANE_ARM_OPEN
}

SERVO_LIMITS = {
    0: {"min": 30, "max": 150},
    1: {"min": 0, "max": 180},
    2: {"min": 0, "max": 180},
    3: {"min": 0, "max": 180},
    4: {"min": 0, "max": 180},
    5: {"min": 0, "max": 190},
    6: {"min": 0, "max": 180},
}

# Клешня
CRANE_ARM_OPEN = 80
CRANE_ARM_CLOSED = 150
CRANE_GRIP_LOW = 0
CRANE_GRIP_MID = 135
CRANE_GRIP_HIGH = 190

# ---------------------------------------------------------------------------
# Зуммер — GPIO 24, PWM для мелодий (BUZZER_PASSIVE=False для активного)
# ---------------------------------------------------------------------------
BUZZER_PIN = 24
BUZZER_PASSIVE = True

# ---------------------------------------------------------------------------
# HC-SR04 ультразвуковой датчик
# ---------------------------------------------------------------------------
ULTRASONIC_TRIGGER = 11
ULTRASONIC_ECHO = 8
ULTRASONIC_MAX_DISTANCE = 2.0  # метры

# ---------------------------------------------------------------------------
# WS2812 RGB LED-лента через SPI
# ---------------------------------------------------------------------------
LED_COUNT = 16
LED_BRIGHTNESS = 200

# Фары/реле и основная фара
SWITCH_PINS = [6, 13]
HEADLIGHT_PIN = 5

# ИК-датчики линии (active LOW над тёмной линией)
LINE_LEFT_PIN = 19
LINE_MIDDLE_PIN = 16
LINE_RIGHT_PIN = 20

# ---------------------------------------------------------------------------
# Камера
# ---------------------------------------------------------------------------
CAMERA_RESOLUTION = (640, 480)
CAMERA_FPS = 30
CAMERA_JPEG_QUALITY = 75
CAMERA_FLIP_HORIZONTAL = False
CAMERA_FLIP_VERTICAL = False

# CV следование по линии
CV_LINE_POS_1 = 440
CV_LINE_POS_2 = 380
CV_LINE_THRESHOLD = 80
CV_LINE_FOLLOW_SPEED = 35
CV_LINE_FOLLOW_STEER_GAIN = 0.8

# CV findColor — следование за цветным объектом (порт из v1/v2 findColor)
FINDCOLOR_AREA_MIN = 800       # минимальная площадь контура (px)
FINDCOLOR_TOLERANCE = 25       # мёртвая зона от центра (px, доля от полуширины)
FINDCOLOR_SPEED = 30
FINDCOLOR_STEER_RANGE = 45

# CV watchDog — детектор движения (порт из v1/v2 Camera_WatchDog)
WATCHDOG_THRESHOLD = 25        # порог разницы кадров (0-255)
WATCHDOG_MIN_AREA = 2000       # минимальная площадь движения (px)
WATCHDOG_COOLDOWN_S = 5.0      # пауза между тревогами

# ---------------------------------------------------------------------------
# Steady camera — стабилизация наклона камеры по MPU6050 (порт из v2)
# ---------------------------------------------------------------------------
STEADY_CAMERA_ENABLED = True
STEADY_COMPENSATION_GAIN = 0.8   # градус серво на градус тангажа
STEADY_UPDATE_INTERVAL = 0.1

# ---------------------------------------------------------------------------
# Батарея / напряжение — слияние подходов v2 (ADS7830) и jsck37 (dmesg)
#   ADS7830_ADDR: I2C-адрес АЦП; если АЦП нет — используется dmesg-мониторинг
#   просадок питания (under-voltage) как в jsck37.
# ---------------------------------------------------------------------------
ADS7830_ADDR = 0x48
ADS7830_CHANNEL = 0
BATTERY_VREF = 8.4            # полное напряжение батареи (2S LiPo)
BATTERY_MIN_V = 6.3           # порог тревоги (v2: 6.3 В)
BATTERY_MEDIAN_WINDOW = 10    # медианный фильтр (выборок)
VOLTAGE_CHECK_SOURCE = 'auto'   # 'dmesg' | 'vcgencmd' | 'auto'
LOW_VOLTAGE_THRESHOLD_V = 1.2
VOLTAGE_CHECK_INTERVAL_S = 2.0

# ---------------------------------------------------------------------------
# Голосовое управление — Sherpa-NCNN офлайн распознавание
# ---------------------------------------------------------------------------
VOICE_MODEL_PATH = "/opt/sherpa-ncnn/model"
VOICE_ALSA_DEVICE = "default"
VOICE_OUTPUT_FILE = "/tmp/picarpro_voice.txt"

# ---------------------------------------------------------------------------
# DualShock 4 / геймпад
# ---------------------------------------------------------------------------
DS4_DEVICE_NAME = "Wireless Controller"
DS4_DEADZONE = 0.12
DS4_STEER_SENSITIVITY = 1.0
DS4_CAM_SENSITIVITY = 1.2
DS4_HEARTBEAT_TIMEOUT = 10.0
DS4_WATCHDOG_INTERVAL = 3.0
DS4_READ_TIMEOUT = 2.0
DS4_INVERT_LY = False
DS4_INVERT_RY = False
DS4_SPEED_MULT = 1.4
DS4_CRANE_STEP = 5
DS4_STEER_RANGE = 60

# ---------------------------------------------------------------------------
# Сеть
# ---------------------------------------------------------------------------
WEBSOCKET_PORT = 8888
FLASK_PORT = 5000

# WiFi Hotspot — резервная точка доступа (wifi_hotspot_manager.sh, порт из v2)
HOTSPOT_SSID = "Adeept_Robot"
HOTSPOT_PASSWORD = "12345678"
HOTSPOT_IP = "10.42.0.1"

# ---------------------------------------------------------------------------
# Безопасность
#   SECRET_KEY: подпись сессий. Если не задан через окружение — генерируется
#   случайный и сохраняется в instance/secret_key.
#   AUTH_TOKEN: если задан (окружение PICARPRO_TOKEN) — все /api и /cmd
#   и WebSocket требуют токен (заголовок X-Auth-Token, ?token= или
#   первое WS-сообщение {"cmd":"auth"}). Пусто = авторизация отключена
#   (доверенная домашняя сеть).
#   CORS_ORIGINS: белый список источников для CORS. Пусто = CORS отключён
#   (UI отдаётся с того же источника).
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get('PICARPRO_SECRET_KEY', '')
AUTH_TOKEN = os.environ.get('PICARPRO_TOKEN', '')
CORS_ORIGINS = [o.strip() for o in os.environ.get('PICARPRO_CORS', '').split(',') if o.strip()]
MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MiB на запрос

# ---------------------------------------------------------------------------
# Маппинг углов руля
# ---------------------------------------------------------------------------
STEER_MAP = {
    'forward': 90, 'backward': 90, 'left': 150, 'right': 30,
    'forward_left': 120, 'forward_right': 60,
    'backward_left': 120, 'backward_right': 60, 'stop': 90,
}

OLED_LOW_VOLTAGE_TEXT = "Low voltage warning Please check your power supply"

# Движение по умолчанию
DEFAULT_SPEED = 50
TURN_RADIUS_MIN = 0.2
TURN_RADIUS_MAX = 1.0

# Калибровка серво / конфиг BT — файлы состояния
SERVO_CAL_FILE = BASE_DIR / 'servo_cal.json'
BT_CONFIG_FILE = BASE_DIR / 'bt_config.json'
