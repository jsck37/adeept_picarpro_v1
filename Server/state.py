import threading
from Server.logger import logger
from Server.utils.system_info import SystemInfo
from Server.network import get_ip
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

    def _try_init(self, name, factory):
        try:
            return factory()
        except Exception as e:
            logger.warning(f'[State] {name} init failed: {e}')
            return None

    def init_hardware(self):
        self.oled = self._try_init('OLED', lambda: __import__('Server.hardware.oled', fromlist=['OLEDDisplay']).OLEDDisplay())
        self.leds = self._try_init('LEDs', lambda: __import__('Server.hardware.leds_ws2812', fromlist=['LEDController']).LEDController())
        self.motors = self._try_init('Motors', lambda: __import__('Server.hardware.motors', fromlist=['MotorController']).MotorController())
        self.servos = self._try_init('Servos', lambda: __import__('Server.hardware.servos', fromlist=['ServoController']).ServoController())
        self.switches = self._try_init('Switch', lambda: __import__('Server.hardware.switch', fromlist=['SwitchController']).SwitchController())
        self.buzzer = self._try_init('Buzzer', lambda: __import__('Server.hardware.buzzer', fromlist=['BuzzerController']).BuzzerController())
        self.ultrasonic = self._try_init('Ultrasonic', lambda: __import__('Server.hardware.ultrasonic', fromlist=['UltrasonicSensor']).UltrasonicSensor())
        self.mpu6050 = self._try_init('MPU6050', lambda: __import__('Server.hardware.mpu6050', fromlist=['MPU6050Controller']).MPU6050Controller())
        self.ds4 = self._try_init('DS4', lambda: __import__('Server.hardware.ds4', fromlist=['DS4Controller']).DS4Controller())
        self.autonomous = self._try_init('Autonomous', lambda: __import__('Server.functions.autonomous', fromlist=['AutonomousController']).AutonomousController(self.motors, self.servos, self.ultrasonic))
        self.voice = self._try_init('Voice', lambda: __import__('Server.functions.voice_command', fromlist=['VoiceCommandController']).VoiceCommandController(self.servos, self.motors))
        ip = get_ip()
        if self.oled and self.oled._initialized:
            self.oled.set_lines([f'{ip}:{FLASK_PORT}', 'Ready', '', ''])

    def init_camera(self):
        if not self.camera:
            try:
                from Server.camera.camera_opencv import Camera
                self.camera = Camera.get_instance()
                self.camera.start()
            except Exception as e:
                logger.error(f'[State] camera init failed: {e}')

    def get_status(self):
        info = SystemInfo.get_all()
        ram = info['ram']
        ultra_ok = self.ultrasonic and self.ultrasonic._initialized
        mpu_ok = self.mpu6050 and self.mpu6050.initialized
        ir_l, ir_m, ir_r = (self.autonomous.get_ir_values()
                            if self.autonomous else (None, None, None))
        servo_angles = list(self.servos._angles) if self.servos else [0] * 7
        return {
            'cpu_temp': info['cpu_temp'],
            'cpu_usage': info['cpu_usage'],
            'ram': ram,
            'low_voltage': info['low_voltage'],
            'ip': get_ip(),
            'distance': self.ultrasonic.get_last_distance() if ultra_ok else 0,
            'mpu6050': self.mpu6050.get_data() if mpu_ok else None,
            'cv_mode': self.camera.cv_thread.cv_mode if self.camera else 'none',
            'auto_active': self.autonomous.is_active() if self.autonomous else False,
            'auto_mode': self.autonomous._current_mode if self.autonomous else 'none',
            'radar_data': self.autonomous.get_radar_data() if self.autonomous else [],
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
        }

    def shutdown(self):
        self.running = False
        logger.info('[State] shutting down...')
        if self.autonomous:
            try: self.autonomous.shutdown()
            except Exception: pass
        if self.voice:
            try: self.voice.shutdown()
            except Exception: pass
        if self.camera:
            try: self.camera.shutdown()
            except Exception: pass
        for hw in (self.motors, self.servos, self.leds, self.switches,
                   self.ultrasonic, self.buzzer, self.oled, self.mpu6050, self.ds4):
            if hw:
                try: hw.shutdown()
                except Exception: pass
        logger.info('[State] shutdown complete')
