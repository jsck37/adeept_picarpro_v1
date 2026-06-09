"""DS4 Bluetooth controller via evdev.

DS4 over Bluetooth creates TWO /dev/input/eventX devices:
  - Main gamepad: sticks, buttons, triggers, D-pad
  - Touchpad: only ABS_X/ABS_Y + BTN_LEFT

We MUST select the main gamepad, not the touchpad.
The main gamepad has BTN_SOUTH (Cross) in its key capabilities.

Stick ranges vary by connection:
  - Bluetooth (hid-sony): 0-255, centre 128
  - USB (hid-playstation): -32768..32767, centre 0
Axis ranges are auto-detected from device capabilities.

Button mapping (v1):
  - Left stick: Drive wheels (LY inverted + speed mult)
  - Right stick: Smooth crane pan/tilt control (servos 1 & 2)
  - PS button: Home all servos
  - L1: Toggle headlights on/off
  - R1: Buzzer beep
  - L2: Left headlight blinker toggle
  - R2: Right headlight blinker toggle
  - Options: Toggle drift mode (also sets LED police light, no sound)
  - Cross (BTN_SOUTH): Claw grip toggle
  - Circle (BTN_EAST): Claw arm toggle
  - Triangle (BTN_NORTH): Start CV line following (trackLineCV)
  - Square (BTN_WEST): Start hand tracking (trackHand)
  - Triangle/Square auto modes: any subsequent button press stops auto mode first
"""

import math, os, select, threading, time
from Server.logger import logger

try:
    import evdev
    from evdev import InputDevice, ecodes
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

from Server.config import (
    DS4_ENABLED, DS4_DEVICE_NAME, DS4_DEADZONE,
    DS4_STEER_SENSITIVITY, DS4_CAM_SENSITIVITY,
    DS4_HEARTBEAT_TIMEOUT, DS4_WATCHDOG_INTERVAL, DS4_READ_TIMEOUT,
    DEFAULT_SPEED, SERVO_CAM_PAN, SERVO_CAM_TILT,
    SERVO_STEERING, SERVO_CLAW_ARM, SERVO_CLAW_GRIP,
    CLAW_ARM_UP, CLAW_ARM_DOWN, CLAW_GRIP_OPEN, CLAW_GRIP_CLOSED,
    CRANE_ENABLED,
    DS4_INVERT_LY, DS4_INVERT_RY, DS4_SPEED_MULT, DS4_CRANE_STEP,
    DS4_DRIFT_STEER_RANGE, DS4_STEER_RANGE,
)


