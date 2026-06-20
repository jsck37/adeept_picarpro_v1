import math, os, select, subprocess, threading, time
from Server.logger import logger

try:
    import evdev
    from evdev import InputDevice, ecodes
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False
    evdev = None
    ecodes = None

from config import (
    DS4_DEVICE_NAME, DS4_DEADZONE,
    DS4_STEER_SENSITIVITY, DS4_CAM_SENSITIVITY,
    DS4_HEARTBEAT_TIMEOUT, DS4_WATCHDOG_INTERVAL, DS4_READ_TIMEOUT,
    DEFAULT_SPEED, SERVO_CAM_PAN, SERVO_CAM_TILT,
    SERVO_STEERING, SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED,
    CRANE_GRIP_LOW, CRANE_GRIP_MID, CRANE_GRIP_HIGH,
    DS4_INVERT_LY, DS4_INVERT_RY, DS4_SPEED_MULT, DS4_CRANE_STEP,
    DS4_STEER_RANGE,
)

CRANE_GRIP_POSITIONS = [CRANE_GRIP_LOW, CRANE_GRIP_MID, CRANE_GRIP_HIGH]
CRANE_GRIP_LABELS = ['low', 'mid', 'high']

_BTN_TL2 = getattr(ecodes, 'BTN_TL2', None) if HAS_EVDEV else None
_BTN_TR2 = getattr(ecodes, 'BTN_TR2', None) if HAS_EVDEV else None


def _ensure_hid_sony():
    try:
        with open('/proc/modules') as f:
            for line in f:
                if line.startswith('hid_sony ') or line.startswith('hid_playstation '):
                    return
    except Exception:
        pass
    try:
        subprocess.run(['modprobe', 'hid-sony'],
                       capture_output=True, text=True, timeout=5)
    except Exception:
        pass


