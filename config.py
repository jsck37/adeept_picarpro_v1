import os

# =============================================================================
# PiCar Pro v1 — Configuration
# =============================================================================
# Central configuration file for the Adeept PiCar-Pro robot.
# All hardware pins, I2C addresses, servo channels, sensor settings,
# camera parameters, DS4 gamepad options, and network defaults live here.
# Import what you need:  from config import SERVO_STEERING, CRANE_ARM_OPEN
# =============================================================================

# ---------------------------------------------------------------------------
# Simulation mode — set to True when running on a non-Raspberry Pi machine
# (e.g. your laptop) to test the web panel without real hardware.
#   Activation:  python3 boot.py --sim
#   Or env var:  PICARPRO_SIM=1 python3 boot.py
# When enabled, all hardware modules are replaced with lightweight stubs,
# WiFi wait is skipped, and simulated sensor data is provided.
# ---------------------------------------------------------------------------
SIM_MODE = os.environ.get('PICARPRO_SIM', '').strip() in ('1', 'true', 'yes')

# ---------------------------------------------------------------------------
# Logging — if True, the server writes a rotating log file to logs/server.txt
# (up to 100 MB, retained 7 days, compressed). If False, logs go to stderr.
# ---------------------------------------------------------------------------
log_file = False

# ---------------------------------------------------------------------------
# I2C bus number — almost always 1 on Raspberry Pi 3B+ / 4 / 5.
# Used by PCA9685 (servos), OLED, and MPU6050.
# ---------------------------------------------------------------------------
I2C_BUS = 1

# ---------------------------------------------------------------------------
# PCA9685 — 16-channel PWM / servo driver
#   Address on the I2C bus (default 0x40 with A0-A5 solder jumpers open).
#   Frequency for standard analog servos: 50 Hz (20 ms period).
# ---------------------------------------------------------------------------
PCA9685_SERVO_ADDR = 0x40
PCA9685_SERVO_FREQ = 50

# ---------------------------------------------------------------------------
# SSD1306 OLED — 128x64 monochrome display on I2C
# ---------------------------------------------------------------------------
OLED_I2C_ADDR = 0x3C
OLED_WIDTH = 128
OLED_HEIGHT = 64

# ---------------------------------------------------------------------------
# MPU6050 — 6-axis IMU (accelerometer + gyroscope)
# Default I2C address 0x68 (0x69 if AD0 pin is high).
# The driver auto-scans both addresses on startup.
# ---------------------------------------------------------------------------
MPU6050_ADDR = 0x68

# ---------------------------------------------------------------------------
# L298N motor driver — GPIO pins
#   Motor A = right side, Motor B = left side.
#   EN = PWM enable (speed control), IN1/IN2 = direction.
# ---------------------------------------------------------------------------
MOTOR_A_EN = 4
MOTOR_A_IN1 = 26
MOTOR_A_IN2 = 21
MOTOR_B_EN = 17
MOTOR_B_IN1 = 27
MOTOR_B_IN2 = 18

# ---------------------------------------------------------------------------
# Servo parameters — global limits for all PCA9685 channels
#   SERVO_COUNT       — how many servo channels are active (0..6).
#   SERVO_MIN_PULSE   — minimum pulse width in microseconds (500 us = 0 deg).
#   SERVO_MAX_PULSE   — maximum pulse width in microseconds (2400 us = 180 deg).
#   SERVO_INIT_ANGLE  — default angle when no specific init is set (90 deg).
# ---------------------------------------------------------------------------
SERVO_COUNT = 7
SERVO_MIN_PULSE = 500
SERVO_MAX_PULSE = 2400
SERVO_INIT_ANGLE = 90

# ---------------------------------------------------------------------------
# Servo channel mapping — which PCA9685 channel does what
#   0 = front-wheel steering
#   1 = camera pan  (left-right)
#   2 = camera tilt (up-down)
#   3 = unused
#   4 = unused
#   5 = crane grip  (tilt angle: low / mid / high)
#   6 = crane arm   (open / close the claw)
# ---------------------------------------------------------------------------
SERVO_STEERING = 0
SERVO_CAM_PAN = 1
SERVO_CAM_TILT = 2
SERVO_CRANE_ARM = 6
SERVO_CRANE_GRIP = 5