class DS4Controller:
    """DualShock 4 gamepad controller with robust auto-reconnect."""

    def __init__(self):
        self._running = False
        self._connected = False
        self._device = None
        self._thread = None
        self._watchdog_thread = None
        self._axis_ranges = {}
        self._last_event_time = 0.0
        self._connect_count = 0

        # Stick state (normalised -1..+1)
        self._lx = self._ly = self._rx = self._ry = 0.0
        self._l2 = self._r2 = 0.0
        self._hat_x = self._hat_y = 0
        self._btn_state = {}

        # Hardware refs (set in start())
        self._motors = self._servos = self._leds = None
        self._buzzer = self._switches = self._shared_state = None
        self._autonomous = None
        self._speed = DEFAULT_SPEED
        self._cam_pan = self._cam_tilt = 90
        self._headlights_on = self._claw_grip_closed = self._claw_arm_down = False
        self._lock = threading.Lock()

        # ── Drift mode ──
        self._drift_mode = False
        self._drift_active = False  # actively drifting (steering + throttle)

        # ── Blinker state ──
        self._left_blinking = False
        self._right_blinking = False
        self._left_blink_thread = None
        self._right_blink_thread = None

        # ── Auto mode state ──
        self._auto_mode_active = False

        # ── Trigger blinker debounce ──
        self._l2_pressed = False
        self._r2_pressed = False

    # -- Public API -------------------------------------------------------

    def start(self, motors, servos, leds, buzzer, switches,
              speed=DEFAULT_SPEED, shared_state=None, autonomous=None):
        if not DS4_ENABLED or not HAS_EVDEV:
            logger.warning("[DS4] Disabled or evdev missing")
            return
        self._motors, self._servos, self._leds = motors, servos, leds
        self._buzzer, self._switches = buzzer, switches
        self._speed, self._shared_state = speed, shared_state
        self._autonomous = autonomous
        self._running = True
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()
        logger.info("[DS4] Searching for controller...")

    def stop(self):
        self._running = False
        self._disconnect()

    @property
    def connected(self):
        return self._connected

    @property
    def drift_mode(self):
        return self._drift_mode

    def get_status(self):
        return {
            'enabled': DS4_ENABLED,
            'connected': self._connected,
            'speed': self._speed,
            'lx': round(self._lx, 2),
            'ly': round(self._ly, 2),
            'rx': round(self._rx, 2),
            'ry': round(self._ry, 2),
            'connect_count': self._connect_count,
            'drift_mode': self._drift_mode,
        }

    # -- Device discovery -------------------------------------------------

    def _find_device(self):
        """Find the MAIN gamepad device (not the touchpad)."""
        if not HAS_EVDEV:
            return None
        try:
            paths = evdev.list_devices()
            if not paths:
                return None
            devices = []
            for p in paths:
                try:
                    devices.append(InputDevice(p))
                except OSError:
                    continue
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

            for dev in candidates:
                caps = dev.capabilities(absinfo=True)
                keys = caps.get(ecodes.EV_KEY, [])
                if ecodes.BTN_SOUTH in keys or ecodes.BTN_A in keys:
                    return dev

            for dev in candidates:
                caps = dev.capabilities(absinfo=True)
                if any(c == ecodes.ABS_X for c, _ in caps.get(ecodes.EV_ABS, [])):
                    return dev
            return candidates[0]
        except Exception as e:
            logger.error(f"[DS4] Search error: {e}")
            return None

    def _device_alive(self):
        """Check that the current device fd is still valid."""
        if not self._device:
            return False
        try:
            fd = self._device.fd
            if fd is None or fd < 0:
                return False
            if not os.path.exists(self._device.path):
                return False
            return True
        except Exception:
            return False

    def _connect(self, device):
        try:
            device.grab()
        except Exception as e:
            logger.warning(f"[DS4] Grab failed (non-fatal): {e}")

        self._device = device
        self._connected = True
        self._axis_ranges = {}
        self._last_event_time = time.monotonic()
        self._connect_count += 1

        try:
            caps = device.capabilities(absinfo=True)
            for code, info in caps.get(ecodes.EV_ABS, []):
                self._axis_ranges[code] = (info.min, info.max)
            abs_info = {
                ecodes.ABS.get(k, hex(k)): f'{v[0]}..{v[1]}'
                for k, v in self._axis_ranges.items()
            }
            logger.info(f"[DS4] Connected #{self._connect_count}: "
                  f"{device.name} @ {device.path}")
            logger.info(f"[DS4] Axes: {abs_info}")
        except Exception:
            logger.info(f"[DS4] Connected #{self._connect_count}: {device.name}")

        self._thread = threading.Thread(target=self._event_loop, daemon=True)
        self._thread.start()

    def _disconnect(self):
        was_connected = self._connected
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
        # Stop blinkers
        self._left_blinking = False
        self._right_blinking = False
        if was_connected:
            logger.warning("[DS4] Disconnected — will auto-reconnect when available")

    # -- Background threads -----------------------------------------------

    def _watchdog(self):
        """Periodically check for new devices when disconnected, and verify
        the heartbeat when connected.

        Uses shorter intervals when disconnected for faster reconnection.
        """
        while self._running:
            if not self._connected:
                dev = self._find_device()
                if dev:
                    self._connect(dev)
                # Poll faster when searching for controller (1s vs 3s)
                time.sleep(1.0)
            else:
                elapsed = time.monotonic() - self._last_event_time
                if elapsed > DS4_HEARTBEAT_TIMEOUT:
                    if not self._device_alive():
                        logger.warning(f"[DS4] Heartbeat timeout ({elapsed:.0f}s) "
                              f"and device gone — disconnecting")
                        self._disconnect()
                    else:
                        logger.info(f"[DS4] Heartbeat timeout ({elapsed:.0f}s) "
                              f"but device still present — resetting timer")
                        self._last_event_time = time.monotonic()
                time.sleep(DS4_WATCHDOG_INTERVAL)

    def _event_loop(self):
        """Read events using select() + read()."""
        while self._running and self._connected:
            try:
                fd = self._device.fd
                if fd is None:
                    logger.warning("[DS4] File descriptor became None")
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

            except OSError as e:
                logger.error(f"[DS4] Device lost (OSError): {e}")
                self._disconnect()
                break
            except ValueError as e:
                logger.error(f"[DS4] Device lost (ValueError): {e}")
                self._disconnect()
                break
            except Exception as e:
                err_str = str(e).lower()
                if 'not open' in err_str or 'closed' in err_str or 'bad file' in err_str:
                    logger.error(f"[DS4] Device lost: {e}")
                    self._disconnect()
                    break
                logger.warning(f"[DS4] Event read error (will retry): {e}")
                time.sleep(0.05)

        if self._connected:
            self._disconnect()

    # -- Axis normalisation -----------------------------------------------

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

    # -- Event handlers ---------------------------------------------------

    def _on_axis(self, code, value):
        if code == ecodes.ABS_X:
            self._lx = self._norm_axis(value, code)
            self._apply_move()
        elif code == ecodes.ABS_Y:
            # INVERTED: push stick forward (value < centre) = ly > 0 = forward
            raw = self._norm_axis(value, code)
            self._ly = -raw if DS4_INVERT_LY else raw
            self._apply_move()
        elif code == ecodes.ABS_RX:
            self._rx = self._norm_axis(value, code)
            self._apply_crane_pan_tilt()
        elif code == ecodes.ABS_RY:
            # INVERTED for crane: push stick up = ry < 0 → we want tilt up
            raw = self._norm_axis(value, code)
            self._ry = -raw if DS4_INVERT_RY else raw
            self._apply_crane_pan_tilt()
        elif code == ecodes.ABS_Z:
            self._l2 = self._norm_trigger(value, code)
            # L2 trigger: toggle left blinker on press (not hold)
            if self._l2 > 0.5 and not self._l2_pressed:
                self._l2_pressed = True
                self._start_left_blinker()
            elif self._l2 < 0.3:
                self._l2_pressed = False
        elif code == ecodes.ABS_RZ:
            self._r2 = self._norm_trigger(value, code)
            # R2 trigger: toggle right blinker on press (not hold)
            if self._r2 > 0.5 and not self._r2_pressed:
                self._r2_pressed = True
                self._start_right_blinker()
            elif self._r2 < 0.3:
                self._r2_pressed = False
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

    def _stop_auto_mode_if_active(self):
        """Stop any running auto mode if one is active."""
        if self._auto_mode_active and self._autonomous:
            try:
                self._autonomous.stop()
                logger.info("[DS4] Auto mode stopped by gamepad button")
            except Exception as e:
                logger.warning(f"[DS4] Error stopping auto mode: {e}")
            self._auto_mode_active = False

    def _btn_press(self, code):
        # ── Auto-mode buttons: Triangle (trackLineCV) and Square (trackHand) ──
        if code in (ecodes.BTN_NORTH, ecodes.BTN_Y):     # Triangle - CV line follow
            if self._autonomous and self._shared_state:
                # Ensure camera is available
                if hasattr(self._shared_state, 'camera') and self._shared_state.camera:
                    self._autonomous._camera = self._shared_state.camera
                elif hasattr(self._shared_state, 'init_camera'):
                    self._shared_state.init_camera()
                    if self._shared_state.camera:
                        self._autonomous._camera = self._shared_state.camera
                self._autonomous.start('trackLineCV')
                self._auto_mode_active = True
                logger.info("[DS4] Started CV line following mode")
            return

        if code in (ecodes.BTN_WEST, ecodes.BTN_X):      # Square - hand tracking
            if self._autonomous and self._shared_state:
                if hasattr(self._shared_state, 'camera') and self._shared_state.camera:
                    self._autonomous._camera = self._shared_state.camera
                elif hasattr(self._shared_state, 'init_camera'):
                    self._shared_state.init_camera()
                    if self._shared_state.camera:
                        self._autonomous._camera = self._shared_state.camera
                self._autonomous.start('trackHand')
                self._auto_mode_active = True
                logger.info("[DS4] Started hand tracking mode")
            return

        # ── All other buttons: stop auto mode first if active ──
        self._stop_auto_mode_if_active()

        if code in (ecodes.BTN_SOUTH, ecodes.BTN_A):       # Cross - claw grip
            if CRANE_ENABLED and self._servos:
                self._claw_grip_closed = not self._claw_grip_closed
                angle = CLAW_GRIP_CLOSED if self._claw_grip_closed else CLAW_GRIP_OPEN
                self._smooth_crane(SERVO_CLAW_GRIP, angle)
        elif code in (ecodes.BTN_EAST, ecodes.BTN_B):      # Circle - claw arm
            if CRANE_ENABLED and self._servos:
                self._claw_arm_down = not self._claw_arm_down
                angle = CLAW_ARM_DOWN if self._claw_arm_down else CLAW_ARM_UP
                self._smooth_crane(SERVO_CLAW_ARM, angle)
        elif code == ecodes.BTN_TL:                         # L1 - headlights toggle
            if self._switches and self._switches._initialized:
                self._headlights_on = not self._headlights_on
                (self._switches.on if self._headlights_on else self._switches.off)(0)
                (self._switches.on if self._headlights_on else self._switches.off)(1)
        elif code == ecodes.BTN_TR:                         # R1 - buzzer beep
            if self._buzzer:
                self._buzzer.beep()
        elif code == ecodes.BTN_MODE:                       # PS - home servos
            if self._servos:
                self._servos.move_init()
                self._cam_pan = self._cam_tilt = 90
        elif code == ecodes.BTN_START:                      # Options - toggle drift mode
            self._drift_mode = not self._drift_mode
            if self._leds:
                if self._drift_mode:
                    self._leds.set_mode('police', (255, 0, 0))
                else:
                    self._leds.set_mode('off', (0, 0, 0))
            logger.info(f"[DS4] Drift mode: {'ON' if self._drift_mode else 'OFF'}")
        elif code == ecodes.BTN_SELECT:                     # Share - unused
            pass
        elif code == ecodes.BTN_THUMBL:                     # L3 - unused
            pass
        elif code == ecodes.BTN_THUMBR:                     # R3 - unused
            pass

    # -- Smooth crane movement --------------------------------------------

    def _smooth_crane(self, servo_id, target_angle, step=DS4_CRANE_STEP, delay=0.03):
        """Move a crane servo smoothly in small steps to avoid jerky motion."""
        if not self._servos:
            return
        current = self._servos.get_angle(servo_id)
        diff = target_angle - current
        if abs(diff) <= step:
            self._servos.set_angle(servo_id, target_angle)
            return

        def _run():
            pos = current
            direction = 1 if diff > 0 else -1
            while self._connected and self._running:
                pos += direction * step
                if direction > 0 and pos >= target_angle:
                    pos = target_angle
                    self._servos.set_angle(servo_id, pos)
                    break
                elif direction < 0 and pos <= target_angle:
                    pos = target_angle
                    self._servos.set_angle(servo_id, pos)
                    break
                self._servos.set_angle(servo_id, pos)
                time.sleep(delay)

        threading.Thread(target=_run, daemon=True).start()

    # -- Movement ---------------------------------------------------------

    def _apply_move(self):
        """Apply left stick to wheel motors with INVERTED Y axis and speed boost.

        ly > 0 (stick pushed forward) = car moves FORWARD
        ly < 0 (stick pulled back) = car moves BACKWARD

        Speed is multiplied by DS4_SPEED_MULT for faster response.
        """
        if not self._motors or not self._servos:
            return
        lx, ly = self._lx, self._ly
        if math.hypot(lx, ly) < 0.05:
            self._motors.stop()
            self._servos.set_angle(SERVO_STEERING, 90)
            self._drift_active = False
            return
        if self._shared_state:
            with self._lock:
                self._speed = self._shared_state.speed

        with self._lock:
            speed = self._speed

        # ly > 0 = forward (stick pushed), ly < 0 = backward (stick pulled)
        d = 'forward' if ly >= 0 else 'backward'

        if abs(lx) < 0.1:
            turn, radius = 'no', 0.5
        elif lx > 0:
            turn, radius = 'right', max(0.2, 0.5 - lx * DS4_STEER_SENSITIVITY * 0.3)
        else:
            turn, radius = 'left', max(0.2, 0.5 + lx * DS4_STEER_SENSITIVITY * 0.3)

        # Apply speed multiplier for faster driving
        abs_ly = abs(ly)
        s = max(10, int(speed * abs_ly * DS4_SPEED_MULT)) if abs_ly > 0.1 else 0
        s = min(100, s)  # Cap at 100

        # ── Drift mode ──
        if self._drift_mode and s > 0 and abs(lx) > 0.3:
            self._drift_active = True
            # Over-steer: exaggerate front wheel angle
            steer = max(25, min(155, 90 - int(lx * DS4_DRIFT_STEER_RANGE * DS4_STEER_SENSITIVITY)))
            # Full rear power, reduced on inner wheel for slide effect
            drift_speed = min(100, int(s * 1.2))
            self._motors.move(drift_speed, d, turn, max(0.15, radius * 0.6))
        else:
            self._drift_active = False
            if s > 0:
                self._motors.move(s, d, turn, radius)
            steer = max(30, min(150, 90 - int(lx * DS4_STEER_RANGE * DS4_STEER_SENSITIVITY)))

        self._servos.set_angle(SERVO_STEERING, steer)

    def _apply_crane_pan_tilt(self):
        """Apply right stick to camera pan/tilt servos (1 & 2).

        Right stick X → cam pan servo (1)
        Right stick Y → cam tilt servo (2)

        Uses proportional control: stick deflection determines step size,
        making small movements precise and large movements fast.
        """
        if not self._servos:
            return
        rx, ry = self._rx, self._ry

        # Only move if stick is deflected enough
        if abs(rx) < DS4_DEADZONE and abs(ry) < DS4_DEADZONE:
            return

        # Pan (servo 1): rx > 0 → increase angle (pan right), rx < 0 → decrease (pan left)
        if abs(rx) >= DS4_DEADZONE:
            current_pan = self._servos.get_angle(SERVO_CAM_PAN)
            # Proportional step: 1° at small deflection, up to 10° at full deflection
            pan_step = max(1, int(abs(rx) * DS4_CAM_SENSITIVITY * 10))
            if rx > 0:
                new_pan = min(180, current_pan + pan_step)
            else:
                new_pan = max(0, current_pan - pan_step)
            if new_pan != current_pan:
                self._servos.set_angle(SERVO_CAM_PAN, new_pan)
                self._cam_pan = new_pan

        # Tilt (servo 2): ry > 0 (stick up) → increase angle (tilt up)
        if abs(ry) >= DS4_DEADZONE:
            current_tilt = self._servos.get_angle(SERVO_CAM_TILT)
            # Proportional step: 1° at small deflection, up to 10° at full deflection
            tilt_step = max(1, int(abs(ry) * DS4_CAM_SENSITIVITY * 10))
            if ry > 0:
                new_tilt = min(180, current_tilt + tilt_step)
            else:
                new_tilt = max(0, current_tilt - tilt_step)
            if new_tilt != current_tilt:
                self._servos.set_angle(SERVO_CAM_TILT, new_tilt)
                self._cam_tilt = new_tilt

    def _apply_dpad(self):
        """D-pad controls the claw grip with smooth movement.

        hat_y > 0 = D-pad DOWN → arm goes DOWN
        hat_y < 0 = D-pad UP   → arm goes UP
        hat_x < 0 = D-pad LEFT  → grip opens
        hat_x > 0 = D-pad RIGHT → grip closes
        """
        if not CRANE_ENABLED or not self._servos:
            return
        if self._hat_y > 0:                                    # D-pad DOWN
            self._smooth_crane(SERVO_CLAW_ARM, CLAW_ARM_DOWN)
        elif self._hat_y < 0:                                  # D-pad UP
            self._smooth_crane(SERVO_CLAW_ARM, CLAW_ARM_UP)
        if self._hat_x < 0:                                    # D-pad LEFT
            self._smooth_crane(SERVO_CLAW_GRIP, CLAW_GRIP_OPEN)
        elif self._hat_x > 0:                                  # D-pad RIGHT
            self._smooth_crane(SERVO_CLAW_GRIP, CLAW_GRIP_CLOSED)

    # -- Blinker methods --------------------------------------------------

    def _start_left_blinker(self):
        """Toggle left headlight blinker on/off."""
        if self._left_blinking:
            self._left_blinking = False
            # Make sure left light is off when stopping blinker
            if self._switches and self._switches._initialized:
                self._switches.off(0)
            return
        self._left_blinking = True

        def _blink():
            while self._left_blinking and self._connected and self._running:
                if self._switches and self._switches._initialized:
                    self._switches.on(0)
                time.sleep(0.3)
                if not self._left_blinking:
                    break
                if self._switches and self._switches._initialized:
                    self._switches.off(0)
                time.sleep(0.3)

        self._left_blink_thread = threading.Thread(target=_blink, daemon=True)
        self._left_blink_thread.start()
        logger.info("[DS4] Left blinker ON")

    def _start_right_blinker(self):
        """Toggle right headlight blinker on/off."""
        if self._right_blinking:
            self._right_blinking = False
            # Make sure right light is off when stopping blinker
            if self._switches and self._switches._initialized:
                self._switches.off(1)
            return
        self._right_blinking = True

        def _blink():
            while self._right_blinking and self._connected and self._running:
                if self._switches and self._switches._initialized:
                    self._switches.on(1)
                time.sleep(0.3)
                if not self._right_blinking:
                    break
                if self._switches and self._switches._initialized:
                    self._switches.off(1)
                time.sleep(0.3)

        self._right_blink_thread = threading.Thread(target=_blink, daemon=True)
        self._right_blink_thread.start()
        logger.info("[DS4] Right blinker ON")

    def shutdown(self):
        self._left_blinking = False
        self._right_blinking = False
        self.stop()
