"""DS4 Bluetooth controller via evdev.

DS4 over Bluetooth creates TWO /dev/input/eventX devices:
  - Main gamepad: sticks, buttons, triggers, D-pad
  - Touchpad: only ABS_X/ABS_Y + BTN_LEFT

We MUST select the main gamepad, not the touchpad.
The main gamepad has BTN_SOUTH (Cross) in its key capabilities.

Stick ranges vary by connection:
  - Bluetooth (hid-sony): 0–255, centre 128
  - USB (hid-playstation): -32768..32767, centre 0
Axis ranges are auto-detected from device capabilities.

Fixes applied:
  - D-pad mapping corrected (hat_y > 0 = DOWN → arm down, not up)
  - Safe grab/ungrab with error recovery
  - Watchdog reconnection without resource leaks
  - Proper event loop with blocking read instead of polling
"""

import math, threading, time

try:
    import evdev
    from evdev import InputDevice, ecodes
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

from Server.config import (
    DS4_ENABLED, DS4_DEVICE_NAME, DS4_DEADZONE,
    DS4_STEER_SENSITIVITY, DS4_CAM_SENSITIVITY,
    DEFAULT_SPEED, SERVO_CAM_PAN, SERVO_CAM_TILT,
    SERVO_STEERING, SERVO_CLAW_ARM, SERVO_CLAW_GRIP,
    CLAW_ARM_UP, CLAW_ARM_DOWN, CLAW_GRIP_OPEN, CLAW_GRIP_CLOSED,
    CRANE_ENABLED,
)


