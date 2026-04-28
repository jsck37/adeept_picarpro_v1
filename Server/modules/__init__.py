"""
PiCar Pro Example Modules (unified v1 + v2).
Each script demonstrates a specific hardware component or function.
All scripts use the optimized Server libraries (config, servos, motors, etc.).
"""

import os
import json

# Module registry: {id: {name, desc, desc_ru, file, icon, hardware}}
MODULES = [
    {
        "id": "led_blink",
        "name": "LED Blink",
        "name_ru": "Мигание LED",
        "desc": "Cycle through 3 on-board LEDs — on/off sequence",
        "desc_ru": "Поочерёдное включение 3 светодиодов",
        "file": "led_blink.py",
        "icon": "💡",
        "hardware": ["LEDs"],
    },
    {
        "id": "buzzer_tone",
        "name": "Buzzer Single Tone",
        "name_ru": "Одиночный тон",
        "desc": "Play a single note (C4) on the active buzzer",
        "desc_ru": "Воспроизвести одну ноту (До) на зуммере",
        "file": "buzzer_tone.py",
        "icon": "🔊",
        "hardware": ["Buzzer"],
    },
    {
        "id": "buzzer_scale",
        "name": "Buzzer Scale",
        "name_ru": "Гамма на зуммере",
        "desc": "Play 7 musical notes (C4-B4) sequentially",
        "desc_ru": "Воспроизвести 7 нот гаммы последовательно",
        "file": "buzzer_scale.py",
        "icon": "🎵",
        "hardware": ["Buzzer"],
    },
    {
        "id": "buzzer_birthday",
        "name": "Happy Birthday",
        "name_ru": "С днём рождения!",
        "desc": "Play Happy Birthday melody on the buzzer",
        "desc_ru": "Мелодия «С днём рождения» на зуммере",
        "file": "buzzer_birthday.py",
        "icon": "🎂",
        "hardware": ["Buzzer"],
    },
    {
        "id": "servo_sweep",
        "name": "Servo 180° Sweep",
        "name_ru": "Серво 180°",
        "desc": "Sweep a servo from 0° to 180° and back",
        "desc_ru": "Поворот серво от 0° до 180° и обратно",
        "file": "servo_sweep.py",
        "icon": "🔄",
        "hardware": ["Servo", "PCA9685"],
    },
    {
        "id": "motor_drive",
        "name": "Motor Drive",
        "name_ru": "Двигатели",
        "desc": "Drive motors forward and backward",
        "desc_ru": "Двигатели вперёд и назад",
        "file": "motor_drive.py",
        "icon": "🏎️",
        "hardware": ["Motors"],
    },
    {
        "id": "ws2812_breath",
        "name": "LED Breathing",
        "name_ru": "Дыхание LED",
        "desc": "WS2812 RGB LED pulsing/breathing effect",
        "desc_ru": "Пульсирующий эффект WS2812 RGB",
        "file": "ws2812_breath.py",
        "icon": "🫁",
        "hardware": ["WS2812"],
    },
    {
        "id": "ws2812_flow",
        "name": "LED Flowing",
        "name_ru": "Бегущие огни",
        "desc": "WS2812 flowing color-chase animation",
        "desc_ru": "Эффект бегущих огней WS2812",
        "file": "ws2812_flow.py",
        "icon": "🌈",
        "hardware": ["WS2812"],
    },
    {
        "id": "ultrasonic",
        "name": "Ultrasonic Distance",
        "name_ru": "Ультразвук",
        "desc": "Measure distance with HC-SR04 ultrasonic sensor",
        "desc_ru": "Измерение расстояния датчиком HC-SR04",
        "file": "ultrasonic.py",
        "icon": "📏",
        "hardware": ["Ultrasonic"],
    },
    {
        "id": "line_track",
        "name": "Line Tracking",
        "name_ru": "Датчик линии",
        "desc": "Read 3-channel IR line tracking sensor",
        "desc_ru": "Чтение 3-канального ИК датчика линии",
        "file": "line_track.py",
        "icon": "🛤️",
        "hardware": ["LineTracker"],
    },
    {
        "id": "oled_snow",
        "name": "OLED Snowflakes",
        "name_ru": "Снежинки OLED",
        "desc": "SSD1306 OLED animated snowflake display",
        "desc_ru": "Анимация снежинок на OLED дисплее",
        "file": "oled_snow.py",
        "icon": "❄️",
        "hardware": ["OLED"],
    },
    {
        "id": "oled_clock",
        "name": "OLED Clock",
        "name_ru": "Часы OLED",
        "desc": "SSD1306 OLED real-time clock display",
        "desc_ru": "Часы реального времени на OLED",
        "file": "oled_clock.py",
        "icon": "🕐",
        "hardware": ["OLED"],
    },
    {
        "id": "camera_stream",
        "name": "Camera Stream",
        "name_ru": "Поток камеры",
        "desc": "Flask MJPEG camera streaming server",
        "desc_ru": "Потоковое видео камеры через Flask",
        "file": "camera_stream.py",
        "icon": "📹",
        "hardware": ["Camera"],
    },
    {
        "id": "cv_color",
        "name": "Color Detection",
        "name_ru": "Поиск цвета",
        "desc": "OpenCV color detection with bounding box overlay",
        "desc_ru": "Обнаружение цвета через OpenCV",
        "file": "cv_color.py",
        "icon": "🎨",
        "hardware": ["Camera", "OpenCV"],
    },
    {
        "id": "cv_gesture",
        "name": "Gesture Detection",
        "name_ru": "Распознавание жестов",
        "desc": "OpenCV hand gesture detection via skin-color HSV masking",
        "desc_ru": "Обнаружение жестов через HSV маску кожи",
        "file": "cv_gesture.py",
        "icon": "✋",
        "hardware": ["Camera", "OpenCV"],
    },
    {
        "id": "cv_motion",
        "name": "Motion Detection",
        "name_ru": "Датчик движения",
        "desc": "OpenCV motion detection watchdog",
        "desc_ru": "Обнаружение движения через OpenCV",
        "file": "cv_motion.py",
        "icon": "👁️",
        "hardware": ["Camera", "OpenCV"],
    },
    {
        "id": "cv_aruco",
        "name": "ArUco Navigation",
        "name_ru": "Навигация ArUco",
        "desc": "ArUco marker detection and waypoint navigation",
        "desc_ru": "Обнаружение маркеров ArUco и навигация",
        "file": "cv_aruco.py",
        "icon": "🎯",
        "hardware": ["Camera", "OpenCV"],
    },
    {
        "id": "battery",
        "name": "Battery Monitor",
        "name_ru": "Монитор батареи",
        "desc": "Read battery voltage and percentage via ADS7830 ADC",
        "desc_ru": "Чтение напряжения и заряда батареи",
        "file": "battery.py",
        "icon": "🔋",
        "hardware": ["ADS7830", "Battery"],
    },
    {
        "id": "mpu6050",
        "name": "MPU6050 Accelerometer",
        "name_ru": "Акселерометр MPU6050",
        "desc": "Read averaged X/Y/Z acceleration from MPU6050",
        "desc_ru": "Чтение ускорений X/Y/Z из MPU6050",
        "file": "mpu6050.py",
        "icon": "🧭",
        "hardware": ["MPU6050"],
    },
    {
        "id": "voice",
        "name": "Voice Commands",
        "name_ru": "Голосовые команды",
        "desc": "Offline speech recognition via Sherpa-NCNN",
        "desc_ru": "Офлайн распознавание речи через Sherpa-NCNN",
        "file": "voice.py",
        "icon": "🎙️",
        "hardware": ["Microphone", "Sherpa-NCNN"],
    },
]


def get_module_list(lang="en"):
    """Return list of modules with localized names/descriptions."""
    result = []
    for m in MODULES:
        entry = {
            "id": m["id"],
            "name": m["name_ru"] if lang == "ru" else m["name"],
            "desc": m["desc_ru"] if lang == "ru" else m["desc"],
            "icon": m["icon"],
            "hardware": m["hardware"],
            "file": m["file"],
        }
        result.append(entry)
    return result


def get_module_by_id(module_id):
    """Find module by ID."""
    for m in MODULES:
        if m["id"] == module_id:
            return m
    return None


def get_module_path(module_id):
    """Get full path to module script."""
    m = get_module_by_id(module_id)
    if m is None:
        return None
    return os.path.join(os.path.dirname(__file__), m["file"])
