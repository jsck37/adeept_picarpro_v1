from Server.logger import logger
from Server.camera.camera_opencv import CV_NONE, CV_LINE, CV_HAND
from config import (
    SERVO_COUNT, SERVO_STEERING, SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED,
    CRANE_GRIP_LOW, CRANE_GRIP_MID, CRANE_GRIP_HIGH,
    SWITCH_PINS, DEFAULT_SPEED, STEER_MAP,
)


def process(state, data):
    cmd = data.get('cmd', '')
    p = data.get('params', {}) or {}
    r = {'ok': False, 'cmd': cmd}

    if cmd == 'move':
        d = p.get('dir', 'stop')
        if d == 'forward':
            state.motors.move(state.speed, 'forward', 'no', 0.5)
        elif d == 'backward':
            state.motors.move(state.speed, 'backward', 'no', 0.5)
        elif d in ('left', 'right'):
            state.motors.move(state.speed, 'forward', d, 0.3)
        elif d.startswith('forward_'):
            state.motors.move(state.speed, 'forward', d.split('_')[1], 0.3)
        elif d.startswith('backward_'):
            state.motors.move(state.speed, 'backward', d.split('_')[1], 0.3)
        elif d == 'stop':
            state.motors.stop()
        if state.servos:
            state.servos.set_angle(SERVO_STEERING, STEER_MAP.get(d, 90))
        r = {'ok': True, 'dir': d}

    elif cmd == 'speed':
        try:
            state.speed = max(0, min(100, int(p.get('value', DEFAULT_SPEED))))
            r = {'ok': True, 'speed': state.speed}
        except Exception:
            r['error'] = 'Invalid speed'

    elif cmd == 'servo':
        sid, ang = int(p.get('id', 0)), int(p.get('angle', 90))
        if 0 <= sid < SERVO_COUNT and state.servos:
            ok = state.servos.set_angle(sid, ang)
            r = {'ok': ok, 'id': sid, 'angle': ang}

    elif cmd == 'servo_home':
        if state.servos:
            state.servos.move_init()
            state.crane_arm_closed = False
            state.crane_grip_position = 'high'
            r = {'ok': True}

    elif cmd == 'servo_get_limits':
        r = {'ok': True, 'limits': state.servos.get_limits() if state.servos else {}}

    elif cmd == 'servo_set_limits':
        sid = int(p.get('id', 0))
        mn = int(p.get('min', 0))
        mx = int(p.get('max', 180))
        if 0 <= sid < SERVO_COUNT and state.servos:
            ok = state.servos.set_limits(sid, mn, mx)
            r = {'ok': ok, 'id': sid, 'min': mn, 'max': mx}

    elif cmd == 'led':
        mode = p.get('mode', 'off')
        color = p.get('color', [255, 0, 0])
        if mode in ('off', 'solid', 'breath', 'flow', 'rainbow', 'police', 'colorWipe'):
            try:
                color = tuple(max(0, min(255, int(c))) for c in color[:3])
            except Exception:
                color = (255, 0, 0)
            if state.leds:
                state.leds.set_mode(mode, color)
            state.led_mode = mode
            state.led_color = color
            r = {'ok': True, 'mode': mode}

    elif cmd == 'buzzer':
        key = {'beep': 'beep', 'birthday': 'happy_birthday'}.get(p.get('melody', 'beep'))
        if key and state.buzzer:
            state.buzzer.play_melody(key)
            r = {'ok': True}

    elif cmd == 'buzzer_stop':
        if state.buzzer:
            state.buzzer.stop()
            r = {'ok': True}

    elif cmd == 'crane':
        act = p.get('action', '')
        actions = {
            'arm_open': (SERVO_CRANE_ARM, CRANE_ARM_OPEN, False, None),
            'arm_close': (SERVO_CRANE_ARM, CRANE_ARM_CLOSED, True, None),
            'grip_low': (SERVO_CRANE_GRIP, CRANE_GRIP_LOW, None, 'low'),
            'grip_mid': (SERVO_CRANE_GRIP, CRANE_GRIP_MID, None, 'mid'),
            'grip_high': (SERVO_CRANE_GRIP, CRANE_GRIP_HIGH, None, 'high'),
        }
        if act == 'arm_toggle' and state.servos:
            new_closed = not state.crane_arm_closed
            angle = CRANE_ARM_CLOSED if new_closed else CRANE_ARM_OPEN
            state.servos.set_angle(SERVO_CRANE_ARM, angle)
            state.crane_arm_closed = new_closed
            r = {'ok': True, 'action': 'arm_toggle', 'closed': new_closed}
        elif act in actions and state.servos:
            sid, angle, arm_state, grip_state = actions[act]
            state.servos.set_angle(sid, angle)
            if arm_state is not None:
                state.crane_arm_closed = arm_state
            if grip_state is not None:
                state.crane_grip_position = grip_state
            r = {'ok': True, 'action': act}
        elif act == 'grip_angle' and state.servos:
            try:
                angle = int(p.get('angle', 90))
            except Exception:
                angle = 90
            state.servos.set_angle(SERVO_CRANE_GRIP, angle)
            state.crane_grip_position = 'custom'
            r = {'ok': True, 'angle': angle}

    elif cmd == 'switch':
        sid, st = int(p.get('id', 0)), p.get('state', False)
        mx = len(SWITCH_PINS) if (state.switches and state.switches._initialized) else 0
        if 0 <= sid < mx:
            (state.switches.on if st else state.switches.off)(sid)
            r = {'ok': True}

    elif cmd == 'headlight':
        action = p.get('action', 'toggle')
        if state.switches and state.switches._initialized:
            if action == 'on':
                state.switches.headlight_on()
            elif action == 'off':
                state.switches.headlight_off()
            else:
                state.switches.headlight_toggle()
            r = {'ok': True, 'headlight': state.switches.headlight_state}

    elif cmd == 'blinker':
        side = p.get('side', '')
        active = p.get('active', True)
        if state.switches and state.switches._initialized:
            if side == 'left':
                state.left_blinker = active
                state.right_blinker = False
                state.switches.set_blinker('right', False)
                state.switches.set_blinker('left', active)
                r = {'ok': True, 'left': state.left_blinker, 'right': state.right_blinker}
            elif side == 'right':
                state.right_blinker = active
                state.left_blinker = False
                state.switches.set_blinker('left', False)
                state.switches.set_blinker('right', active)
                r = {'ok': True, 'left': state.left_blinker, 'right': state.right_blinker}
            elif side == 'both_off':
                state.left_blinker = False
                state.right_blinker = False
                state.switches.set_blinker('left', False)
                state.switches.set_blinker('right', False)
                r = {'ok': True, 'left': False, 'right': False}

    elif cmd == 'web_active':
        state.web_active = p.get('active', True)
        r = {'ok': True, 'web_active': state.web_active}

    elif cmd == 'cv_mode':
        mode_map = {'none': CV_NONE, 'findlineCV': CV_LINE, 'trackHand': CV_HAND}
        m = mode_map.get(p.get('mode', 'none'))
        if m is not None:
            state.init_camera()
            if state.camera:
                state.camera.set_cv_mode(m)
                r = {'ok': True, 'mode': p.get('mode')}

    elif cmd == 'hand_color':
        state.init_camera()
        if not state.camera:
            r['error'] = 'Camera not available'
        else:
            presets = {
                'skin': (0, 30, 50, 25, 255, 255),
                'red': (0, 100, 80, 10, 255, 255),
                'green': (35, 80, 50, 85, 255, 255),
                'blue': (95, 80, 50, 135, 255, 255),
                'yellow': (20, 80, 80, 35, 255, 255),
            }
            preset = p.get('preset')
            if preset and preset in presets:
                c = presets[preset]
            else:
                try:
                    c = (int(p.get('h_low', 0)), int(p.get('s_low', 30)),
                         int(p.get('v_low', 50)), int(p.get('h_high', 25)),
                         int(p.get('s_high', 255)), int(p.get('v_high', 255)))
                except Exception:
                    c = presets['skin']
            state.camera.set_hand_color(*c)
            r = {'ok': True, 'color': list(c)}

    elif cmd == 'i2c_scan':
        from Server.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
        devs = i2c_scan()
        addr, who = find_mpu6050_on_bus()
        r = {'ok': True,
             'devices': [f'0x{a:02X}' for a in devs],
             'mpu6050_found': addr is not None,
             'mpu6050_addr': f'0x{addr:02X}' if addr else None,
             'mpu6050_who_am_i': f'0x{who:02X}' if who else None}

    elif cmd == 'auto':
        func = p.get('func', 'stop')
        valid = ('radarScan', 'automatic', 'trackLine', 'trackLineCV',
                 'trackHand', 'keepDistance', 'stop')
        if func in valid:
            if func in ('trackLineCV', 'trackHand'):
                state.init_camera()
                if state.camera:
                    state.autonomous.set_camera(state.camera)
            if func == 'stop':
                state.autonomous.stop()
            else:
                state.autonomous.start(func)
            r = {'ok': True, 'func': func}

    elif cmd == 'ds4_status':
        r = {'ok': True}
        r.update(state.ds4.get_status() if state.ds4 else {'connected': False})

    elif cmd == 'bt_scan':
        from Server.routes.bluetooth_routes import scan_devices
        r = {'ok': True, 'devices': scan_devices()}

    elif cmd == 'bt_connect':
        from Server.routes.bluetooth_routes import pair_and_connect
        mac = p.get('mac', '').strip()
        if mac:
            ok, msg = pair_and_connect(mac, state.ds4)
            r = {'ok': ok, 'message': msg}
        else:
            r['error'] = 'MAC required'

    elif cmd == 'bt_disconnect':
        from Server.routes.bluetooth_routes import disconnect_device
        mac = p.get('mac', '').strip()
        disconnect_device(mac)
        r = {'ok': True}

    elif cmd == 'voice':
        action = p.get('action', 'stop')
        if state.voice:
            if action == 'start':
                state.voice.start()
                r = {'ok': True, 'action': 'start'}
            elif action == 'stop':
                state.voice.stop()
                r = {'ok': True, 'action': 'stop'}

    elif cmd == 'get_log':
        from Server.logger import log_buffer
        after = p.get('after_ts', 0.0)
        lines = log_buffer.get_lines_since(after_ts=after, max_lines=500)
        r = {'ok': True, 'lines': [[ts, txt] for ts, txt in lines]}

    elif cmd == 'clear_log':
        from Server.logger import log_buffer
        log_buffer.clear()
        r = {'ok': True}

    return r