class DS4Controller:
    """DualShock 4 gamepad controller with auto-reconnect."""

    def __init__(self):
        self._running = False
        self._connected = False
        self._device = None
        self._thread = None
        self._watchdog_thread = None
        self._axis_ranges = {}  # {code: (min, max)}

        # Stick state (normalised –1..+1)
        self._lx = self._ly = self._rx = self._ry = 0.0
        self._l2 = self._r2 = 0.0
        self._hat_x = self._hat_y = 0
        self._btn_state = {}

        # Hardware refs (set in start())
        self._motors = self._servos = self._leds = None
        self._buzzer = self._switches = self._shared_state = None
        self._speed = DEFAULT_SPEED
        self._cam_pan = self._cam_tilt = 90
        self._headlights_on = self._claw_grip_closed = self._claw_arm_down = False
        self._led_mode_idx = 0
        self._led_modes = ['off', 'solid', 'breath', 'flow', 'rainbow', 'police']
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────

    def start(self, motors, servos, leds, buzzer, switches,
              speed=DEFAULT_SPEED, shared_state=None):
        if not DS4_ENABLED or not HAS_EVDEV:
            print("[DS4] Disabled or evdev missing")
            return
        self._motors, self._servos, self._leds = motors, servos, leds
        self._buzzer, self._switches = buzzer, switches
        self._speed, self._shared_state = speed, shared_state
        self._running = True
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()
        print("[DS4] Searching for controller...")

    def stop(self):
        self._running = False
        self._disconnect()

    @property
    def connected(self):
        return self._connected

    def get_status(self):
        return {
            'enabled': DS4_ENABLED,
            'connected': self._connected,
            'speed': self._speed,
            'lx': round(self._lx, 2),
            'ly': round(self._ly, 2),
            'rx': round(self._rx, 2),
            'ry': round(self._ry, 2),
        }

    # ── Device discovery ────────────────────────────────────────────────

    def _find_device(self):
        """Find the MAIN gamepad device (not the touchpad)."""
        if not HAS_EVDEV:
            return None
        try:
            devices = [InputDevice(p) for p in evdev.list_devices()]
            candidates = []
            for dev in devices:
                name = dev.name.lower()
                if (DS4_DEVICE_NAME.lower() in name
                        or 'dualshock' in name
                        or 'sony interactive' in name):
                    candidates.append(dev)
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]

            # Multiple devices: find the one with gamepad buttons (not touchpad)
            for dev in candidates:
                caps = dev.capabilities(absinfo=True)
                keys = caps.get(ecodes.EV_KEY, [])
                if ecodes.BTN_SOUTH in keys or ecodes.BTN_A in keys:
                    return dev

            # Fallback: first with ABS_X
            for dev in candidates:
                caps = dev.capabilities(absinfo=True)
                if any(c == ecodes.ABS_X for c, _ in caps.get(ecodes.EV_ABS, [])):
                    return dev
            return candidates[0]
        except Exception as e:
            print(f"[DS4] Search error: {e}")
            return None

    def _connect(self, device):
        try:
            device.grab()
        except Exception as e:
            print(f"[DS4] Grab failed (non-fatal): {e}")
            # Continue without exclusive access — other processes may still read

        self._device = device
        self._connected = True
        self._axis_ranges = {}

        try:
            caps = device.capabilities(absinfo=True)
            for code, info in caps.get(ecodes.EV_ABS, []):
                self._axis_ranges[code] = (info.min, info.max)
            abs_info = {
                ecodes.ABS.get(k, hex(k)): f'{v[0]}..{v[1]}'
                for k, v in self._axis_ranges.items()
            }
            print(f"[DS4] Connected: {device.name} @ {device.path}")
            print(f"[DS4] Axes: {abs_info}")
        except Exception:
            print(f"[DS4] Connected: {device.name}")

        self._thread = threading.Thread(target=self._event_loop, daemon=True)
        self._thread.start()

    def _disconnect(self):
        self._connected = False
        if self._device:
            try:
                self._device.ungrab()
            except Exception:
                pass
            self._device = None
        if self._motors:
            try:
                self._motors.stop()
            except Exception:
                pass
        self._lx = self._ly = self._rx = self._ry = 0.0
        print("[DS4] Disconnected")

    # ── Background threads ──────────────────────────────────────────────

    def _watchdog(self):
        while self._running:
            if not self._connected:
                dev = self._find_device()
                if dev:
                    self._connect(dev)
            time.sleep(3)

    def _event_loop(self):
        while self._running and self._connected:
            try:
                # Use blocking read with timeout for responsiveness
                for ev in self._device.read_loop():
                    if not self._running or not self._connected:
                        break
                    if ev.type == ecodes.EV_ABS:
                        self._on_axis(ev.code, ev.value)
                    elif ev.type == ecodes.EV_KEY:
                        self._on_key(ev.code, ev.value)
            except OSError:
                print("[DS4] Device lost")
                self._disconnect()
                break
            except Exception as e:
                print(f"[DS4] Event error: {e}")
                time.sleep(0.01)
        if self._connected:
            self._disconnect()

    # ── Axis normalisation ──────────────────────────────────────────────

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

    # ── Event handlers ──────────────────────────────────────────────────

    def _on_axis(self, code, value):
        if code == ecodes.ABS_X:
            self._lx = self._norm_axis(value, code)
            self._apply_move()
        elif code == ecodes.ABS_Y:
            self._ly = self._norm_axis(value, code)
            self._apply_move()
        elif code == ecodes.ABS_RX:
            self._rx = self._norm_axis(value, code)
            self._apply_camera()
        elif code == ecodes.ABS_RY:
            self._ry = self._norm_axis(value, code)
            self._apply_camera()
        elif code == ecodes.ABS_Z:
            self._l2 = self._norm_trigger(value, code)
            self._apply_triggers()
        elif code == ecodes.ABS_RZ:
            self._r2 = self._norm_trigger(value, code)
            self._apply_triggers()
        elif code == ecodes.ABS_HAT0X:
            self._hat_x = value
            self._apply_dpad()
        elif code == ecodes.ABS_HAT0Y:
            self._hat_y = value
            self._apply_dpad()

    def _on_key(self, code, value):
        was = self._btn_state.get(code, False)
        self._btn_state[code] = value
        if value and not was:          # rising edge only
            self._btn_press(code)

    def _btn_press(self, code):
        if code in (ecodes.BTN_SOUTH, ecodes.BTN_A):       # Cross — claw grip
            if CRANE_ENABLED and self._servos:
                self._claw_grip_closed = not self._claw_grip_closed
                angle = CLAW_GRIP_CLOSED if self._claw_grip_closed else CLAW_GRIP_OPEN
                self._servos.set_angle(SERVO_CLAW_GRIP, angle)
        elif code in (ecodes.BTN_EAST, ecodes.BTN_B):      # Circle — claw arm
            if CRANE_ENABLED and self._servos:
                self._claw_arm_down = not self._claw_arm_down
                angle = CLAW_ARM_DOWN if self._claw_arm_down else CLAW_ARM_UP
                self._servos.set_angle(SERVO_CLAW_ARM, angle)
        elif code in (ecodes.BTN_NORTH, ecodes.BTN_Y):     # Triangle — beep
            if self._buzzer:
                self._buzzer.beep()
        elif code in (ecodes.BTN_WEST, ecodes.BTN_X):      # Square — LED cycle
            if self._leds:
                self._led_mode_idx = (self._led_mode_idx + 1) % len(self._led_modes)
                self._leds.set_mode(self._led_modes[self._led_mode_idx], (255, 0, 0))
        elif code == ecodes.BTN_TL:                         # L1 — headlights
            if self._switches and self._switches._initialized:
                self._headlights_on = not self._headlights_on
                (self._switches.on if self._headlights_on else self._switches.off)(0)
                (self._switches.on if self._headlights_on else self._switches.off)(1)
        elif code == ecodes.BTN_TR:                         # R1 — alarm
            if self._buzzer:
                self._buzzer.play_alarm()
        elif code == ecodes.BTN_MODE:                       # PS — home servos
            if self._servos:
                self._servos.move_init()
                self._cam_pan = self._cam_tilt = 90
        elif code == ecodes.BTN_START:                      # Options — e-stop
            if self._motors:
                self._motors.stop()
            if self._servos:
                self._servos.set_angle(SERVO_STEERING, 90)
            self._lx = self._ly = 0.0
        elif code == ecodes.BTN_SELECT:                     # Share — unused
            pass
        elif code == ecodes.BTN_THUMBL:                     # L3 — speed reset
            with self._lock:
                self._speed = DEFAULT_SPEED
        elif code == ecodes.BTN_THUMBR:                     # R3 — centre camera
            if self._servos:
                self._cam_pan = self._cam_tilt = 90
                self._servos.set_angle(SERVO_CAM_PAN, 90)
                self._servos.set_angle(SERVO_CAM_TILT, 90)

    # ── Movement ────────────────────────────────────────────────────────

    def _apply_move(self):
        if not self._motors or not self._servos:
            return
        lx, ly = self._lx, self._ly
        if math.hypot(lx, ly) < 0.05:
            self._motors.stop()
            self._servos.set_angle(SERVO_STEERING, 90)
            return
        if self._shared_state:
            with self._lock:
                self._speed = self._shared_state.speed
        with self._lock:
            speed = self._speed
        d = 'forward' if ly >= 0 else 'backward'
        if abs(lx) < 0.1:
            turn, radius = 'no', 0.5
        elif lx > 0:
            turn, radius = 'right', max(0.2, 0.5 - lx * DS4_STEER_SENSITIVITY * 0.3)
        else:
            turn, radius = 'left', max(0.2, 0.5 + lx * DS4_STEER_SENSITIVITY * 0.3)
        s = max(10, int(speed * abs(ly))) if abs(ly) > 0.1 else 0
        if s > 0:
            self._motors.move(s, d, turn, radius)
        steer = max(30, min(150, 90 - int(lx * 60 * DS4_STEER_SENSITIVITY)))
        self._servos.set_angle(SERVO_STEERING, steer)

    def _apply_camera(self):
        if not self._servos:
            return
        rx, ry = self._rx, self._ry
        pan = max(0, min(180, int(90 + rx * 90 * DS4_CAM_SENSITIVITY)))
        # Tilt: ry > 0 = stick down → tilt down (lower angle)
        #       ry < 0 = stick up → tilt up (higher angle)
        tilt = max(0, min(180, int(90 - ry * 90 * DS4_CAM_SENSITIVITY)))
        if pan != self._cam_pan or tilt != self._cam_tilt:
            self._cam_pan, self._cam_tilt = pan, tilt
            self._servos.set_angle(SERVO_CAM_PAN, pan)
            self._servos.set_angle(SERVO_CAM_TILT, tilt)

    def _apply_triggers(self):
        with self._lock:
            delta = int(self._r2 * 3) - int(self._l2 * 3)
            if delta:
                self._speed = max(0, min(100, self._speed + delta))
                if self._shared_state:
                    self._shared_state.speed = self._speed

    def _apply_dpad(self):
        """D-pad controls the claw.

        FIX: hat_y > 0 means D-pad DOWN → arm goes DOWN (CLAW_ARM_DOWN).
             hat_y < 0 means D-pad UP   → arm goes UP   (CLAW_ARM_UP).
        """
        if not CRANE_ENABLED or not self._servos:
            return
        if self._hat_y > 0:                                    # D-pad DOWN
            self._servos.set_angle(SERVO_CLAW_ARM, CLAW_ARM_DOWN)
        elif self._hat_y < 0:                                  # D-pad UP
            self._servos.set_angle(SERVO_CLAW_ARM, CLAW_ARM_UP)
        if self._hat_x < 0:                                    # D-pad LEFT
            self._servos.set_angle(SERVO_CLAW_GRIP, CLAW_GRIP_OPEN)
        elif self._hat_x > 0:                                  # D-pad RIGHT
            self._servos.set_angle(SERVO_CLAW_GRIP, CLAW_GRIP_CLOSED)

    def shutdown(self):
        self.stop()
