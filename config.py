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
# Ultrasonic sensor — set to False if the HC-SR04 module is not installed.
# When disabled, ultrasonic-dependent features (radar, auto-obstacle, keepDistance)
# will gracefully skip initialization.
# ---------------------------------------------------------------------------
ULTRASONIC_ENABLED = False

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
    0: 90,   # steering — centre
    1: 90,   # camera pan — centre
    2: 90,   # camera tilt — centre
    3: 90,   # unused
    4: 90,   # unused
    5: None, # crane grip — defaults to CRANE_GRIP_HIGH (raised)
    6: None, # crane arm  — defaults to CRANE_ARM_OPEN (claw open)
}

# ---------------------------------------------------------------------------
# Crane arm (channel 6) — claw open / close angles
#   CRANE_ARM_OPEN   = 80   — claw fully open  (relaxed spring).
#   CRANE_ARM_CLOSED = 150  — claw fully closed (gripping object).
#   The arm only does open/close toggling, controlled by a single button.
# ---------------------------------------------------------------------------
CRANE_ARM_OPEN = 80
CRANE_ARM_CLOSED = 150

# ---------------------------------------------------------------------------
# Crane grip (channel 5) — tilt angle positions
#   CRANE_GRIP_LOW  = 0    — arm fully lowered (picking up objects).
#   CRANE_GRIP_MID  = 135  — arm at middle     (carrying position).
#   CRANE_GRIP_HIGH = 190  — arm fully raised   (default / safe position).
#
#   Because grip is controlled by a single button (gamepad ▢ / web UI),
#   it cycles through positions: low -> mid -> high -> mid -> low -> ...
#   The direction reverses at each endpoint so every press moves
#   to the next adjacent position.
#
#   NOTE: CRANE_GRIP_HIGH = 190 exceeds the standard 180-degree range,
#   but the servo hardware and PCA9685 driver support it with the
#   configured actuation_range. The ServoController.set_angle() clamp
#   has been extended to allow up to 190 for grip positions.
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
# Only effective when ULTRASONIC_ENABLED = True.
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
# IR line-tracking sensors — GPIO pins for left / right infrared detectors.
# Active LOW: sensor outputs 0 when over a dark line, 1 on white surface.
# ---------------------------------------------------------------------------
LINE_LEFT_PIN = 20
LINE_RIGHT_PIN = 19

# ---------------------------------------------------------------------------
# Camera settings — resolution, frame rate, JPEG quality, and flip options.
# CAMERA_FLIP_HORIZONTAL / CAMERA_FLIP_VERTICAL — set True to mirror/flip
# the image if the camera ribbon cable is inserted upside-down.
# ---------------------------------------------------------------------------
CAMERA_RESOLUTION = (640, 480)
CAMERA_FPS = 45
CAMERA_JPEG_QUALITY = 80
CAMERA_FLIP_HORIZONTAL = False
CAMERA_FLIP_VERTICAL = False

# ---------------------------------------------------------------------------
# Computer Vision — line-following parameters
#   CV_LINE_POS_1 / POS_2 — Y-coordinates (pixels from top) of two
#     horizontal scan lines used to detect the centre of the black line.
#   CV_LINE_THRESHOLD — binarisation threshold (0 = auto-Otsu).
# ---------------------------------------------------------------------------
CV_LINE_POS_1 = 440
CV_LINE_POS_2 = 380
CV_LINE_THRESHOLD = 80

# ---------------------------------------------------------------------------
# CV line-following — speed and steering tuning
#   CV_LINE_FOLLOW_SPEED      — base motor speed (0-100 %).
#   CV_LINE_FOLLOW_STEER_GAIN — multiplier for steering correction.
#   CV_LINE_FOLLOW_SCAN_Y_RATIO — vertical position (0=top, 1=bottom)
#     of the primary scan line, as a fraction of frame height.
# ---------------------------------------------------------------------------
CV_LINE_FOLLOW_SPEED = 35
CV_LINE_FOLLOW_STEER_GAIN = 0.8
CV_LINE_FOLLOW_SCAN_Y_RATIO = 0.7

