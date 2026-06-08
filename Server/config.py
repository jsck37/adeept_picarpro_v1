"""PiCar Pro v1 — hardware configuration.

Centralised constants for all subsystems.  Every module imports from
here so that magic numbers live in exactly one place.

Changes from v1:
  - DS4 stick inversion config added
  - Speed multiplier for gamepad
  - Crane smooth step size config
  - Drift mode config
  - Hotspot IP for captive portal
"""

import os

# ── Feature flags ──────────────────────────────────────────────────────
ULTRASONIC_ENABLED  = False
LINE_TRACKER_ENABLED = True
CRANE_ENABLED       = True
DS4_ENABLED         = True

# ── Logging ────────────────────────────────────────────────────────────
log_file = False           # True = write logs to file, False = console only

# ── I2C bus ────────────────────────────────────────────────────────────
I2C_BUS = 1

# ── PCA9685 Servo PWM driver ───────────────────────────────────────────
PCA9685_SERVO_ADDR = 0x40
PCA9685_SERVO_FREQ = 50

# ── SSD1306 OLED display ──────────────────────────────────────────────
OLED_I2C_ADDR = 0x3C
OLED_WIDTH    = 128
OLED_HEIGHT   = 64

# ── MPU6050 IMU ───────────────────────────────────────────────────────
MPU6050_ADDR = 0x68

# ── Motor pins (L298N H-bridge) ───────────────────────────────────────
MOTOR_A_EN  = 4
MOTOR_A_IN1 = 26
MOTOR_A_IN2 = 21
MOTOR_B_EN  = 17
MOTOR_B_IN1 = 27
MOTOR_B_IN2 = 18

# ── Servo channels & pulse limits ──────────────────────────────────────
SERVO_COUNT      = 6
SERVO_MIN_PULSE  = 500
SERVO_MAX_PULSE  = 2400
SERVO_INIT_ANGLE = 90

# Channel indices on the PCA9685
SERVO_STEERING   = 0
SERVO_CAM_PAN    = 1
SERVO_CAM_TILT   = 2
SERVO_CLAW_ARM   = 4
SERVO_CLAW_GRIP  = 5

# ── Claw angle limits ─────────────────────────────────────────────────
# For the claw-arm servo a LOWER angle raises the arm, a HIGHER angle
# lowers it.  For the grip servo a HIGHER angle closes the grip.
#
# D-PAD mapping (FIXED in v1):
#   hat_y > 0  (D-pad DOWN)  → CLAW_ARM_DOWN  (arm goes DOWN)
#   hat_y < 0  (D-pad UP)    → CLAW_ARM_UP    (arm goes UP)
CLAW_ARM_UP      = 30    # arm raised  (low angle = arm up)
CLAW_ARM_DOWN    = 120   # arm lowered (high angle = arm down)
CLAW_GRIP_OPEN   = 60    # grip opened (low angle = open)
CLAW_GRIP_CLOSED = 130   # grip closed (high angle = closed)

# ── Buzzer ─────────────────────────────────────────────────────────────
BUZZER_PIN      = 24
BUZZER_PASSIVE  = True

# ── Ultrasonic sensor (disabled) ──────────────────────────────────────
ULTRASONIC_TRIGGER      = 11
ULTRASONIC_ECHO         = 8
ULTRASONIC_MAX_DISTANCE = 2.0   # metres

# ── WS2812 RGB LED strip ──────────────────────────────────────────────
LED_COUNT     = 16
LED_BRIGHTNESS = 255

# ── Headlight relay switches ──────────────────────────────────────────
SWITCH_PINS = [6, 13]

# ── Line-tracker IR sensors (2 sensors: left + right) ──────────────
LINE_LEFT_PIN   = 20
LINE_RIGHT_PIN  = 19

# ── Camera ─────────────────────────────────────────────────────────────
CAMERA_RESOLUTION      = (640, 480)
CAMERA_FPS             = 30
CAMERA_JPEG_QUALITY    = 80
CAMERA_FLIP_HORIZONTAL = False
CAMERA_FLIP_VERTICAL   = False

