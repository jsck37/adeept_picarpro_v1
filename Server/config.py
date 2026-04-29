"""
Centralized configuration for PiCar Pro v1 (optimized).
All hardware pins, I2C addresses, and runtime parameters in one place.

Hardware setup (user-specific):
- Steering servo: PCA9685 channel 0 (front wheels left-right)
- motorA: RIGHT motor (GPIO: EN=4, IN1=26, IN2=21)
- motorB: LEFT motor  (GPIO: EN=17, IN1=27, IN2=18)
- Left headlight:  Switch port 1 (GPIO 6)
- Right headlight: Switch port 2 (GPIO 13)
- Crane/manipulator: DISABLED (not connected)
- WS2812 backlight + batteries + OLED + camera on shared power
"""

# =============================================================================
# Hardware version: set to 1 for original PiCar Pro v1 hardware
# Change to 2 if you upgrade the motor driver board
# =============================================================================
HARDWARE_VERSION = 1

# =============================================================================
# I2C Configuration
# =============================================================================
I2C_BUS = 1  # Default I2C bus on Raspberry Pi

# PCA9685 Servo PWM controller
PCA9685_SERVO_ADDR = 0x40  # v1 hardware
PCA9685_SERVO_FREQ = 50    # 50Hz for servos

# PCA9685 Motor controller (v2 hardware uses 0x5F, v1 uses direct GPIO)
PCA9685_MOTOR_ADDR = 0x5F  # Only used when HARDWARE_VERSION == 2

# ADS7830 ADC for battery voltage
ADS7830_ADDR = 0x48
ADS7830_CHANNEL = 0

# SSD1306 OLED display
OLED_I2C_ADDR = 0x3C
OLED_WIDTH = 128
OLED_HEIGHT = 64

# MPU6050 IMU
MPU6050_ADDR = 0x68

# =============================================================================
# Motor Configuration (v1: direct GPIO, v2: PCA9685)
# =============================================================================
# v1 GPIO motor pins
MOTOR_A_EN = 4
MOTOR_A_IN1 = 26
MOTOR_A_IN2 = 21
MOTOR_B_EN = 17
MOTOR_B_IN1 = 27
MOTOR_B_IN2 = 18

# v2 PCA9685 motor channels (only when HARDWARE_VERSION == 2)
MOTOR_M1_IN1 = 15
MOTOR_M1_IN2 = 14
MOTOR_M2_IN1 = 12
MOTOR_M2_IN2 = 13
MOTOR_M3_IN1 = 11
MOTOR_M3_IN2 = 10
MOTOR_M4_IN1 = 8
MOTOR_M4_IN2 = 9

# =============================================================================
# Servo Configuration
# =============================================================================
SERVO_COUNT = 3          # Only steering + camera pan/tilt (crane DISABLED)
SERVO_MIN_PULSE = 500   # microseconds
SERVO_MAX_PULSE = 2400  # microseconds
SERVO_INIT_ANGLE = 90   # default center position

# Servo channel assignments
SERVO_STEERING = 0      # Front wheel steering (left-right rotation)
SERVO_CAM_PAN = 1       # Camera pan
SERVO_CAM_TILT = 2      # Camera tilt

# Crane/manipulator is DISABLED — not physically connected
CRANE_ENABLED = False

# =============================================================================
# Ultrasonic Sensor
# =============================================================================
ULTRASONIC_TRIGGER = 11
ULTRASONIC_ECHO = 8
ULTRASONIC_MAX_DISTANCE = 2.0  # meters

# =============================================================================
# WS2812 LED Strip
# =============================================================================
LED_COUNT = 16
LED_BRIGHTNESS = 255
LED_SPI_BUS = 0
LED_SPI_DEVICE = 0

# =============================================================================
# GPIO Switches (3-port LED/relay)
# Port 0 = GPIO 5  (unused / backlight)
# Port 1 = GPIO 6  (LEFT headlight)
# Port 2 = GPIO 13 (RIGHT headlight)
# =============================================================================
SWITCH_PINS = [5, 6, 13]
HEADLIGHT_LEFT_PORT = 1   # port1 = left headlight
HEADLIGHT_RIGHT_PORT = 2  # port2 = right headlight

# =============================================================================
# Line Tracker IR Sensors
# =============================================================================
LINE_LEFT_PIN = 20
LINE_MIDDLE_PIN = 16
LINE_RIGHT_PIN = 19

# =============================================================================
# Buzzer
# =============================================================================
BUZZER_PIN = 18

# =============================================================================
# Camera Configuration
# =============================================================================
CAMERA_RESOLUTION = (640, 480)
CAMERA_FPS = 20           # Target frame rate (was unlimited in v1!)
CAMERA_JPEG_QUALITY = 70  # 0-100, lower = less CPU usage
CAMERA_FLIP_HORIZONTAL = False
CAMERA_FLIP_VERTICAL = False

# =============================================================================
# OpenCV / Computer Vision
# =============================================================================
# Default color tracking HSV range (green)
CV_COLOR_LOWER_H = 35
CV_COLOR_LOWER_S = 43
CV_COLOR_LOWER_V = 46
CV_COLOR_UPPER_H = 77
CV_COLOR_UPPER_S = 255
CV_COLOR_UPPER_V = 255

# Line following scan positions
CV_LINE_POS_1 = 440  # Bottom scan line
CV_LINE_POS_2 = 380  # Upper scan line

# Watchdog motion detection sensitivity
CV_WATCHDOG_THRESHOLD = 25

# =============================================================================
# Network Configuration
# =============================================================================
WEBSOCKET_PORT = 8888
FLASK_PORT = 5000
ZMQ_PORT = 5555
GUI_SERVER_PORT = 10223

# WiFi Hotspot
HOTSPOT_SSID = "Adeept_Robot"
HOTSPOT_PASSWORD = "12345678"
HOTSPOT_GATEWAY = "192.168.4.1"

# =============================================================================
# Battery Monitoring — DISABLED (no ADS7830 hardware module)
# =============================================================================
BATTERY_ENABLED = False
BATTERY_FULL_VOLTAGE = 8.4
BATTERY_WARNING_VOLTAGE = 6.3
BATTERY_VOLTAGE_RATIO = 0.25  # Voltage divider ratio (R15=3k, R17=1k)

# =============================================================================
# Motion Parameters
# =============================================================================
DEFAULT_SPEED = 50         # 0-100
TURN_RADIUS_MIN = 0.3
TURN_RADIUS_MAX = 1.0
RADAR_SCAN_SPEED = 3       # Servo speed during radar scan

# =============================================================================
# Voice Commands (optional, requires sherpa-ncnn)
# =============================================================================
VOICE_MODEL_PATH = "/home/pi/sherpa-ncnn/sherpa-ncnn-streaming-zipformer-bilingual-zh-en-2023-02-13"
VOICE_ALSA_DEVICE = "plughw:2,0"
VOICE_OUTPUT_FILE = "/tmp/voice_command.txt"
