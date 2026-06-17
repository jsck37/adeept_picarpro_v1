import math, threading, time
from config import (
    SERVO_COUNT, SERVO_INIT_ANGLE, SERVO_INIT_ANGLES, SERVO_LIMITS,
    CRANE_ARM_OPEN, CRANE_GRIP_HIGH, LED_COUNT,
)
from Server.logger import logger


class SimServoController:
    def __init__(self):
        self._angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._init_angles = [SERVO_INIT_ANGLE] * SERVO_COUNT
        self._pwm_initialized = True
        self._limits = {}
        for i in range(SERVO_COUNT):
            if i in SERVO_LIMITS:
                self._limits[i] = dict(SERVO_LIMITS[i])
            else:
                self._limits[i] = {"min": 0, "max": 180}
        for i, a in SERVO_INIT_ANGLES.items():
            if 0 <= i < SERVO_COUNT:
                if a is None:
                    if i == 6:
                        a = CRANE_ARM_OPEN
                    elif i == 5:
                        a = CRANE_GRIP_HIGH
                    else:
                        a = SERVO_INIT_ANGLE
                self._init_angles[i] = a
                self._angles[i] = a
        logger.info(f"[Sim:Servos] OK ({SERVO_COUNT} channels, angles={self._init_angles})")

    def _clamp(self, sid, angle):
        lim = self._limits.get(sid, {"min": 0, "max": 180})
        return max(lim["min"], min(lim["max"], angle))

    def set_angle(self, sid, angle):
        if sid >= SERVO_COUNT:
            return
        angle = self._clamp(sid, angle)
        self._angles[sid] = angle

    def move_angle(self, sid, offset):
        if sid < SERVO_COUNT:
            self.set_angle(sid, self._init_angles[sid] + offset)

    def smooth_move(self, sid, target, steps=10, delay=0.02):
        self.set_angle(sid, target)

    def move_init(self):
        for i in range(SERVO_COUNT):
            self._angles[i] = self._init_angles[i]

    def set_init_angle(self, sid, angle):
        if 0 <= sid < SERVO_COUNT:
            self._init_angles[sid] = self._clamp(sid, angle)

    def get_angle(self, sid):
        return self._angles[sid] if 0 <= sid < SERVO_COUNT else 0

    def get_limits(self, sid=None):
        if sid is not None:
            return self._limits.get(sid, {"min": 0, "max": 180})
        return dict(self._limits)

    def set_limits(self, sid, min_angle, max_angle):
        if 0 <= sid < SERVO_COUNT:
            self._limits[sid] = {"min": int(min_angle), "max": int(max_angle)}
            logger.info(f"[Sim:Servos] S{sid} limits set: {min_angle}-{max_angle}")
            return True
        return False

    def stop_all(self):
        pass

    def shutdown(self):
        logger.info("[Sim:Servos] Shutdown")


class SimMotorController:
    def __init__(self):
        self._speed = 50
        self._initialized = True
        logger.info("[Sim:Motors] OK")

    def move(self, speed=None, direction='forward', turn='no', radius=0.5):
        speed = speed if speed is not None else self._speed
        self._speed = max(0, min(100, speed))

    def stop(self):
        pass

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, v):
        self._speed = max(0, min(100, v))

    def shutdown(self):
        logger.info("[Sim:Motors] Shutdown")


class SimLEDController:
    def __init__(self):
        self._initialized = True
        self._mode = "off"
        self._color = (0, 0, 0)
        self._running = True
        logger.info(f"[Sim:LEDs] OK ({LED_COUNT} LEDs)")

    def fill(self, r, g, b):
        pass

    def clear(self):
        pass

    def set_mode(self, mode, color=(255, 0, 0)):
        self._mode = mode
        self._color = color

    def shutdown(self):
        self._running = False
        logger.info("[Sim:LEDs] Shutdown")