# ---------------------------------------------------------------------------
# Voice control — Sherpa-NCNN offline speech recognition
#   VOICE_MODEL_PATH  — directory containing the NCNN model files.
#   VOICE_ALSA_DEVICE — ALSA PCM device name for the microphone.
#   VOICE_OUTPUT_FILE — temporary file where sherpa-ncnn writes
#     recognised text; the server polls this file for new commands.
# ---------------------------------------------------------------------------
VOICE_MODEL_PATH = "/opt/sherpa-ncnn/model"
VOICE_ALSA_DEVICE = "default"
VOICE_OUTPUT_FILE = "/tmp/picarpro_voice.txt"

# ---------------------------------------------------------------------------
# DualShock 4 / gamepad configuration
#   DS4_ENABLED           — master switch; set False to disable gamepad.
#   DS4_DEVICE_NAME       — substring to match in evdev device names.
#   DS4_DEADZONE          — axis dead zone (0..1); sticks below this
#     threshold report zero, preventing drift.
#   DS4_STEER_SENSITIVITY — multiplier for steering response.
#   DS4_CAM_SENSITIVITY   — multiplier for camera pan/tilt speed.
#   DS4_HEARTBEAT_TIMEOUT — seconds without any event before the
#     watchdog declares the controller disconnected.
#   DS4_WATCHDOG_INTERVAL — how often the watchdog thread checks.
#   DS4_READ_TIMEOUT      — select() timeout when reading events.
# ---------------------------------------------------------------------------
DS4_ENABLED = True
DS4_DEVICE_NAME = "Wireless Controller"
DS4_DEADZONE = 0.12
DS4_STEER_SENSITIVITY = 1.0
DS4_CAM_SENSITIVITY = 0.3
DS4_HEARTBEAT_TIMEOUT = 10.0
DS4_WATCHDOG_INTERVAL = 3.0
DS4_READ_TIMEOUT = 2.0

# ---------------------------------------------------------------------------
# DS4 — axis inversion and speed tuning
#   DS4_INVERT_LY / DS4_INVERT_RY — flip left/right stick Y axis.
#     Useful if the controller is mounted upside-down.
#   DS4_SPEED_MULT — multiplier applied to base speed when driving
#     with the left stick (allows speeds > 100 % for short bursts).
#   DS4_CRANE_STEP — degrees per step when smoothly moving crane servos.
#   DS4_STEER_RANGE — maximum steering angle offset from centre (90 deg).
#     90 +/- 60 => steering sweeps 30..150 degrees.
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
#   Only works on Bookworm+ (Debian 12+) with NetworkManager.
#   The setup script creates a systemd service that auto-starts the
#   hotspot if the Pi cannot connect to any saved WiFi network.
# ---------------------------------------------------------------------------
HOTSPOT_SSID = "Adeept_Robot"
HOTSPOT_PASSWORD = "12345678"
HOTSPOT_IP = "10.42.0.1"

# ---------------------------------------------------------------------------
# Flask secret key — used for session signing.
# Override via environment variable PICARPRO_SECRET_KEY in production.
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get('PICARPRO_SECRET_KEY', 'picarpro')

# ---------------------------------------------------------------------------
# Steering angle map — maps direction commands to servo angles.
# 90 = centre, 150 = full left, 30 = full right.
# Used by both the web UI and WebSocket command handler.
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
#   DEFAULT_SPEED  — initial motor speed (0-100 %).
#   TURN_RADIUS_MIN / MAX — clamping range for the differential steering
#     radius parameter (0.2 = tight turn, 1.0 = gentle curve).
# ---------------------------------------------------------------------------
DEFAULT_SPEED = 50
TURN_RADIUS_MIN = 0.2
TURN_RADIUS_MAX = 1.0