class DS4Controller:
    def __init__(self):
        self._running = False
        self._connected = False
        self._device = None
        self._thread = None
        self._watchdog_thread = None
        self._axis_ranges = {}
        self._last_event_time = 0.0
        self._connect_count = 0
        self._lx = self._ly = self._rx = self._ry = 0.0
        self._l2 = self._r2 = 0.0
        self._hat_x = self._hat_y = 0
        self._btn_state = {}
        self._motors = self._servos = self._leds = None
        self._buzzer = self._switches = self._shared_state = None
        self._autonomous = None
        self._speed = DEFAULT_SPEED
        self._cam_pan = self._cam_tilt = 90
        self._headlights_on = False
        self._crane_arm_closed = False
        self._crane_grip_index = 2
        self._crane_grip_direction = -1
        self._lock = threading.Lock()
        self._left_blinking = False
        self._right_blinking = False
        self._auto_mode_active = False
        self._turbo_active = False
        self._police_turbo_on = False
        self._rainbow_on = False
        self._rescan_flag = threading.Event()
        self._dpad_left_pressed = False
        self._dpad_right_pressed = False
        self._dpad_up_pressed = False
        self._dpad_down_pressed = False

    def start(self, motors, servos, leds, buzzer, switches,
              speed=DEFAULT_SPEED, shared_state=None, autonomous=None):
        if not HAS_EVDEV:
            logger.warning('[DS4] evdev not installed')
            return
        self._motors = motors
        self._servos = servos
        self._leds = leds
        self._buzzer = buzzer
        self._switches = switches
        self._speed = speed
        self._shared_state = shared_state
        self._autonomous = autonomous
        self._running = True
        _ensure_hid_sony()
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()
        logger.info('[DS4] searching for controller...')

    def stop(self):
        self._running = False
        self._disconnect()

    @property
    def connected(self):
        return self._connected

    def get_status(self):
        grip = CRANE_GRIP_LABELS[self._crane_grip_index] \
            if 0 <= self._crane_grip_index < len(CRANE_GRIP_LABELS) else 'unknown'
        return {
            'connected': self._connected,
            'speed': self._speed,
            'lx': round(self._lx, 2), 'ly': round(self._ly, 2),
            'rx': round(self._rx, 2), 'ry': round(self._ry, 2),
            'connect_count': self._connect_count,
            'turbo': self._turbo_active,
            'rainbow': self._rainbow_on,
            'police_turbo': self._police_turbo_on,
            'crane_arm_closed': self._crane_arm_closed,
            'crane_grip': grip,
        }

    def trigger_rescan(self):
        self._rescan_flag.set()

    def _find_device(self):
        if not HAS_EVDEV:
            return None
        try:
            paths = evdev.list_devices()
            candidates = []
            for p in paths:
                try:
                    dev = InputDevice(p)
                except OSError:
                    continue
                name = dev.name.lower()
                if (DS4_DEVICE_NAME.lower() in name or 'dualshock' in name
                        or 'sony interactive' in name or 'playstation' in name):
                    candidates.append(dev)
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]
            for dev in candidates:
                caps = dev.capabilities(absinfo=True)
                keys = caps.get(ecodes.EV_KEY, [])
                if ecodes.BTN_SOUTH in keys or ecodes.BTN_A in keys:
                    return dev
            return candidates[0]
        except Exception as e:
            logger.error(f'[DS4] search error: {e}')
            return None

    def _connect(self, device):
        try:
            device.grab()
        except Exception:
            pass
        self._device = device
        self._connected = True
        self._axis_ranges = {}
        self._last_event_time = time.monotonic()
        self._connect_count += 1
        try:
            caps = device.capabilities(absinfo=True)
            for code, info in caps.get(ecodes.EV_ABS, []):
                self._axis_ranges[code] = (info.min, info.max)
            logger.info(f'[DS4] connected #{self._connect_count}: {device.name} @ {device.path}')
        except Exception:
            logger.info(f'[DS4] connected #{self._connect_count}: {device.name}')
        self._thread = threading.Thread(target=self._event_loop, daemon=True)
        self._thread.start()

    def _disconnect(self):
        was = self._connected
        self._connected = False
        if self._device:
            try:
                self._device.ungrab()
            except Exception:
                pass
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
        if self._motors:
            try:
                self._motors.stop()
            except Exception:
                pass
        self._lx = self._ly = self._rx = self._ry = 0.0
        self._l2 = self._r2 = 0.0
        self._hat_x = self._hat_y = 0
        self._turbo_active = False
        self._left_blinking = False
        self._right_blinking = False
        if was:
            logger.warning('[DS4] disconnected — will auto-reconnect')

    def _watchdog(self):
        while self._running:
            if not self._connected:
                dev = self._find_device()
                if dev:
                    self._connect(dev)
                self._rescan_flag.wait(timeout=1.0)
                self._rescan_flag.clear()
            else:
                elapsed = time.monotonic() - self._last_event_time
                if elapsed > DS4_HEARTBEAT_TIMEOUT:
                    try:
                        alive = (self._device is not None
                                 and self._device.fd is not None
                                 and self._device.fd >= 0
                                 and os.path.exists(self._device.path))
                    except Exception:
                        alive = False
                    if not alive:
                        self._disconnect()
                    else:
                        self._last_event_time = time.monotonic()
                time.sleep(DS4_WATCHDOG_INTERVAL)

    def _event_loop(self):
        while self._running and self._connected:
            try:
                fd = self._device.fd
                if fd is None:
                    self._disconnect()
                    break
                r, _, _ = select.select([fd], [], [], DS4_READ_TIMEOUT)
                if not r:
                    continue
                for ev in self._device.read():
                    self._last_event_time = time.monotonic()
                    if not self._running or not self._connected:
                        break
                    if ev.type == ecodes.EV_ABS:
                        self._on_axis(ev.code, ev.value)
                    elif ev.type == ecodes.EV_KEY:
                        self._on_key(ev.code, ev.value)
            except OSError:
                self._disconnect()
                break
            except Exception as e:
                err = str(e).lower()
                if 'not open' in err or 'closed' in err or 'bad file' in err:
                    self._disconnect()
                    break
                time.sleep(0.05)
        if self._connected:
            self._disconnect()

    def _range(self, code):
        return self._axis_ranges.get(code, (0, 255))

    def _norm_axis(self, value, code):
        lo, hi = self._range(code)
        centre = (lo + hi) / 2.0
        half = (hi - lo) / 2.0
        if half == 0:
            return 0.0
        n = (value - centre) / half
        if abs(n) < DS4_DEADZONE:
            return 0.0
        if n > 0:
            n = (n - DS4_DEADZONE) / (1.0 - DS4_DEADZONE)
        else:
            n = (n + DS4_DEADZONE) / (1.0 - DS4_DEADZONE)
        return max(-1.0, min(1.0, n))

    def _norm_trigger(self, value, code):
        lo, hi = self._range(code)
        if hi == lo:
            return 0.0
        return max(0.0, min(1.0, (value - lo) / (hi - lo)))

    def _on_axis(self, code, value):
        if code == ecodes.ABS_X:
            self._lx = self._norm_axis(value, code)
            self._apply_move()
        elif code == ecodes.ABS_Y:
            raw = self._norm_axis(value, code)
            self._ly = -raw if not DS4_INVERT_LY else raw
            self._apply_move()
        elif code == ecodes.ABS_RX:
            self._rx = self._norm_axis(value, code)
            self._apply_pan_tilt()
        elif code == ecodes.ABS_RY:
            raw = self._norm_axis(value, code)
            self._ry = -raw if not DS4_INVERT_RY else raw
            self._apply_pan_tilt()
        elif code in (ecodes.ABS_Z, ecodes.ABS_BRAKE):
            self._l2 = self._norm_trigger(value, code)
            self._apply_turbo()
        elif code in (ecodes.ABS_RZ, ecodes.ABS_GAS):
            self._r2 = self._norm_trigger(value, code)
            self._apply_turbo()
        elif code == ecodes.ABS_HAT0X:
            self._hat_x = value
            self._apply_dpad()
        elif code == ecodes.ABS_HAT0Y:
            self._hat_y = value
            self._apply_dpad()

    def _on_key(self, code, value):
        was = self._btn_state.get(code, False)
        self._btn_state[code] = value
        if value and not was:
            self._btn_press(code)

    def _stop_auto_if_active(self):
        if self._auto_mode_active and self._autonomous:
            try:
                self._autonomous.stop()
            except Exception:
                pass
            self._auto_mode_active = False

    def _cycle_crane_grip(self):
        self._crane_grip_index += self._crane_grip_direction
        if self._crane_grip_index >= len(CRANE_GRIP_POSITIONS) - 1:
            self._crane_grip_direction = -1
        elif self._crane_grip_index <= 0:
            self._crane_grip_direction = 1
        self._crane_grip_index = max(0, min(len(CRANE_GRIP_POSITIONS) - 1,
                                            self._crane_grip_index))
        angle = CRANE_GRIP_POSITIONS[self._crane_grip_index]
        label = CRANE_GRIP_LABELS[self._crane_grip_index]
        self._smooth_crane(SERVO_CRANE_GRIP, angle)
        if self._shared_state:
            self._shared_state.crane_grip_position = label

    def _btn_press(self, code):
        if code in (ecodes.BTN_NORTH, ecodes.BTN_Y):
            if self._autonomous and self._shared_state:
                if not self._shared_state.camera:
                    self._shared_state.init_camera()
                if self._shared_state.camera:
                    self._autonomous._camera = self._shared_state.camera
                    self._autonomous.start('trackLineCV')
                    self._auto_mode_active = True
            return
        if code in (ecodes.BTN_WEST, ecodes.BTN_X):
            if self._autonomous and self._shared_state:
                if not self._shared_state.camera:
                    self._shared_state.init_camera()
                if self._shared_state.camera:
                    self._autonomous._camera = self._shared_state.camera
                    self._autonomous.start('trackHand')
                    self._auto_mode_active = True
            return
        self._stop_auto_if_active()
        if code in (ecodes.BTN_SOUTH, ecodes.BTN_A):
            if self._servos:
                self._crane_arm_closed = not self._crane_arm_closed
                angle = CRANE_ARM_CLOSED if self._crane_arm_closed else CRANE_ARM_OPEN
                self._smooth_crane(SERVO_CRANE_ARM, angle)
                if self._shared_state:
                    self._shared_state.crane_arm_closed = self._crane_arm_closed
        elif code in (ecodes.BTN_EAST, ecodes.BTN_B):
            if self._servos:
                self._cycle_crane_grip()
        elif code == ecodes.BTN_TL:
            self._toggle_side_lights()
        elif code == ecodes.BTN_TR:
            if self._buzzer:
                self._buzzer.beep()
        elif _BTN_TL2 is not None and code == _BTN_TL2:
            self._l2 = 1.0
            self._apply_turbo()
        elif _BTN_TR2 is not None and code == _BTN_TR2:
            self._r2 = 1.0
            self._apply_turbo()
        elif code == ecodes.BTN_MODE:
            if self._servos:
                self._servos.move_init()
                self._cam_pan = self._cam_tilt = 90
                self._crane_arm_closed = False
                self._crane_grip_index = 2
                self._crane_grip_direction = -1
                if self._shared_state:
                    self._shared_state.crane_arm_closed = False
                    self._shared_state.crane_grip_position = 'high'
        elif code == ecodes.BTN_START:
            self._toggle_police_turbo()
        elif code == ecodes.BTN_SELECT:
            self._toggle_police_only()

    def _smooth_crane(self, sid, target, step=DS4_CRANE_STEP, delay=0.03):
        if not self._servos:
            return
        current = self._servos.get_angle(sid)
        diff = target - current
        if abs(diff) <= step:
            self._servos.set_angle(sid, target)
            return
        def _run():
            pos = current
            direction = 1 if diff > 0 else -1
            while self._connected and self._running:
                pos += direction * step
                if (direction > 0 and pos >= target) or (direction < 0 and pos <= target):
                    self._servos.set_angle(sid, target)
                    break
                self._servos.set_angle(sid, pos)
                time.sleep(delay)
        threading.Thread(target=_run, daemon=True).start()

    def _apply_turbo(self):
        turbo_on = (self._l2 > 0.5 or self._r2 > 0.5)
        self._turbo_active = turbo_on
        if turbo_on:
            self._apply_move()

    def _apply_move(self):
        if not self._motors or not self._servos:
            return
        if self._shared_state and self._shared_state.web_active:
            return
        lx, ly = self._lx, self._ly
        if self._turbo_active:
            speed = 100
            d = 'forward'
        elif self._police_turbo_on:
            speed = 100
            d = 'forward' if ly >= 0 else 'backward'
            if math.hypot(lx, ly) < 0.05:
                self._motors.stop()
                self._servos.set_angle(SERVO_STEERING, 90)
                return
        else:
            if math.hypot(lx, ly) < 0.05:
                self._motors.stop()
                self._servos.set_angle(SERVO_STEERING, 90)
                return
            if self._shared_state:
                self._speed = self._shared_state.speed
            speed = self._speed
            d = 'forward' if ly >= 0 else 'backward'
        if abs(lx) < 0.1:
            turn, radius = 'no', 0.5
        elif lx > 0:
            turn, radius = 'right', max(0.2, 0.5 - lx * DS4_STEER_SENSITIVITY * 0.3)
        else:
            turn, radius = 'left', max(0.2, 0.5 + lx * DS4_STEER_SENSITIVITY * 0.3)
        if self._turbo_active or self._police_turbo_on:
            s = 100
        else:
            abs_ly = abs(ly)
            s = max(10, int(speed * abs_ly * DS4_SPEED_MULT)) if abs_ly > 0.1 else 0
            s = min(100, s)
        if s > 0:
            if self._police_turbo_on and turn != 'no':
                self._motors_single_turn(s, d, turn)
            elif turn != 'no':
                self._motors_move_with_turn(s, d, turn, radius)
            else:
                self._motors.move(s, d, turn, radius)
        steer = max(30, min(150, 90 - int(lx * DS4_STEER_RANGE * DS4_STEER_SENSITIVITY)))
        self._servos.set_angle(SERVO_STEERING, steer)

    def _motors_single_turn(self, speed, direction, turn):
        if not self._motors or not self._motors._initialized:
            return
        s = speed / 100.0
        if turn == 'left':
            left, right = 0.0, s
        else:
            left, right = s, 0.0
        forward = (direction == 'forward')
        self._motors._set_side(self._motors._MOTOR_A_IN1 if hasattr(self._motors, '_MOTOR_A_IN1') else 26,
                               self._motors._MOTOR_A_IN2 if hasattr(self._motors, '_MOTOR_A_IN2') else 21,
                               self._motors._pwm_a, forward, right)
        self._motors._set_side(self._motors._MOTOR_B_IN1 if hasattr(self._motors, '_MOTOR_B_IN1') else 27,
                               self._motors._MOTOR_B_IN2 if hasattr(self._motors, '_MOTOR_B_IN2') else 18,
                               self._motors._pwm_b, forward, left)

    def _motors_move_with_turn(self, speed, direction, turn, radius):
        if not self._motors or not self._motors._initialized:
            return
        s = speed / 100.0
        radius = max(0.2, min(1.0, radius))
        if turn == 'left':
            left = max(s * 0.3, s * (1 - radius))
            right = s
        else:
            left = s
            right = max(s * 0.3, s * (1 - radius))
        forward = (direction == 'forward')
        self._motors._set_side(self._motors._MOTOR_A_IN1 if hasattr(self._motors, '_MOTOR_A_IN1') else 26,
                               self._motors._MOTOR_A_IN2 if hasattr(self._motors, '_MOTOR_A_IN2') else 21,
                               self._motors._pwm_a, forward, right)
        self._motors._set_side(self._motors._MOTOR_B_IN1 if hasattr(self._motors, '_MOTOR_B_IN1') else 27,
                               self._motors._MOTOR_B_IN2 if hasattr(self._motors, '_MOTOR_B_IN2') else 18,
                               self._motors._pwm_b, forward, left)

    def _apply_pan_tilt(self):
        if not self._servos:
            return
        rx, ry = self._rx, self._ry
        if abs(rx) < DS4_DEADZONE and abs(ry) < DS4_DEADZONE:
            return
        if abs(rx) >= DS4_DEADZONE:
            cur = self._servos.get_angle(SERVO_CAM_PAN)
            step = max(1, int(abs(rx) * 6 * DS4_CAM_SENSITIVITY))
            new = min(180, cur + step) if rx > 0 else max(0, cur - step)
            if new != cur:
                self._servos.set_angle(SERVO_CAM_PAN, new)
                self._cam_pan = new
        if abs(ry) >= DS4_DEADZONE:
            cur = self._servos.get_angle(SERVO_CAM_TILT)
            step = max(1, int(abs(ry) * 5 * DS4_CAM_SENSITIVITY))
            new = min(180, cur + step) if ry > 0 else max(0, cur - step)
            if new != cur:
                self._servos.set_angle(SERVO_CAM_TILT, new)
                self._cam_tilt = new

    def _apply_dpad(self):
        if self._hat_y < 0 and not self._dpad_up_pressed:
            self._dpad_up_pressed = True
            self._toggle_headlight_main()
        elif self._hat_y >= 0:
            self._dpad_up_pressed = False
        if self._hat_y > 0 and not self._dpad_down_pressed:
            self._dpad_down_pressed = True
            self._toggle_rainbow()
        elif self._hat_y <= 0:
            self._dpad_down_pressed = False
        if self._hat_x < 0 and not self._dpad_left_pressed:
            self._dpad_left_pressed = True
            self._start_left_blinker()
        elif self._hat_x >= 0:
            self._dpad_left_pressed = False
        if self._hat_x > 0 and not self._dpad_right_pressed:
            self._dpad_right_pressed = True
            self._start_right_blinker()
        elif self._hat_x <= 0:
            self._dpad_right_pressed = False

    def _toggle_headlight_main(self):
        if self._switches and self._switches._initialized:
            on_now = self._switches.headlight_toggle()
            self._headlights_on = on_now

    def _toggle_side_lights(self):
        if self._switches and self._switches._initialized:
            self._headlights_on = not self._headlights_on
            (self._switches.on if self._headlights_on else self._switches.off)(0)
            (self._switches.on if self._headlights_on else self._switches.off)(1)

    def _toggle_rainbow(self):
        if not self._leds:
            return
        self._rainbow_on = not self._rainbow_on
        if self._rainbow_on:
            self._leds.set_mode('rainbow', (255, 255, 255))
            if self._shared_state:
                self._shared_state.led_mode = 'rainbow'
                self._shared_state.led_color = (255, 255, 255)
        else:
            self._leds.set_mode('off', (0, 0, 0))
            if self._shared_state:
                self._shared_state.led_mode = 'off'

    def _toggle_police_turbo(self):
        self._police_turbo_on = not self._police_turbo_on
        if self._police_turbo_on:
            if self._leds:
                self._leds.set_mode('police', (255, 0, 0))
            if self._shared_state:
                self._shared_state.led_mode = 'police'
        else:
            if self._leds:
                self._leds.set_mode('off', (0, 0, 0))
            if self._shared_state:
                self._shared_state.led_mode = 'off'
        self._apply_move()

    def _toggle_police_only(self):
        if self._leds:
            if self._police_turbo_on:
                self._police_turbo_on = False
                self._leds.set_mode('off', (0, 0, 0))
                if self._shared_state:
                    self._shared_state.led_mode = 'off'
            else:
                self._leds.set_mode('police', (255, 0, 0))
                if self._shared_state:
                    self._shared_state.led_mode = 'police'

    def _start_left_blinker(self):
        if self._left_blinking:
            self._left_blinking = False
            if self._shared_state:
                self._shared_state.left_blinker = False
            if self._switches and self._switches._initialized:
                self._switches.set_blinker('left', False)
            return
        self._left_blinking = True
        if self._shared_state:
            self._shared_state.left_blinker = True
            self._shared_state.right_blinker = False
        if self._switches and self._switches._initialized:
            self._switches.set_blinker('right', False)
            self._switches.set_blinker('left', True)

    def _start_right_blinker(self):
        if self._right_blinking:
            self._right_blinking = False
            if self._shared_state:
                self._shared_state.right_blinker = False
            if self._switches and self._switches._initialized:
                self._switches.set_blinker('right', False)
            return
        self._right_blinking = True
        if self._shared_state:
            self._shared_state.right_blinker = True
            self._shared_state.left_blinker = False
        if self._switches and self._switches._initialized:
            self._switches.set_blinker('left', False)
            self._switches.set_blinker('right', True)

    def shutdown(self):
        self._left_blinking = False
        self._right_blinking = False
        self.stop()