class SimOLEDDisplay:
    def __init__(self):
        self._initialized = True
        self._lines = ["PiCar Pro [SIM]", "Starting...", "", ""]
        self._scroll_text = ""
        self._low_voltage = False
        self._running = True
        logger.info("[Sim:OLED] OK")

    def set_lines(self, lines):
        for i, l in enumerate(lines[:4]):
            self._lines[i] = str(l)[:21]

    def set_scroll_text(self, text):
        self._scroll_text = text

    def set_low_voltage(self, active: bool):
        if active != self._low_voltage:
            self._low_voltage = active
            logger.warning(f"[Sim:OLED] Low-voltage warning: {'ON' if active else 'OFF'}")

    def shutdown(self):
        self._running = False
        logger.info("[Sim:OLED] Shutdown")


class SimBuzzerController:
    def __init__(self):
        self._initialized = True
        self._running = True
        logger.info("[Sim:Buzzer] OK")

    def play_melody(self, name="beep"):
        logger.info(f"[Sim:Buzzer] Play: {name}")

    def beep(self):
        self.play_melody("beep")

    def stop(self):
        pass

    def shutdown(self):
        self._running = False
        logger.info("[Sim:Buzzer] Shutdown")


class SimSwitchController:
    def __init__(self):
        self._initialized = True
        self._states = [False, False]
        self._headlight_on = False
        logger.info("[Sim:Switch] 2 switches + headlight OK")

    def on(self, i):
        if 0 <= i < len(self._states):
            self._states[i] = True

    def off(self, i):
        if 0 <= i < len(self._states):
            self._states[i] = False

    def get_state(self, i):
        if 0 <= i < len(self._states):
            return self._states[i]
        return False

    def headlight_on(self):
        self._headlight_on = True

    def headlight_off(self):
        self._headlight_on = False

    def headlight_toggle(self):
        self._headlight_on = not self._headlight_on
        return self._headlight_on

    @property
    def headlight_state(self):
        return self._headlight_on

    def shutdown(self):
        logger.info("[Sim:Switch] Shutdown")


class SimUltrasonicSensor:
    def __init__(self):
        self._initialized = True
        self._distance = 42.0
        self._running = True
        logger.info("[Sim:Ultrasonic] OK (simulated distance: 42.0 cm)")

    def get_last_distance(self):
        self._distance = 42.0 + math.sin(time.time() * 0.5) * 15.0
        return round(self._distance, 1)

    def shutdown(self):
        self._running = False
        logger.info("[Sim:Ultrasonic] Shutdown")


class SimMPU6050Controller:
    def __init__(self):
        self.initialized = True
        self._running = True
        self._accel = {'x': 0.0, 'y': 0.0, 'z': 9.81}
        self._gyro = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self._roll = 0.0
        self._pitch = 0.0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._sim_loop, daemon=True)
        self._thread.start()
        logger.info("[Sim:MPU6050] OK (simulated IMU data)")

    def _sim_loop(self):
        while self._running:
            t = time.time()
            with self._lock:
                self._accel = {
                    'x': round(0.1 * math.sin(t * 0.3), 3),
                    'y': round(0.1 * math.cos(t * 0.3), 3),
                    'z': round(9.81 + 0.05 * math.sin(t * 0.5), 3),
                }
                self._gyro = {
                    'x': round(0.5 * math.sin(t * 0.2), 1),
                    'y': round(0.3 * math.cos(t * 0.2), 1),
                    'z': round(0.1 * math.sin(t * 0.1), 1),
                }
                self._roll = round(2.0 * math.sin(t * 0.15), 1)
                self._pitch = round(1.5 * math.cos(t * 0.15), 1)
            time.sleep(0.1)

    def get_data(self):
        with self._lock:
            return {
                'accel': dict(self._accel),
                'gyro': dict(self._gyro),
                'roll': self._roll,
                'pitch': self._pitch,
            }

    def shutdown(self):
        self._running = False
        self.initialized = False
        logger.info("[Sim:MPU6050] Shutdown")


class SimDS4Controller:
    def __init__(self):
        self.connected = False
        logger.info("[Sim:DS4] OK (no real device)")

    def start(self, **kwargs):
        pass

    def get_status(self):
        return {"connected": False, "device": None}

    def shutdown(self):
        logger.info("[Sim:DS4] Shutdown")
