"""
PiCar Pro Modules — auto-discovery of all .py scripts.

Modules are found automatically:
1. All .py files in Server/modules/ (built-in examples)
2. All .py files in Server/uploads/ (user-uploaded scripts)

Meta-information (name, description, icon, hardware) is read from
the script's docstring or from the METADATA dict below.
Files without an entry in METADATA get auto-generated info from filename.
"""

import os

# Directory where this __init__.py lives
MODULES_DIR = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────
# Metadata registry — describes each module script.
# Files not listed here will get auto-generated metadata
# from their filename (snake_case → readable name).
# ─────────────────────────────────────────────────────
METADATA = {
    "led_blink.py": {
        "name": "LED Blink",
        "name_ru": "Мигание LED",
        "desc": "Cycle through 3 on-board LEDs — on/off sequence",
        "desc_ru": "Поочерёдное включение 3 светодиодов",
        "icon": "💡",
        "hardware": ["LEDs"],
    },
    "buzzer_tone.py": {
        "name": "Buzzer Single Tone",
        "name_ru": "Одиночный тон",
        "desc": "Play a single note (C4) on the active buzzer",
        "desc_ru": "Воспроизвести одну ноту (До) на зуммере",
        "icon": "🔊",
        "hardware": ["Buzzer"],
    },
    "buzzer_scale.py": {
        "name": "Buzzer Scale",
        "name_ru": "Гамма на зуммере",
        "desc": "Play 7 musical notes (C4-B4) sequentially",
        "desc_ru": "Воспроизвести 7 нот гаммы последовательно",
        "icon": "🎵",
        "hardware": ["Buzzer"],
    },
    "buzzer_birthday.py": {
        "name": "Happy Birthday",
        "name_ru": "С днём рождения!",
        "desc": "Play Happy Birthday melody on the buzzer",
        "desc_ru": "Мелодия «С днём рождения» на зуммере",
        "icon": "🎂",
        "hardware": ["Buzzer"],
    },
    "servo_sweep.py": {
        "name": "Servo 180° Sweep",
        "name_ru": "Серво 180°",
        "desc": "Sweep a servo from 0° to 180° and back",
        "desc_ru": "Поворот серво от 0° до 180° и обратно",
        "icon": "🔄",
        "hardware": ["Servo", "PCA9685"],
    },
    "motor_drive.py": {
        "name": "Motor Drive",
        "name_ru": "Двигатели",
        "desc": "Drive motors forward and backward",
        "desc_ru": "Двигатели вперёд и назад",
        "icon": "🏎️",
        "hardware": ["Motors"],
    },
    "ws2812_breath.py": {
        "name": "LED Breathing",
        "name_ru": "Дыхание LED",
        "desc": "WS2812 RGB LED pulsing/breathing effect",
        "desc_ru": "Пульсирующий эффект WS2812 RGB",
        "icon": "🫁",
        "hardware": ["WS2812"],
    },
    "ws2812_flow.py": {
        "name": "LED Flowing",
        "name_ru": "Бегущие огни",
        "desc": "WS2812 flowing color-chase animation",
        "desc_ru": "Эффект бегущих огней WS2812",
        "icon": "🌈",
        "hardware": ["WS2812"],
    },
    "ultrasonic.py": {
        "name": "Ultrasonic Distance",
        "name_ru": "Ультразвук",
        "desc": "Measure distance with HC-SR04 ultrasonic sensor",
        "desc_ru": "Измерение расстояния датчиком HC-SR04",
        "icon": "📏",
        "hardware": ["Ultrasonic"],
    },
    "line_track.py": {
        "name": "Line Tracking",
        "name_ru": "Датчик линии",
        "desc": "Read 3-channel IR line tracking sensor",
        "desc_ru": "Чтение 3-канального ИК датчика линии",
        "icon": "🛤️",
        "hardware": ["LineTracker"],
    },
    "oled_snow.py": {
        "name": "OLED Snowflakes",
        "name_ru": "Снежинки OLED",
        "desc": "SSD1306 OLED animated snowflake display",
        "desc_ru": "Анимация снежинок на OLED дисплее",
        "icon": "❄️",
        "hardware": ["OLED"],
    },
    "oled_clock.py": {
        "name": "OLED Clock",
        "name_ru": "Часы OLED",
        "desc": "SSD1306 OLED real-time clock display",
        "desc_ru": "Часы реального времени на OLED",
        "icon": "🕐",
        "hardware": ["OLED"],
    },
    "camera_stream.py": {
        "name": "Camera Stream",
        "name_ru": "Поток камеры",
        "desc": "Flask MJPEG camera streaming server",
        "desc_ru": "Потоковое видео камеры через Flask",
        "icon": "📹",
        "hardware": ["Camera"],
    },
    "cv_color.py": {
        "name": "Color Detection",
        "name_ru": "Поиск цвета",
        "desc": "OpenCV color detection with bounding box overlay",
        "desc_ru": "Обнаружение цвета через OpenCV",
        "icon": "🎨",
        "hardware": ["Camera", "OpenCV"],
    },
    "cv_gesture.py": {
        "name": "Gesture Detection",
        "name_ru": "Распознавание жестов",
        "desc": "OpenCV hand gesture detection via skin-color HSV masking",
        "desc_ru": "Обнаружение жестов через HSV маску кожи",
        "icon": "✋",
        "hardware": ["Camera", "OpenCV"],
    },
    "cv_motion.py": {
        "name": "Motion Detection",
        "name_ru": "Датчик движения",
        "desc": "OpenCV motion detection watchdog",
        "desc_ru": "Обнаружение движения через OpenCV",
        "icon": "👁️",
        "hardware": ["Camera", "OpenCV"],
    },
    "cv_aruco.py": {
        "name": "ArUco Navigation",
        "name_ru": "Навигация ArUco",
        "desc": "ArUco marker detection and waypoint navigation",
        "desc_ru": "Обнаружение маркеров ArUco и навигация",
        "icon": "🎯",
        "hardware": ["Camera", "OpenCV"],
    },
    "battery.py": {
        "name": "Battery Monitor (DISABLED)",
        "name_ru": "Монитор батареи (ОТКЛЮЧЕН)",
        "desc": "ADS7830 not present — module disabled",
        "desc_ru": "ADS7830 отсутствует — модуль отключён",
        "icon": "⚠️",
        "hardware": ["ADS7830", "Battery"],
    },
    "mpu6050.py": {
        "name": "MPU6050 Accelerometer",
        "name_ru": "Акселерометр MPU6050",
        "desc": "Read averaged X/Y/Z acceleration from MPU6050",
        "desc_ru": "Чтение ускорений X/Y/Z из MPU6050",
        "icon": "🧭",
        "hardware": ["MPU6050"],
    },
    "voice.py": {
        "name": "Voice Commands",
        "name_ru": "Голосовые команды",
        "desc": "Offline speech recognition via Sherpa-NCNN",
        "desc_ru": "Офлайн распознавание речи через Sherpa-NCNN",
        "icon": "🎙️",
        "hardware": ["Microphone", "Sherpa-NCNN"],
    },
}


