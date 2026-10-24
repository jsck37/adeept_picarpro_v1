import threading
import time

from picarpro.logger import logger
from picarpro.utils.system_info import SystemInfo
from picarpro.network import get_ip
from config import DEFAULT_SPEED, FLASK_PORT


class SharedState:
    def __init__(self):
        self.speed = DEFAULT_SPEED
        self.running = True
        self.web_active = False
        self.left_blinker = False
        self.right_blinker = False
        self.crane_arm_closed = False
        self.crane_grip_position = 'high'
        self.led_mode = 'off'
        self.led_color = (255, 0, 0)
        self.ws_clients = set()
        self.ws_clients_lock = threading.Lock()
        self.motors = None
        self.servos = None
        self.switches = None
        self.leds = None
        self.buzzer = None
        self.ultrasonic = None
        self.mpu6050 = None
        self.oled = None
        self.ds4 = None
        self.camera = None
        self.autonomous = None
        self.voice = None
        self.steady = None
        self.voltage = None
        # Портированные режимы hugo-Peltier + NavTCP/IMU-стрим
        self.aruco_nav = None       # бортовая ArUco-навигация
        self.pusher = None          # расталкивание препятствий клешней
        self.color_scan = None      # сканирование рукой + цветовой захват
        self.nav_server = None      # NavTCP (команды хоста, порт 5001)
        self.imu_stream = None      # UDP-стриминг gz (порт 5005)
        self.nav_tcp_host = None    # IP подключённого навигационного хоста
        self.nav_last_cmd = ''
        self.nav_last_cmd_ts = 0.0

    def _try_init(self, name, factory):
        try:
            return factory()
        except Exception as e:
            logger.warning(f'[State] {name} init failed: {e}')
            return None

    def _init(self, module_name, class_name):
        """Прямой импорт вместо __import__-строк (чище и быстрее)."""
        import importlib
        try:
            mod = importlib.import_module(f'picarpro.hardware.{module_name}')
            return self._try_init(class_name, lambda: getattr(mod, class_name)())
        except Exception as e:
            logger.warning(f'[State] {class_name} import failed: {e}')
            return None

    def init_hardware(self):
        self.oled = self._init('oled', 'OLEDDisplay')
        self.leds = self._init('leds_ws2812', 'LEDController')
        self.motors = self._init('motors', 'MotorController')
        self.servos = self._init('servos', 'ServoController')
        self.switches = self._init('switch', 'SwitchController')
        self.buzzer = self._init('buzzer', 'BuzzerController')
        self.ultrasonic = self._init('ultrasonic', 'UltrasonicSensor')
        self.mpu6050 = self._init('mpu6050', 'MPU6050Controller')
        self.ds4 = self._init('ds4', 'DS4Controller')

        # Мониторинг питания: тревога при низкой батарее — зуммер + мигание
        def _battery_alarm():
            if self.buzzer:
                self.buzzer.beep()
            if self.leds:
                self.leds.set_mode('police', (255, 0, 0))

        from picarpro.hardware.voltage import VoltageMonitor
        self.voltage = self._try_init(
            'Voltage', lambda: VoltageMonitor(on_alarm=_battery_alarm))

        self.autonomous = self._try_init('Autonomous', lambda: _make_autonomous(self))
        self.voice = self._try_init(
            'Voice', lambda: _make_voice(self))
        if self.mpu6050 and self.servos:
            from picarpro.functions.steady_camera import SteadyCameraController
            self.steady = self._try_init(
                'SteadyCamera',
                lambda: SteadyCameraController(self.servos, self.mpu6050))

        ip = get_ip()
        if self.oled and self.oled._initialized:
            self.oled.set_lines([f'{ip}:{FLASK_PORT}', 'Ready', '', ''])

    def init_camera(self):
        if not self.camera:
            try:
                from picarpro.camera.camera_opencv import Camera
                self.camera = Camera.get_instance()
                self.camera.start()
            except Exception as e:
                logger.error(f'[State] camera init failed: {e}')

    def get_status(self):
        info = SystemInfo.get_all()
        ultra_ok = self.ultrasonic is not None and self.ultrasonic._initialized
        mpu_ok = self.mpu6050 is not None and self.mpu6050.initialized
        ir_l, ir_m, ir_r = (self.autonomous.get_ir_values()
                            if self.autonomous else (None, None, None))
        servo_angles = list(self.servos._angles) if self.servos else [0] * 7
        return {
            'cpu_temp': info['cpu_temp'],
            'cpu_usage': info['cpu_usage'],
            'ram': info['ram'],
            'low_voltage': info['low_voltage'],
            'battery': self.voltage.get_status() if self.voltage else None,
            'ip': get_ip(),
            'distance': self.ultrasonic.get_last_distance() if ultra_ok else 0,
            'mpu6050': self.mpu6050.get_data() if mpu_ok else None,
            'cv_mode': self.camera.cv_thread.cv_mode if self.camera else 'none',
            'auto_active': self.autonomous.is_active() if self.autonomous else False,
            'auto_mode': self.autonomous._current_mode if self.autonomous else 'none',
            'radar_data': self.autonomous.get_radar_data() if self.autonomous else [],
            'steady_active': self.steady.active if self.steady else False,
            'speed': self.speed,
            'hw': {
                'motors': self.motors._initialized if self.motors else False,
                'servos': self.servos._pwm_initialized if self.servos else False,
                'leds': self.leds._initialized if self.leds else False,
                'buzzer': self.buzzer._initialized if self.buzzer else False,
                'switches': self.switches._initialized if self.switches else False,
                'ultrasonic': ultra_ok,
                'mpu6050': mpu_ok,
                'oled': self.oled._initialized if self.oled else False,
                'camera': self.camera is not None,
                'crane': self.servos is not None and self.servos._pwm_initialized,
                'autonomous': self.autonomous is not None,
                'ds4': self.ds4.connected if self.ds4 else False,
                'voice': self.voice._initialized if self.voice else False,
                'battery_adc': self.voltage.has_adc if self.voltage else False,
            },
            'headlight': self.switches.headlight_state
                         if self.switches and self.switches._initialized else False,
            'left_blinker': self.left_blinker,
            'right_blinker': self.right_blinker,
            'ir_left': ir_l, 'ir_middle': ir_m, 'ir_right': ir_r,
            'ds4': self.ds4.get_status() if self.ds4 else None,
            'voice': {
                'available': self.voice._initialized if self.voice else False,
                'active': self.voice._active if self.voice else False,
                'last_command': self.voice._last_command if self.voice else '',
            },
            'led_mode': self.led_mode,
            'led_color': list(self.led_color),
            'servo_limits': self.servos.get_limits() if self.servos else {},
            'servo_angles': servo_angles,
            'crane_arm_closed': self.crane_arm_closed,
            'crane_grip_position': self.crane_grip_position,
            'yolo': (self.camera.get_yolo_status()
                     if self.camera else {'loaded': False, 'objects': []}),
            'nav_tcp': {
                'host': self.nav_tcp_host,
                'last_cmd': self.nav_last_cmd,
                'age_s': round(max(0.0, time.time()
                                   - self.nav_last_cmd_ts), 1)
                if self.nav_last_cmd_ts else None,
            },
            'aruco_nav': (self.aruco_nav.get_status()
                          if self.aruco_nav else
                          {'active': False, 'finished': False}),
            'push': (self.pusher.get_status() if self.pusher else
                     {'active': False, 'pushes_done': 0}),
            'color_scan': (self.color_scan.get_status() if self.color_scan else
                           {'active': False, 'mode': 'IDLE'}),
        }

    def shutdown(self):
        self.running = False
        logger.info('[State] shutting down...')
        if self.aruco_nav:
            try: self.aruco_nav.stop()
            except Exception: pass
        if self.color_scan:
            try: self.color_scan.stop()
            except Exception: pass
        if self.pusher:
            try: self.pusher.stop()
            except Exception: pass
        if self.nav_server:
            try: self.nav_server.shutdown()
            except Exception: pass
        if self.imu_stream:
            try: self.imu_stream.stop()
            except Exception: pass
        if self.autonomous:
            try: self.autonomous.shutdown()
            except Exception: pass
        if self.voice:
            try: self.voice.shutdown()
            except Exception: pass
        if self.steady:
            try: self.steady.shutdown()
            except Exception: pass
        if self.camera:
            try: self.camera.shutdown()
            except Exception: pass
        if self.voltage:
            try: self.voltage.shutdown()
            except Exception: pass
        for hw in (self.motors, self.servos, self.leds, self.switches,
                   self.ultrasonic, self.buzzer, self.oled, self.mpu6050,
                   self.ds4):
            if hw:
                try: hw.shutdown()
                except Exception: pass
        logger.info('[State] shutdown complete')


def _make_autonomous(state):
    from picarpro.functions.autonomous import AutonomousController

    def _alarm(pixels):
        # охрана: зуммер + полицейский режим LED
        if state.buzzer:
            state.buzzer.play_melody('alarm')
        if state.leds:
            state.leds.set_mode('police', (255, 0, 0))

    ctrl = AutonomousController(state.motors, state.servos, state.ultrasonic)
    ctrl.set_alarm_callback(_alarm)
    return ctrl


def _make_voice(state):
    from picarpro.functions.voice_command import VoiceCommandController
    return VoiceCommandController(state.servos, state.motors)
