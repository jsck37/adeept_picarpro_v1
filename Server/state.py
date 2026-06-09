import json, os
from config import (
    DEFAULT_SPEED, SERVO_COUNT, SERVO_INIT_ANGLE,
    ULTRASONIC_ENABLED,
)
from Server.utils.system_info import SystemInfo

SERVO_CAL_FILE = os.path.join(os.path.dirname(__file__), "servo_cal.json")


def load_servo_cal():
    try:
        if os.path.isfile(SERVO_CAL_FILE):
            with open(SERVO_CAL_FILE) as f:
                return json.load(f).get("init_angles", [SERVO_INIT_ANGLE] * SERVO_COUNT)
    except Exception:
        pass
    return [SERVO_INIT_ANGLE] * SERVO_COUNT


def save_servo_cal(angles):
    try:
        with open(SERVO_CAL_FILE, "w") as f:
            json.dump({"init_angles": angles}, f, indent=2)
    except Exception:
        pass


class SharedState:
    def __init__(self):
        self.speed = DEFAULT_SPEED
        self.running = True
        self.motors = self.servos = self.leds = self.ultrasonic = None
        self.switches = self.oled = self.buzzer = self.mpu6050 = None
        self.autonomous = self.voice = self.camera = self.ds4 = None
        self.ws_clients = set()

    def init_camera(self):
        if not self.camera:
            from Server.camera.camera_opencv import Camera
            self.camera = Camera()

    def get_status(self):
        info = SystemInfo.get_all()
        ram = info['ram']
        ultra_ok = self.ultrasonic and self.ultrasonic._initialized
        mpu_ok = self.mpu6050 and self.mpu6050.initialized
        return {
            "cpu_temp": info["cpu_temp"],
            "cpu_usage": info["cpu_usage"],
            "ram_percent": ram["percent"],
            "ram_used_mb": ram["used_mb"],
            "ram_total_mb": ram["total_mb"],
            "distance": self.ultrasonic.get_last_distance() if ultra_ok else 0,
            "mpu6050": self.mpu6050.get_data() if mpu_ok else None,
            "cv_mode": self.camera.cv_thread.cv_mode if self.camera else "none",
            "auto_active": self.autonomous.is_active() if self.autonomous else False,
            "auto_mode": self.autonomous._current_mode if self.autonomous else "none",
            "speed": self.speed,
            "ultrasonic_enabled": ULTRASONIC_ENABLED,
            "hw": {
                "motors": self.motors._initialized if self.motors else False,
                "servos": self.servos._pwm_initialized if self.servos else False,
                "leds": self.leds._initialized if self.leds else False,
                "buzzer": self.buzzer._initialized if self.buzzer else False,
                "switches": self.switches._initialized if self.switches else False,
                "ultrasonic": ultra_ok,
                "mpu6050": mpu_ok,
                "oled": self.oled._initialized if self.oled else False,
                "camera": self.camera is not None,
                "crane": True,
                "ds4": self.ds4.connected if self.ds4 else False,
                "voice": self.voice._initialized if self.voice else False,
            },
            "ds4": self.ds4.get_status() if self.ds4 else None,
            "voice": {
                "available": self.voice._initialized if self.voice else False,
                "active": self.voice._active if self.voice else False,
                "last_command": self.voice._last_command if self.voice else "",
            },
        }

    def shutdown_hardware(self):
        self.running = False
        from Server.logger import logger
        logger.info("[WebServer] Shutting down...")
        if self.autonomous:
            try:
                self.autonomous.shutdown()
            except Exception:
                pass
        if self.voice:
            try:
                self.voice.shutdown()
            except Exception:
                pass
        if self.camera:
            try:
                self.camera.shutdown()
            except Exception:
                pass
        for hw in (self.motors, self.servos, self.leds, self.switches,
                   self.ultrasonic, self.buzzer, self.oled, self.mpu6050, self.ds4):
            if hw:
                try:
                    hw.shutdown()
                except Exception:
                    pass
        logger.info("[WebServer] Shutdown complete")