# ---------------------------------------------------------------------------
# Servo initial angles — per-channel overrides.
#   None = use a specialised default (see below), 90 = centre.
#   Channels 5 & 6 are set to None so that ServoController._init_pca9685()
#   can apply CRANE_ARM_OPEN and CRANE_GRIP_HIGH respectively.
# ---------------------------------------------------------------------------
SERVO_INIT_ANGLES = {
    0: 90,
    1: 90,
    2: 90,
    3: 90,
    4: 90,
    5: None,
    6: None,
}

# ---------------------------------------------------------------------------
# Servo angle limits — per-channel min/max angle clamping.
#   Each servo's movement range is clamped to these limits in
#   ServoController.set_angle(). Can be overridden at runtime via
#   the 'servo_set_limits' WebSocket command and persisted to servo_cal.json.
# ---------------------------------------------------------------------------
SERVO_LIMITS = {
    0: {"min": 30, "max": 150},
    1: {"min": 0, "max": 180},
    2: {"min": 0, "max": 180},
    3: {"min": 0, "max": 180},
    4: {"min": 0, "max": 180},
    5: {"min": 0, "max": 190},
    6: {"min": 0, "max": 180},
}

# ---------------------------------------------------------------------------
# Crane arm (channel 6) — claw open / close angles
#   CRANE_ARM_OPEN   = 80   — claw fully open  (relaxed spring).
#   CRANE_ARM_CLOSED = 150  — claw fully closed (gripping object).
# ---------------------------------------------------------------------------
CRANE_ARM_OPEN = 80
CRANE_ARM_CLOSED = 150

# ---------------------------------------------------------------------------
# Crane grip (channel 5) — tilt angle positions
#   CRANE_GRIP_LOW  = 0    — arm fully lowered (picking up objects).
#   CRANE_GRIP_MID  = 135  — arm at middle     (carrying position).
#   CRANE_GRIP_HIGH = 190  — arm fully raised   (default / safe position).
# ---------------------------------------------------------------------------
CRANE_GRIP_LOW = 0
CRANE_GRIP_MID = 135
CRANE_GRIP_HIGH = 190

# ---------------------------------------------------------------------------
# Passive buzzer — GPIO 24, driven by PWM for melodies.
# Set BUZZER_PASSIVE = False for an active buzzer (simple on/off).
# ---------------------------------------------------------------------------
BUZZER_PIN = 24
BUZZER_PASSIVE = True

# ---------------------------------------------------------------------------
# HC-SR04 ultrasonic sensor — trigger / echo GPIO pins and max range (meters)
# ---------------------------------------------------------------------------
ULTRASONIC_TRIGGER = 11
ULTRASONIC_ECHO = 8
ULTRASONIC_MAX_DISTANCE = 2.0

# ---------------------------------------------------------------------------
# WS2812 RGB LED strip — number of LEDs and global brightness (0-255).
# ---------------------------------------------------------------------------
LED_COUNT = 16
LED_BRIGHTNESS = 255

# ---------------------------------------------------------------------------
# Headlight / relay switches — GPIO pins for two switchable channels.
# Typically used for front headlights and rear lights via relays.
# ---------------------------------------------------------------------------
SWITCH_PINS = [6, 13]

# ---------------------------------------------------------------------------
# Robot-hat headlight — GPIO pin for the main front headlight.
# This is a simple ON/OFF light connected to pin 1 on the robot-hat board.
# ---------------------------------------------------------------------------
HEADLIGHT_PIN = 5

# ---------------------------------------------------------------------------
# IR line-tracking sensors — GPIO pins for left / right infrared detectors.
# Active LOW: sensor outputs 0 when over a dark line, 1 on white surface.
# ---------------------------------------------------------------------------
LINE_LEFT_PIN = 19
LINE_RIGHT_PIN = 20