def _filename_to_name(filename):
    """Convert snake_case.py to readable 'Snake Case'."""
    name = os.path.splitext(filename)[0]
    return name.replace("_", " ").title()


def _read_docstring(filepath):
    """Try to read the first line of a Python file's docstring."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    # Get the docstring content
                    quote = stripped[:3]
                    rest = stripped[3:]
                    if rest.endswith(quote) and len(rest) > 3:
                        return rest[:-3].strip()
                    if rest:
                        return rest.strip()
                    # Multi-line docstring — read next line
                    for next_line in f:
                        ns = next_line.strip()
                        if ns.endswith(quote):
                            return ns[:-3].strip()
                        return ns.strip()
                elif stripped.startswith("#") or not stripped:
                    continue
                else:
                    break
    except Exception:
        pass
    return ""


def _scan_directory(directory, prefix=""):
    """Scan a directory for .py files and return module entries."""
    modules = []
    if not os.path.isdir(directory):
        return modules

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".py") or filename.startswith("_") or filename == "__init__.py":
            continue

        filepath = os.path.join(directory, filename)
        if not os.path.isfile(filepath):
            continue

        # Use metadata if available, otherwise auto-generate
        meta = METADATA.get(filename)

        if meta:
            entry = {
                "id": prefix + os.path.splitext(filename)[0],
                "name": meta.get("name", _filename_to_name(filename)),
                "name_ru": meta.get("name_ru", _filename_to_name(filename)),
                "desc": meta.get("desc", ""),
                "desc_ru": meta.get("desc_ru", ""),
                "icon": meta.get("icon", "📄"),
                "hardware": meta.get("hardware", []),
                "file": filename,
            }
        else:
            # Auto-generate from filename + docstring
            docstring = _read_docstring(filepath)
            entry = {
                "id": prefix + os.path.splitext(filename)[0],
                "name": _filename_to_name(filename),
                "name_ru": _filename_to_name(filename),
                "desc": docstring or f"Script: {filename}",
                "desc_ru": docstring or f"Скрипт: {filename}",
                "icon": "📄",
                "hardware": [],
                "file": filename,
            }

        modules.append(entry)

    return modules


def get_module_list(lang="en"):
    """Return all available modules with localized names/descriptions.

    Scans Server/modules/ for built-in scripts and
    Server/uploads/ for user-uploaded scripts.
    """
    result = []

    # Built-in modules from Server/modules/
    for m in _scan_directory(MODULES_DIR, prefix=""):
        entry = {
            "id": m["id"],
            "name": m["name_ru"] if lang == "ru" else m["name"],
            "desc": m["desc_ru"] if lang == "ru" else m["desc"],
            "icon": m["icon"],
            "hardware": m["hardware"],
            "file": m["file"],
        }
        result.append(entry)

    # User-uploaded scripts from Server/uploads/
    upload_dir = os.path.join(MODULES_DIR, "uploads")
    for m in _scan_directory(upload_dir, prefix="upload_"):
        entry = {
            "id": m["id"],
            "name": m["name"],
            "desc": m["desc"],
            "icon": m["icon"],
            "hardware": m["hardware"],
            "file": m["file"],
            "is_upload": True,
        }
        result.append(entry)

    return result


def get_module_by_id(module_id):
    """Find module by ID across all sources."""
    for m in get_module_list():
        if m["id"] == module_id:
            return m
    return None


def get_module_path(module_id):
    """Get full filesystem path to module script."""
    # Check built-in modules first
    m = get_module_by_id(module_id)
    if m is None:
        return None

    if m.get("is_upload"):
        upload_dir = os.path.join(MODULES_DIR, "uploads")
        return os.path.join(upload_dir, m["file"])
    else:
        return os.path.join(MODULES_DIR, m["file"])
