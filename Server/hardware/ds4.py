"""DualShock 4 Bluetooth controller input handler.

Connects to a PS4 controller paired via Bluetooth (device name "Wireless Controller").
Uses the evdev library to read input events and maps them to robot controls.

Controller mapping:
  Left stick   -> Wheel movement (forward/backward/turn)
  Right stick  -> Camera pan/tilt
  L2 / R2      -> Speed down / up
  Cross        -> Claw grip toggle
  Circle       -> Claw arm toggle
  Triangle     -> Buzzer beep
  Square       -> LED mode cycle
  L1           -> Headlights toggle
  R1           -> Horn (alarm)
  D-pad Up/Down -> Claw arm up/down
  D-pad Left/Right -> Claw grip open/close
  PS button    -> Home all servos
  Options      -> Stop all movement
  Share        -> Toggle CV mode

Prerequisites:
  - DS4 paired via Bluetooth: bluetoothctl -> connect <MAC>
  - evdev installed: pip3 install evdev
  - Controller appears as /dev/input/eventX

The controller is auto-discovered by searching evdev devices for
name containing "Wireless Controller" or "DualShock".
"""

import math
import threading
import time

try:
    import evdev
    from evdev import InputDevice, categorize, ecodes
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
    """Background thread that reads DS4 input and drives the robot."""

    # ── Axis codes (evdev) ──────────────────────────────────────────────
    AXIS_LX = ecodes.ABS_X if HAS_EVDEV else 0x00       # Left stick X
    AXIS_LY = ecodes.ABS_Y if HAS_EVDEV else 0x01       # Left stick Y
    AXIS_RX = ecodes.ABS_RX if HAS_EVDEV else 0x03      # Right stick X
    AXIS_RY = ecodes.ABS_RY if HAS_EVDEV else 0x04      # Right stick Y
    AXIS_L2 = ecodes.ABS_Z if HAS_EVDEV else 0x02       # L2 trigger
    AXIS_R2 = ecodes.ABS_RZ if HAS_EVDEV else 0x05      # R2 trigger
    AXIS_HAT_X = ecodes.ABS_HAT0X if HAS_EVDEV else 0x10  # D-pad X
    AXIS_HAT_Y = ecodes.ABS_HAT0Y if HAS_EVDEV else 0x11  # D-pad Y

    # ── Button codes (evdev) ────────────────────────────────────────────
    BTN_CROSS    = ecodes.BTN_A if HAS_EVDEV else 0x130
    BTN_CIRCLE   = ecodes.BTN_B if HAS_EVDEV else 0x131
    BTN_SQUARE   = ecodes.BTN_X if HAS_EVDEV else 0x133
    BTN_TRIANGLE = ecodes.BTN_Y if HAS_EVDEV else 0x134
    BTN_L1       = ecodes.BTN_TL if HAS_EVDEV else 0x136
    BTN_R1       = ecodes.BTN_TR if HAS_EVDEV else 0x137
    BTN_L2       = ecodes.BTN_TL2 if HAS_EVDEV else 0x138
    BTN_R2       = ecodes.BTN_TR2 if HAS_EVDEV else 0x139
    BTN_SHARE    = ecodes.BTN_SELECT if HAS_EVDEV else 0x13a
    BTN_OPTIONS  = ecodes.BTN_START if HAS_EVDEV else 0x13b
    BTN_PS       = ecodes.BTN_MODE if HAS_EVDEV else 0x13c
    BTN_L3       = ecodes.BTN_THUMBL if HAS_EVDEV else 0x13d
    BTN_R3       = ecodes.BTN_THUMBR if HAS_EVDEV else 0x13e

    def __init__(self):
        self._running = False
        self._connected = False
        self._device = None
        self._thread = None
        self._watchdog_thread = None

        # ── Stick state (normalized: -1.0 to +1.0) ──
        self._lx = 0.0   # left stick X
        self._ly = 0.0   # left stick Y
        self._rx = 0.0   # right stick X
        self._ry = 0.0   # right stick Y
        self._l2 = 0.0   # L2 trigger (0.0 - 1.0)
        self._r2 = 0.0   # R2 trigger (0.0 - 1.0)
        self._hat_x = 0  # D-pad X (-1, 0, 1)
        self._hat_y = 0  # D-pad Y (-1, 0, 1)

        # ── Button press tracking (for edge detection) ──
        self._btn_state = {}
        self._btn_just_pressed = {}

        # ── Robot hardware references (set via start()) ──
        self._motors = None
        self._servos = None
        self._leds = None
        self._buzzer = None
        self._switches = None
        self._speed_ref = None  # SharedState.speed reference (list with one int for mutability)
        self._speed = DEFAULT_SPEED

        # ── Camera state ──
        self._cam_pan = 90
        self._cam_tilt = 90

        # ── Toggle states ──
        self._headlights_on = False
        self._claw_grip_closed = False
        self._claw_arm_down = False
        self._led_mode_idx = 0
        self._led_modes = ['off', 'solid', 'breath', 'flow', 'rainbow', 'police']
        self._cv_mode_idx = 0
        self._cv_modes = ['none', 'findColor', 'findlineCV', 'watchDog']

        # ── Movement state ──
        self._last_move_dir = 'stop'
        self._move_throttle = 0

        # ── Lock ──
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────────

    def start(self, motors, servos, leds, buzzer, switches, speed=DEFAULT_SPEED, shared_state=None):
        """Start the DS4 controller background thread.

        Args:
            motors: MotorController instance
            servos: ServoController instance
            leds: LEDController instance
            buzzer: BuzzerController instance
            switches: SwitchController instance
            speed: initial speed (0-100)
            shared_state: SharedState instance (for speed sync with web UI)
        """
        if not DS4_ENABLED:
            print("[DS4] Disabled in config.py")
            return

        if not HAS_EVDEV:
            print("[DS4] evdev not installed! pip3 install evdev")
            return

        self._motors = motors
        self._servos = servos
        self._leds = leds
        self._buzzer = buzzer
        self._switches = switches
        self._speed = speed
        self._shared_state = shared_state

        self._running = True

        # Start watchdog thread that auto-discovers the controller
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True
        )
        self._watchdog_thread.start()

        print("[DS4] Controller thread started, searching for device...")

    def stop(self):
        """Stop the controller thread and disconnect."""
        self._running = False
        self._disconnect()
        print("[DS4] Stopped")

    @property
    def connected(self):
        return self._connected

    @property
    def speed(self):
        with self._lock:
            return self._speed

    @speed.setter
    def speed(self, value):
        with self._lock:
            self._speed = max(0, min(100, value))

    def get_status(self):
        """Return DS4 status dict for the web UI."""
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
        """Search for the DS4 controller among evdev input devices.

        Returns InputDevice or None.
        """
        if not HAS_EVDEV:
            return None

        try:
            devices = [InputDevice(path) for path in evdev.list_devices()]
            for dev in devices:
                name_lower = dev.name.lower()
                # DS4 over Bluetooth: "Wireless Controller"
                # DS4 over USB: "Sony Interactive Entertainment Wireless Controller"
                # Also match generic "DualShock" name variants
                if (DS4_DEVICE_NAME.lower() in name_lower or
                        'dualshock' in name_lower or
                        'sony interactive' in name_lower):
                    print(f"[DS4] Found device: {dev.name} at {dev.path}")
                    return dev
        except Exception as e:
            print(f"[DS4] Device search error: {e}")

        return None

    def _connect(self, device):
        """Connect to a discovered DS4 device."""
        try:
            # Grab the device so events don't also go to the OS
            device.grab()
            self._device = device
            self._connected = True
            print(f"[DS4] Connected: {device.name}")

            # Start the event reading thread
            self._thread = threading.Thread(
                target=self._event_loop, daemon=True
            )
            self._thread.start()

        except Exception as e:
            print(f"[DS4] Connection failed: {e}")
            self._connected = False

    def _disconnect(self):
        """Disconnect from the DS4 device."""
        self._connected = False
        if self._device is not None:
            try:
                self._device.ungrab()
            except Exception:
                pass
            self._device = None

        # Stop robot on disconnect
        if self._motors is not None:
            try:
                self._motors.stop()
            except Exception:
                pass

        # Reset sticks
        self._lx = 0.0
        self._ly = 0.0
        self._rx = 0.0
        self._ry = 0.0

        print("[DS4] Disconnected")

    # ── Background threads ──────────────────────────────────────────────

    def _watchdog_loop(self):
        """Periodically check if the controller is connected.
        If not, try to find and connect to one.
        """
        while self._running:
            if not self._connected:
                device = self._find_device()
                if device is not None:
                    self._connect(device)
            time.sleep(3.0)  # Check every 3 seconds

    def _event_loop(self):
        """Main event reading loop. Runs in its own thread."""
        while self._running and self._connected:
            try:
                # Read events with a timeout so we can check _running
                # evdev's read_loop() is blocking, so we use read_one() instead
                event = self._device.read_one()
                if event is None:
                    time.sleep(0.005)  # 5ms poll interval
                    continue

                self._handle_event(event)

            except OSError:
                # Device disconnected
                print("[DS4] Device removed or connection lost")
                self._disconnect()
                break
            except Exception as e:
                print(f"[DS4] Event error: {e}")
                time.sleep(0.01)

        self._disconnect()

    # ── Event handling ──────────────────────────────────────────────────

    def _handle_event(self, event):
        """Process a single evdev input event."""
        if event.type == ecodes.EV_ABS:
            self._handle_axis(event)
        elif event.type == ecodes.EV_KEY:
            self._handle_button(event)

    def _normalize_axis(self, value, min_val=0, max_val=255):
        """Normalize an axis value to -1.0..+1.0 with center=0."""
        center = (min_val + max_val) / 2.0
        half_range = (max_val - min_val) / 2.0
        normalized = (value - center) / half_range

        # Apply deadzone
        if abs(normalized) < DS4_DEADZONE:
            return 0.0

        # Re-scale from deadzone edge to full range
        if normalized > 0:
            normalized = (normalized - DS4_DEADZONE) / (1.0 - DS4_DEADZONE)
        else:
            normalized = (normalized + DS4_DEADZONE) / (1.0 - DS4_DEADZONE)

        return max(-1.0, min(1.0, normalized))

    def _normalize_trigger(self, value, min_val=0, max_val=255):
        """Normalize a trigger value to 0.0..1.0."""
        if max_val == min_val:
            return 0.0
        normalized = (value - min_val) / (max_val - min_val)
        return max(0.0, min(1.0, normalized))

    def _handle_axis(self, event):
        """Handle an absolute axis event (sticks/triggers/dpad)."""
        code = event.code
        value = event.value

        if code == self.AXIS_LX:
            self._lx = self._normalize_axis(value)
            self._apply_movement()
        elif code == self.AXIS_LY:
            self._ly = -self._normalize_axis(value)  # Invert Y (up = forward)
            self._apply_movement()
        elif code == self.AXIS_RX:
            self._rx = self._normalize_axis(value)
            self._apply_camera()
        elif code == self.AXIS_RY:
            self._ry = -self._normalize_axis(value)  # Invert Y (up = look up)
            self._apply_camera()
        elif code == self.AXIS_L2:
            self._l2 = self._normalize_trigger(value)
            self._apply_triggers()
        elif code == self.AXIS_R2:
            self._r2 = self._normalize_trigger(value)
            self._apply_triggers()
        elif code == self.AXIS_HAT_X:
            self._hat_x = value  # -1, 0, or 1
            self._apply_dpad()
        elif code == self.AXIS_HAT_Y:
            self._hat_y = -value  # Invert: D-pad up = +1
            self._apply_dpad()

    def _handle_button(self, event):
        """Handle a button press/release event."""
        code = event.code
        pressed = event.value == 1  # 1=pressed, 0=released

        # Track button state
        was_pressed = self._btn_state.get(code, False)
        self._btn_state[code] = pressed

        # Edge detection: only act on press (not release)
        if pressed and not was_pressed:
            self._on_button_press(code)

    def _on_button_press(self, code):
        """Handle a single button press (rising edge)."""
        if code == self.BTN_CROSS:
            # Claw grip toggle
            if CRANE_ENABLED and self._servos:
                self._claw_grip_closed = not self._claw_grip_closed
                angle = CLAW_GRIP_CLOSED if self._claw_grip_closed else CLAW_GRIP_OPEN
                self._servos.set_angle(SERVO_CLAW_GRIP, angle)
                state = "closed" if self._claw_grip_closed else "open"
                print(f"[DS4] Claw grip: {state}")

        elif code == self.BTN_CIRCLE:
            # Claw arm toggle
            if CRANE_ENABLED and self._servos:
                self._claw_arm_down = not self._claw_arm_down
                angle = CLAW_ARM_DOWN if self._claw_arm_down else CLAW_ARM_UP
                self._servos.set_angle(SERVO_CLAW_ARM, angle)
                state = "down" if self._claw_arm_down else "up"
                print(f"[DS4] Claw arm: {state}")

        elif code == self.BTN_TRIANGLE:
            # Buzzer beep
            if self._buzzer:
                self._buzzer.beep()

        elif code == self.BTN_SQUARE:
            # LED mode cycle
            if self._leds:
                self._led_mode_idx = (self._led_mode_idx + 1) % len(self._led_modes)
                mode = self._led_modes[self._led_mode_idx]
                self._leds.set_mode(mode, (255, 0, 0))
                print(f"[DS4] LED mode: {mode}")

        elif code == self.BTN_L1:
            # Headlights toggle
            if self._switches and self._switches._initialized:
                self._headlights_on = not self._headlights_on
                if self._headlights_on:
                    self._switches.on(0)
                    self._switches.on(1)
                else:
                    self._switches.off(0)
                    self._switches.off(1)
                state = "ON" if self._headlights_on else "OFF"
                print(f"[DS4] Headlights: {state}")

        elif code == self.BTN_R1:
            # Horn (alarm)
            if self._buzzer:
                self._buzzer.play_alarm()

        elif code == self.BTN_PS:
            # Home all servos
            if self._servos:
                self._servos.move_init()
                self._cam_pan = 90
                self._cam_tilt = 90
                print("[DS4] Servos homed")

        elif code == self.BTN_OPTIONS:
            # Stop all movement
            if self._motors:
                self._motors.stop()
            if self._servos:
                self._servos.set_angle(SERVO_STEERING, 90)
            self._lx = 0.0
            self._ly = 0.0
            print("[DS4] Emergency stop")

        elif code == self.BTN_SHARE:
            # Cycle CV mode
            self._cv_mode_idx = (self._cv_mode_idx + 1) % len(self._cv_modes)
            mode = self._cv_modes[self._cv_mode_idx]
            print(f"[DS4] CV mode: {mode}")

        elif code == self.BTN_L3:
            # Speed reset to default
            with self._lock:
                self._speed = DEFAULT_SPEED
            print(f"[DS4] Speed reset: {self._speed}%")

        elif code == self.BTN_R3:
            # Center camera
            if self._servos:
                self._cam_pan = 90
                self._cam_tilt = 90
                self._servos.set_angle(SERVO_CAM_PAN, 90)
                self._servos.set_angle(SERVO_CAM_TILT, 90)
            print("[DS4] Camera centered")

    # ── Movement applications ───────────────────────────────────────────

    def _apply_movement(self):
        """Map left stick position to wheel movement and steering."""
        if self._motors is None or self._servos is None:
            return

        lx = self._lx
        ly = self._ly

        # Check if stick is in deadzone
        magnitude = math.sqrt(lx * lx + ly * ly)
        if magnitude < 0.05:
            # Stop if previously moving
            self._motors.stop()
            self._servos.set_angle(SERVO_STEERING, 90)
            return

        # Sync speed from shared state (web UI can change it)
        if self._shared_state is not None:
            with self._lock:
                self._speed = self._shared_state.speed

        with self._lock:
            speed = self._speed

        # Determine direction and turn
        # ly > 0 = forward, ly < 0 = backward
        # lx > 0 = right, lx < 0 = left
        direction = 'forward' if ly >= 0 else 'backward'
        abs_ly = abs(ly)

        # Steering: map lx to turn direction and radius
        if abs(lx) < 0.1:
            turn = 'no'
            radius = 0.5
        elif lx > 0:
            turn = 'right'
            radius = max(0.2, 0.5 - lx * DS4_STEER_SENSITIVITY * 0.3)
        else:
            turn = 'left'
            radius = max(0.2, 0.5 + lx * DS4_STEER_SENSITIVITY * 0.3)

        # Scale speed by stick Y axis (proportional control)
        scaled_speed = int(speed * abs_ly)

        # Minimum speed to actually move
        if scaled_speed < 10 and abs_ly > 0.1:
            scaled_speed = 10

        self._motors.move(scaled_speed, direction, turn, radius)

        # Map steering angle
        # Center=90, left=150, right=30 (matching WebServer.py)
        steer_angle = 90 - int(lx * 60 * DS4_STEER_SENSITIVITY)
        steer_angle = max(30, min(150, steer_angle))
        self._servos.set_angle(SERVO_STEERING, steer_angle)

    def _apply_camera(self):
        """Map right stick position to camera pan/tilt servo angles."""
        if self._servos is None:
            return

        rx = self._rx
        ry = self._ry

        # Adjust camera angles based on stick input
        # Each axis maps to an offset from 90 degrees
        pan_offset = rx * 90 * DS4_CAM_SENSITIVITY
        tilt_offset = ry * 90 * DS4_CAM_SENSITIVITY

        new_pan = max(0, min(180, int(90 + pan_offset)))
        new_tilt = max(0, min(180, int(90 + tilt_offset)))

        if new_pan != self._cam_pan or new_tilt != self._cam_tilt:
            self._cam_pan = new_pan
            self._cam_tilt = new_tilt
            self._servos.set_angle(SERVO_CAM_PAN, new_pan)
            self._servos.set_angle(SERVO_CAM_TILT, new_tilt)

    def _apply_triggers(self):
        """Map L2/R2 to speed control.

        L2 = speed down (proportional to how much it's pressed)
        R2 = speed up (proportional)
        """
        with self._lock:
            # R2 increases speed, L2 decreases
            delta = int(self._r2 * 3) - int(self._l2 * 3)  # ±3 per cycle
            if delta != 0:
                self._speed = max(0, min(100, self._speed + delta))
                # Sync back to shared state (web UI)
                if self._shared_state is not None:
                    self._shared_state.speed = self._speed

    def _apply_dpad(self):
        """Map D-pad to claw arm and grip controls."""
        if not CRANE_ENABLED or self._servos is None:
            return

        # D-pad Up/Down -> Claw arm
        if self._hat_y > 0:
            self._servos.set_angle(SERVO_CLAW_ARM, CLAW_ARM_UP)
            self._claw_arm_down = False
        elif self._hat_y < 0:
            self._servos.set_angle(SERVO_CLAW_ARM, CLAW_ARM_DOWN)
            self._claw_arm_down = True

        # D-pad Left/Right -> Claw grip
        if self._hat_x < 0:
            self._servos.set_angle(SERVO_CLAW_GRIP, CLAW_GRIP_OPEN)
            self._claw_grip_closed = False
        elif self._hat_x > 0:
            self._servos.set_angle(SERVO_CLAW_GRIP, CLAW_GRIP_CLOSED)
            self._claw_grip_closed = True

    # ── Shutdown ────────────────────────────────────────────────────────

    def shutdown(self):
        """Clean shutdown."""
        self.stop()