# ── OpenCV — line detection ────────────────────────────────────────
CV_LINE_POS_1        = 440     # horizontal scan-line 1 (px from top)
CV_LINE_POS_2        = 380     # horizontal scan-line 2
CV_LINE_THRESHOLD    = 80

# ── OpenCV — autonomous line following ─────────────────────────────────
CV_LINE_FOLLOW_SPEED       = 35    # motor speed (0-100) while following
CV_LINE_FOLLOW_STEER_GAIN  = 0.8   # proportional gain for steering PID
CV_LINE_FOLLOW_SCAN_Y_RATIO = 0.7  # scan row as fraction of frame height

# ── Voice recognition (Sherpa-NCNN offline ASR) ───────────────────────
VOICE_MODEL_PATH  = "/opt/sherpa-ncnn/model"       # sherpa-ncnn model directory
VOICE_ALSA_DEVICE = "default"                        # ALSA capture device
VOICE_OUTPUT_FILE = "/tmp/picarpro_voice.txt"        # file sherpa writes results to

# ── DS4 Bluetooth controller ──────────────────────────────────────────
DS4_DEVICE_NAME      = "Wireless Controller"
DS4_DEADZONE         = 0.12
DS4_STEER_SENSITIVITY = 1.0
DS4_CAM_SENSITIVITY  = 0.8
DS4_HEARTBEAT_TIMEOUT = 10.0   # seconds without events -> consider disconnected
DS4_WATCHDOG_INTERVAL = 3.0    # seconds between watchdog checks
DS4_READ_TIMEOUT      = 2.0    # select() timeout for event reading

# ── DS4 v1 settings ──────────────────────────────────────────────────
DS4_INVERT_LY        = True    # Invert left stick Y (push forward = forward)
DS4_INVERT_RY        = True    # Invert right stick Y (push up = tilt up / crane up)
DS4_SPEED_MULT       = 1.4    # Speed multiplier for gamepad control (1.4 = 40% faster)
DS4_CRANE_STEP       = 5       # Crane servo smooth step in degrees
DS4_DRIFT_STEER_RANGE = 70     # Max steer angle range in drift mode (from centre)
DS4_STEER_RANGE      = 60     # Max steer angle range in normal mode (from centre)

# ── Drift mode ───────────────────────────────────────────────────────
# The robot is rear-wheel drive with front-wheel steering.
# Drift mode over-steers the front wheels and applies aggressive
# rear-wheel power to induce oversteer / drift.
DRIFT_ENABLED        = True
DRIFT_STEER_MULT     = 1.2    # Steering angle multiplier in drift mode
DRIFT_POWER_MULT     = 1.2    # Motor power multiplier in drift mode
DRIFT_INNER_BRAKE    = 0.6    # Reduce inner wheel to 60% to induce slide

# ── Network ────────────────────────────────────────────────────────────
WEBSOCKET_PORT = 8888
FLASK_PORT     = 5000

# ── WiFi hotspot ──────────────────────────────────────────────────────
HOTSPOT_SSID     = "Adeept_Robot"
HOTSPOT_PASSWORD = "12345678"
HOTSPOT_IP       = "10.42.0.1"   # Gateway IP of the WiFi hotspot (for web UI access)

# ── Flask ────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get('PICARPRO_SECRET_KEY', 'picarpro')

# ── Steering map (direction → servo angle) ────────────────────────────
STEER_MAP = {
    'forward': 90, 'backward': 90, 'left': 150, 'right': 30,
    'forward_left': 120, 'forward_right': 60,
    'backward_left': 120, 'backward_right': 60, 'stop': 90,
}

# ── OLED scroll text ──────────────────────────────────────────────────
OLED_SCROLL_TEXT = "modded by turik <3 from 8241117 "

# ── Motion defaults ────────────────────────────────────────────────────
DEFAULT_SPEED    = 50
TURN_RADIUS_MIN = 0.2      # Reduced from 0.3 for tighter turns
TURN_RADIUS_MAX = 1.0
