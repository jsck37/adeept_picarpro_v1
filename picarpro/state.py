import threading
import time

from picarpro.logger import logger
from picarpro.utils import hotspot
from picarpro.utils.system_info import SystemInfo
from picarpro.network import get_ip
from picarpro.control import ControlArbiter
from config import (SERVO_STEERING, DEFAULT_SPEED, FLASK_PORT, HW_RETRY_INTERVAL_S,
                    RADAR_SECTOR_DEG,
                    WEB_ACTIVE_TTL_S, WAITING_LED_POLL_S)


_CV2_FOUND = None


def _cv2_installed():
    """cv2 установлен? Результат кэшируется (get_status зовётся часто)."""
    global _CV2_FOUND
    if _CV2_FOUND is None:
        import sys
        if 'cv2' in sys.modules:           # уже импортирован (в т.ч. тестовый фейк)
            _CV2_FOUND = True
        else:
            try:
                import importlib.util
                _CV2_FOUND = importlib.util.find_spec('cv2') is not None
            except (ImportError, ValueError):
                _CV2_FOUND = False
    return _CV2_FOUND


class SharedState:
    def __init__(self):
        self.speed = DEFAULT_SPEED
        self.running = True
        self._web_active_until = 0.0     # monotonic: до какого момента веб «держит руль»
        self._last_activity = time.monotonic()
        self.power = None
        self.control = ControlArbiter()
        from picarpro.functions.missions import MissionRunner
        self.missions = MissionRunner(self)    # сценарии/запись пути (без потоков, пока не запущены)
        self.ws_meta = {}                # ws -> {'id','visible','last',...}
        self.left_blinker = False
        self.right_blinker = False
        self.crane_arm_closed = False
        self.crane_grip_position = 'mid'   # v1.6.4: high убран
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
        self._camera_failed = False   # init_camera() упал: модуль CV «не найден»
        self.autonomous = None
        self.voice = None
        self.steady = None
        self.voltage = None
        # фоновые сервисы
        self.pusher = None          # расталкивание препятствий клешней

    # ---- активность оператора ------------------------------------------------
    @property
    def web_active(self):
        """Веб-клиент недавно управлял роботом (DS4 тогда не перехватывает).

        Раньше это был булев флаг, который выставлялся первой же командой из
        веба и больше НИКОГДА не сбрасывался — геймпад переставал работать
        до перезапуска сервиса. Теперь «захват» истекает через WEB_ACTIVE_TTL_S.
        """
        return time.monotonic() < self._web_active_until

    @web_active.setter
    def web_active(self, value):
        self._web_active_until = (time.monotonic() + WEB_ACTIVE_TTL_S
                                  if value else 0.0)

    def autonomy_active(self):
        """Выполняется ли автономный режим/миссия (сам ведёт робота)."""
        if self.autonomous is not None and self.autonomous.is_active():
            return True
        if self.missions is not None and self.missions.is_active():
            return True
        for svc in (self.pusher,):
            if svc is None:
                continue
            try:
                if svc.get_status().get('active'):
                    return True
            except Exception:
                pass
        return False

    def stop_autonomy(self):
        """Остановить автономный режим/миссии. True — что-то было остановлено."""
        was = self.autonomy_active()
        if not was:
            return False
        for svc in (self.autonomous, self.pusher, self.missions):
            if svc is None:
                continue
            try:
                svc.stop()
            except Exception:
                pass
        return True

    def emergency_stop(self):
        """Аварийный стоп: моторы, автономные режимы и миссии, зуммер."""
        self.stop_autonomy()
        if self.motors:
            self.motors.stop()
        if self.servos:
            try:
                self.servos.set_angle(SERVO_STEERING, 90)
            except Exception:
                pass
        logger.warning('[State] EMERGENCY STOP')

    def _hotspot_count(self):
        """Число устройств точки доступа (кэш 3 с внутри hotspot.count_clients).

        None — считать не удалось (нет iw/leases/arp, запуск не на роботе).
        """
        try:
            count, _src = hotspot.count_clients(
                local_ips=hotspot.local_ip_candidates(get_ip))
        except Exception as e:
            logger.warning(f'[State] hotspot count failed: {e}')
            count = None
        return count

    def _devices_connected(self):
        """Есть ли хоть какое-то подключённое устройство (v1.6.13).

        Источник — точка доступа (iw/dnsmasq/arp, см. picarpro/utils/
        hotspot.py), а не только вкладки веб-интерфейса: телефон мог висеть
        в Wi-Fi робота без открытого UI, а подсветка мигала «ждём».
        Геймпад DS4 (Bluetooth) и открытые вкладки тоже считаются
        устройствами. Если hotspot-счётчик недоступен — прежнее поведение
        (только веб-клиенты).
        """
        if self.ws_clients:
            return True
        count = self._hotspot_count()
        if count is not None and count > 0:
            return True
        if self.ds4 is not None and self.ds4.connected:
            return True
        return False

    def refresh_waiting_led(self):
        """Нет ни одного подключённого устройства -> нижняя подсветка мигает
        синим (ожидание подключения); появилось -> прежний режим подсветки."""
        leds = self.leds
        if leds is None or not hasattr(leds, 'set_waiting'):
            return
        try:
            leds.set_waiting(not self._devices_connected())
        except Exception as e:
            logger.warning(f'[State] waiting LED failed: {e}')

    def _waiting_led_loop(self):
        """Подключение к точке доступа происходит БЕЗ веб-событий: если не
        опрашивать hotspot, подсветка не узнавала о телефоне, подключившемся
        мимо веб-интерфейса (и наоборот — продолжала мигать после
        отключения всех устройств)."""
        while self.running:
            time.sleep(WAITING_LED_POLL_S)
            if not self.running:
                break
            try:
                self.refresh_waiting_led()
            except Exception:
                pass

    def on_last_client_gone(self):
        """Последний веб-клиент отключился (обрыв Wi-Fi, закрыли вкладку).

        Если робот при этом ехал по ручной команде, без этого он ехал бы до
        упора: UI шлёт «move» только при изменении, «keepalive» нет.
        Автономные режимы и геймпад (он сам шлёт события) не трогаем.
        """
        self.refresh_waiting_led()
        if self.ws_clients or self.motors is None:
            return False
        if self.autonomy_active():
            return False
        if self.ds4 is not None and self.ds4.connected and not self.web_active:
            return False
        if getattr(self.motors, 'is_moving', False):
            self.motors.stop()
            logger.warning('[State] last web client gone — motors stopped')
            return True
        return False

    def touch(self):
        """Отметить активность (для менеджера питания)."""
        self._last_activity = time.monotonic()

    def touch_web(self):
        """Команда управления из веба: продлить захват руля."""
        self.touch()
        self.web_active = True

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
        self.refresh_waiting_led()      # старт без клиентов: ждём подключения

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

        from picarpro.power import PowerManager
        self.power = self._try_init('Power', lambda: PowerManager(self))
        self._start_hw_retry()
        # v1.6.13: фоновый опрос hotspot — подсветка «ждём подключения»
        # реагирует на подключение/отключение устройств мимо веб-интерфейса
        self._waiting_led_thread = threading.Thread(
            target=self._waiting_led_loop, daemon=True)
        self._waiting_led_thread.start()

    # ---- автовосстановление железа (v1.4.0) --------------------------------
    # Если в момент загрузки шина I2C была занята/зависла (классика:
    # неверно подключённый MPU6050) или SPI ещё не поднят, контроллеры
    # остаются мёртвыми до перезапуска сервиса. Фоновый ретрай делает
    # модули доступными сам — панель серво/LED в UI оживёт без перезапуска.
    _HW_RETRY = [
        # (атрибут state, модуль, класс, флаг «жив»)
        ('oled', 'oled', 'OLEDDisplay', '_initialized'),
        ('leds', 'leds_ws2812', 'LEDController', '_initialized'),
        ('motors', 'motors', 'MotorController', '_initialized'),
        ('servos', 'servos', 'ServoController', '_pwm_initialized'),
        ('switches', 'switch', 'SwitchController', '_initialized'),
        ('buzzer', 'buzzer', 'BuzzerController', '_initialized'),
        ('ultrasonic', 'ultrasonic', 'UltrasonicSensor', '_initialized'),
        # mpu6050 — собственный retry-цикл (не дублируем);
        # ds4 — собственный watchdog и Bluetooth-логика.
    ]

    def _start_hw_retry(self):
        self._hw_retry_thread = threading.Thread(
            target=self._hw_retry_loop, daemon=True)
        self._hw_retry_thread.start()

    def _hw_alive(self, attr, flag):
        obj = getattr(self, attr, None)
        return obj is not None and bool(getattr(obj, flag, True))

    def _hw_retry_step(self):
        """Один проход ретрая: реинициализация мёртвых модулей.

        Вынесено из цикла для тестируемости. Возвращает список
        восстановленных имён."""
        recovered = []
        for attr, module, cls, flag in self._HW_RETRY:
            if self._hw_alive(attr, flag):
                continue
            obj = self._init(module, cls)
            if obj is not None and bool(getattr(obj, flag, False)):
                setattr(self, attr, obj)
                recovered.append(attr)
                logger.info(f'[State] {attr} recovered (auto-retry v1.4.0)')
        if recovered:
            self._recreate_dependents()
            if 'leds' in recovered:
                self.refresh_waiting_led()   # лента ожила: нет клиентов — мигаем синим
        return recovered

    def _hw_retry_loop(self):
        while self.running:
            time.sleep(HW_RETRY_INTERVAL_S)
            if not self.running:
                break
            try:
                self._hw_retry_step()
            except Exception as e:
                logger.warning(f'[State] hw retry error: {e}')

    def _recreate_dependents(self):
        """После восстановления железа пересоздать режимы, которые при
        загрузке держали мёртвые ссылки (только если они не активны)."""
        try:
            if (self.steady is None and self.mpu6050
                    and getattr(self.mpu6050, 'initialized', False)
                    and self._hw_alive('servos', '_pwm_initialized')):
                from picarpro.functions.steady_camera import SteadyCameraController
                self.steady = self._try_init(
                    'SteadyCamera',
                    lambda: SteadyCameraController(self.servos, self.mpu6050))
        except Exception:
            pass
        try:
            if self.autonomous is not None and not self.autonomous.is_active():
                # rebind вместо пересоздания: раньше каждое восстановление
                # оставляло работающий поток старого контроллера и теряло
                # привязку камеры (CV-режимы после этого отвечали
                # «Camera not available»)
                self.autonomous.rebind(self.motors, self.servos,
                                       self.ultrasonic, self.mpu6050)
        except Exception:
            pass
        try:
            if self.voice is not None and not self.voice._active:
                self.voice.servos = self.servos
                self.voice.motors = self.motors
        except Exception:
            pass
        try:
            if self.steady is not None and not self.steady.active:
                self.steady.rebind(self.servos, self.mpu6050)
        except Exception:
            pass
        # DS4-геймпад получает ссылки на железо один раз в start()
        ds4 = self.ds4
        if ds4 is not None:
            for ref_attr, state_attr in (
                    ('_motors', 'motors'), ('_servos', 'servos'),
                    ('_leds', 'leds'), ('_buzzer', 'buzzer'),
                    ('_switches', 'switches')):
                try:
                    if getattr(ds4, ref_attr, None) is not None:
                        setattr(ds4, ref_attr, getattr(self, state_attr))
                except Exception:
                    pass

    def init_camera(self):
        if not self.camera:
            try:
                from picarpro.camera.camera_opencv import Camera
                self.camera = Camera.get_instance()
                self.camera.start()
                self._camera_failed = False
            except Exception as e:
                self._camera_failed = True
                logger.error(f'[State] camera init failed: {e}')

    def cv_available(self):
        """Модуль компьютерного зрения «найден» (для проверки «Module not found»).

        Камера создаётся лениво (первый /video_feed или CV-команда), поэтому
        «camera is None» ещё не значит, что модуля нет: иначе карточка CV была бы
        заблокирована ДО первого запуска и разблокировать её было бы нечем.
        Нет модуля = камера уже не смогла стартовать или не установлен cv2."""
        if self.camera is not None:
            return True
        if self._camera_failed:
            return False
        return _cv2_installed()

    def get_status(self):
        info = SystemInfo.get_all()
        ultra_ok = self.ultrasonic is not None and self.ultrasonic._initialized
        mpu_ok = self.mpu6050 is not None and self.mpu6050.initialized
        ir_l, ir_m, ir_r = (self.autonomous.get_ir_values()
                            if self.autonomous else (None, None, None))
        servo_angles = self.servos.get_angles() if self.servos else [0] * 7
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
            # v1.6.10: состояние режима (лабиринт: решение, дистанции, угол) и
            # сектор радара — UI рисует веер и подсказку направления
            'auto_info': (self.autonomous.get_auto_info()
                          if self.autonomous and self.autonomous.is_active() else {}),
            'radar_sector': RADAR_SECTOR_DEG,
            'ultra_ok': ultra_ok,
            'steady_active': self.steady.active if self.steady else False,
            'speed': self.speed,
            'ds4': self.ds4.get_status() if self.ds4 else None,
            'low_power': bool(self.power and self.power.low_power),
            'clients': len(self.ws_clients),
            # v1.6.13: устройства точки доступа (iw/dnsmasq/arp) — источник
            # счётчика «Устройства» и подсветки ожидания; None = не считается
            'hotspot_clients': self._hotspot_count(),
            'driver': self.control.driver,
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
                # v1.6.9: проверка «Module not found» для окон MPU6050 / DS4 /
                # Миссии / CV / Autonomous. «ds4» выше — геймпад ПОДКЛЮЧЁН, а
                # «ds4_found» — модуль есть (иначе карточку с кнопками
                # Bluetooth нельзя было бы использовать, пока геймпад не
                # подключён).
                'ds4_found': self.ds4 is not None,
                'missions': bool(self.motors is not None
                                 and self.motors._initialized),
                'cv': self.cv_available(),
                'voice': self.voice._initialized if self.voice else False,
                'battery_adc': self.voltage.has_adc if self.voltage else False,
            },
            'headlight': self.switches.headlight_state
                         if self.switches and self.switches._initialized else False,
            'side_lights': (self.switches.get_state(0) or self.switches.get_state(1))
                           if self.switches and self.switches._initialized else False,
            'left_blinker': self.left_blinker,
            'right_blinker': self.right_blinker,
            'ir_left': ir_l, 'ir_middle': ir_m, 'ir_right': ir_r,
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
            'push': (self.pusher.get_status() if self.pusher else
                     {'active': False, 'pushes_done': 0}),
            'mission': self.missions.get_status(),
        }

    def shutdown(self):
        self.running = False
        logger.info('[State] shutting down...')
        if self.power:
            try: self.power.shutdown()
            except Exception: pass
        if self.pusher:
            try: self.pusher.stop()
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

    ctrl = AutonomousController(state.motors, state.servos, state.ultrasonic,
                                state.mpu6050)
    if state.camera:
        ctrl.set_camera(state.camera)
    ctrl.set_alarm_callback(_alarm)
    return ctrl


def _make_voice(state):
    from picarpro.functions.voice_command import VoiceCommandController
    return VoiceCommandController(state.servos, state.motors)
