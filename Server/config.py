"""PiCar Pro v1 — centralized hardware configuration."""

# ── Feature flags — set False if hardware is not present ────────────────
ULTRASONIC_ENABLED = False      # HC-SR04 not available
LINE_TRACKER_ENABLED = False    # IR line tracker not available

# ── I2C ─────────────────────────────────────────────────────────────────
I2C_BUS = 1

# ── PCA9685 Servo PWM controller ────────────────────────────────────────
PCA9685_SERVO_ADDR = 0x40
PCA9685_SERVO_FREQ = 50

# ── SSD1306 OLED ────────────────────────────────────────────────────────
OLED_I2C_ADDR = 0x3C
OLED_WIDTH = 128
OLED_HEIGHT = 64

# ── MPU6050 IMU ─────────────────────────────────────────────────────────
MPU6050_ADDR = 0x68

# ── Motor pins (L298N direct GPIO) ─────────────────────────────────────
MOTOR_A_EN = 4
MOTOR_A_IN1 = 26
MOTOR_A_IN2 = 21
MOTOR_B_EN = 17
MOTOR_B_IN1 = 27
MOTOR_B_IN2 = 18

# ── Servos ──────────────────────────────────────────────────────────────
SERVO_COUNT = 6                # 0=steering, 1=cam pan, 2=cam tilt, 3=unused, 4=claw arm, 5=claw grip
SERVO_MIN_PULSE = 500
SERVO_MAX_PULSE = 2400
SERVO_INIT_ANGLE = 90
SERVO_STEERING = 0
SERVO_CAM_PAN = 1
SERVO_CAM_TILT = 2
SERVO_CLAW_ARM = 4             # Crane arm up/down (RobotHat ch4)
SERVO_CLAW_GRIP = 5            # Claw open/close (RobotHat ch5)
CRANE_ENABLED = True           # Claw crane is now connected

# ── Crane/Claw angle limits ─────────────────────────────────────────────
CLAW_ARM_UP = 30               # Arm raised position
CLAW_ARM_DOWN = 120             # Arm lowered position
CLAW_GRIP_OPEN = 60            # Claw open (released)
CLAW_GRIP_CLOSED = 130         # Claw closed (grabbed)

# ── Buzzer ──────────────────────────────────────────────────────────────
BUZZER_PIN = 24
BUZZER_PASSIVE = False         # Active buzzer on RobotHat v1

# ── Ultrasonic HC-SR04 (only used when ULTRASONIC_ENABLED=True) ────────
ULTRASONIC_TRIGGER = 11
ULTRASONIC_ECHO = 8
ULTRASONIC_MAX_DISTANCE = 2.0

# ── WS2812 LED (rpi_ws281x on GPIO 12) ─────────────────────────────────
LED_COUNT = 16
LED_BRIGHTNESS = 255

# ── Headlight switches ─────────────────────────────────────────────────
SWITCH_PINS = [6, 13]

# ── Line tracker IR sensors (only used when LINE_TRACKER_ENABLED=True) ──
LINE_LEFT_PIN = 20
LINE_MIDDLE_PIN = 16
LINE_RIGHT_PIN = 19

# ── Camera ──────────────────────────────────────────────────────────────
CAMERA_RESOLUTION = (640, 480)
CAMERA_FPS = 30
CAMERA_JPEG_QUALITY = 80
CAMERA_FLIP_HORIZONTAL = False
CAMERA_FLIP_VERTICAL = False

# ── OpenCV defaults ─────────────────────────────────────────────────────
CV_COLOR_LOWER_H = 35
CV_COLOR_LOWER_S = 43
CV_COLOR_LOWER_V = 46
CV_COLOR_UPPER_H = 77
CV_COLOR_UPPER_S = 255
CV_COLOR_UPPER_V = 255
CV_LINE_POS_1 = 440
CV_LINE_POS_2 = 380
CV_LINE_THRESHOLD = 80
CV_WATCHDOG_THRESHOLD = 25
CV_WATCHDOG_BLUR_SIZE = (7, 7)

# ── Network ─────────────────────────────────────────────────────────────
WEBSOCKET_PORT = 8888
FLASK_PORT = 5000

# ── WiFi Hotspot ────────────────────────────────────────────────────────
HOTSPOT_SSID = "Adeept_Robot"
HOTSPOT_PASSWORD = "12345678"

# ── Motion ──────────────────────────────────────────────────────────────
DEFAULT_SPEED = 50
TURN_RADIUS_MIN = 0.3
TURN_RADIUS_MAX = 1.0
RADAR_SCAN_SPEED = 3