# ---------------------------------------------------------------------------
# Camera settings — resolution, frame rate, JPEG quality, and flip options.
# ---------------------------------------------------------------------------
CAMERA_RESOLUTION = (640, 480)
CAMERA_FPS = 45
CAMERA_JPEG_QUALITY = 80
CAMERA_FLIP_HORIZONTAL = False
CAMERA_FLIP_VERTICAL = False

# ---------------------------------------------------------------------------
# Computer Vision — line-following parameters
# ---------------------------------------------------------------------------
CV_LINE_POS_1 = 440
CV_LINE_POS_2 = 380
CV_LINE_THRESHOLD = 80

# ---------------------------------------------------------------------------
# CV line-following — speed and steering tuning
# ---------------------------------------------------------------------------
CV_LINE_FOLLOW_SPEED = 35
CV_LINE_FOLLOW_STEER_GAIN = 0.8

# ---------------------------------------------------------------------------
# Voice control — Sherpa-NCNN offline speech recognition
# ---------------------------------------------------------------------------
VOICE_MODEL_PATH = "/opt/sherpa-ncnn/model"
VOICE_ALSA_DEVICE = "default"
VOICE_OUTPUT_FILE = "/tmp/picarpro_voice.txt"

# ---------------------------------------------------------------------------
# DualShock 4 / gamepad configuration
#   DS4_DEVICE_NAME       — substring to match in evdev device names.
#   DS4_DEADZONE          — axis dead zone (0..1).
#   DS4_STEER_SENSITIVITY — multiplier for steering response.
#   DS4_CAM_SENSITIVITY   — multiplier for camera pan/tilt speed.
#   DS4_HEARTBEAT_TIMEOUT — seconds without any event before disconnect.
#   DS4_WATCHDOG_INTERVAL — how often the watchdog thread checks.
#   DS4_READ_TIMEOUT      — select() timeout when reading events.
# ---------------------------------------------------------------------------
DS4_DEVICE_NAME = "Wireless Controller"
DS4_DEADZONE = 0.12
DS4_STEER_SENSITIVITY = 1.0
DS4_CAM_SENSITIVITY = 1.2
DS4_HEARTBEAT_TIMEOUT = 10.0
DS4_WATCHDOG_INTERVAL = 3.0
DS4_READ_TIMEOUT = 2.0

# ---------------------------------------------------------------------------
# DS4 — axis inversion and speed tuning
# ---------------------------------------------------------------------------
DS4_INVERT_LY = False
DS4_INVERT_RY = False
DS4_SPEED_MULT = 1.4
DS4_CRANE_STEP = 5
DS4_STEER_RANGE = 60

# ---------------------------------------------------------------------------
# Network — WebSocket (real-time control) and Flask (web UI / API) ports.
# ---------------------------------------------------------------------------
WEBSOCKET_PORT = 8888
FLASK_PORT = 5000

# ---------------------------------------------------------------------------
# WiFi Hotspot — fallback AP when no known WiFi is available.
# ---------------------------------------------------------------------------
HOTSPOT_SSID = "Adeept_Robot"
HOTSPOT_PASSWORD = "12345678"
HOTSPOT_IP = "10.42.0.1"

# ---------------------------------------------------------------------------
# Flask secret key — used for session signing.
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get('PICARPRO_SECRET_KEY', 'picarpro')

# ---------------------------------------------------------------------------
# Steering angle map — maps direction commands to servo angles.
# ---------------------------------------------------------------------------
STEER_MAP = {
    'forward': 90, 'backward': 90, 'left': 150, 'right': 30,
    'forward_left': 120, 'forward_right': 60,
    'backward_left': 120, 'backward_right': 60, 'stop': 90,
}

# ---------------------------------------------------------------------------
# OLED scroll text — message that scrolls on the bottom line of the display.
# ---------------------------------------------------------------------------
OLED_SCROLL_TEXT = "modded by turik with <3 from 8241117 "

# ---------------------------------------------------------------------------
# Motion defaults
# ---------------------------------------------------------------------------
DEFAULT_SPEED = 50
TURN_RADIUS_MIN = 0.2
TURN_RADIUS_MAX = 1.0
