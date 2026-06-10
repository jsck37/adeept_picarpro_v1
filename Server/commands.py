from config import (
    SERVO_COUNT, SERVO_STEERING, SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED,
    CRANE_GRIP_LOW, CRANE_GRIP_MID, CRANE_GRIP_HIGH,
    SWITCH_PINS, DEFAULT_SPEED, STEER_MAP,
)
from Server.camera.camera_opencv import CV_NONE, CV_LINE, CV_HAND
from Server.state import load_servo_cal, save_servo_cal
from Server.utils.log_buffer import log_buffer


def process_command(state, data):
    cmd = data.get('cmd', '')
    p = data.get('params', {})
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
        state.servos.set_angle(SERVO_STEERING, STEER_MAP.get(d, 90))
        r = {'ok': True, 'cmd': cmd, 'dir': d, 'steer': STEER_MAP.get(d, 90)}

    elif cmd == 'speed':
        try:
            state.speed = max(0, min(100, int(p.get('value', DEFAULT_SPEED))))
            r = {'ok': True, 'speed': state.speed}
        except Exception:
            r['error'] = 'Invalid speed'

    elif cmd == 'servo':
        sid, ang = int(p.get('id', 0)), int(p.get('angle', 90))
        if 0 <= sid < SERVO_COUNT:
            state.servos.set_angle(sid, max(0, min(180, ang)))
            r = {'ok': True, 'id': sid, 'angle': ang}

    elif cmd == 'servo_calibrate':
        sid, ang = int(p.get('id', 0)), int(p.get('angle', 90))
        if 0 <= sid < SERVO_COUNT:
            ang = max(0, min(180, ang))
            state.servos.set_init_angle(sid, ang)
            cal = load_servo_cal()
            cal[sid] = ang
            save_servo_cal(cal)
            r = {'ok': True, 'id': sid, 'init_angle': ang}

    elif cmd == 'servo_home':
        state.servos.move_init()
        r = {'ok': True}

    elif cmd == 'led':
        mode = p.get('mode', 'off')
        color = p.get('color', [255, 0, 0])
        if mode in ('off', 'solid', 'breath', 'flow', 'rainbow', 'police', 'colorWipe'):
            try:
                color = tuple(max(0, min(255, int(c))) for c in color[:3])
            except Exception:
                color = (255, 0, 0)
            state.leds.set_mode(mode, color)
            r = {'ok': True, 'mode': mode}

    elif cmd == 'buzzer':
        key = {'beep': 'beep', 'birthday': 'happy_birthday'}.get(
            p.get('melody', 'beep'))
        if key:
            state.buzzer.play_melody(key)
            r = {'ok': True}

    elif cmd == 'buzzer_stop':
        state.buzzer.stop()
        r = {'ok': True}

    elif cmd == 'crane':
        act = p.get('action', '')
        actions = {
            'arm_open': (SERVO_CRANE_ARM, CRANE_ARM_OPEN),
            'arm_close': (SERVO_CRANE_ARM, CRANE_ARM_CLOSED),
            'grip_low': (SERVO_CRANE_GRIP, CRANE_GRIP_LOW),
            'grip_mid': (SERVO_CRANE_GRIP, CRANE_GRIP_MID),
            'grip_high': (SERVO_CRANE_GRIP, CRANE_GRIP_HIGH),
        }
        if act in actions:
            state.servos.set_angle(*actions[act])
            r = {'ok': True, 'action': act}

    elif cmd == 'switch':
        sid, st = int(p.get('id', 0)), p.get('state', False)
        mx = len(SWITCH_PINS) if state.switches._initialized else 0
        if 0 <= sid < mx:
            (state.switches.on if st else state.switches.off)(sid)
            r = {'ok': True}

    elif cmd == 'cv_mode':
        mode_map = {
            'none': CV_NONE,
            'findlineCV': CV_LINE,
            'trackHand': CV_HAND,
        }
        cv = mode_map.get(p.get('mode', 'none'))
        if cv is not None:
            state.init_camera()
            state.camera.set_cv_mode(cv)
            r = {'ok': True, 'mode': p.get('mode')}

    elif cmd == 'i2c_scan':
        from Server.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
        devs = i2c_scan()
        addr, who = find_mpu6050_on_bus()
        r = {
            'ok': True,
            'devices': [f'0x{a:02X}' for a in devs],
            'mpu6050_found': addr is not None,
            'mpu6050_addr': f'0x{addr:02X}' if addr else None,
            'mpu6050_who_am_i': f'0x{who:02X}' if who else None,
        }

    elif cmd == 'auto':
        func = p.get('func', 'stop')
        valid_funcs = ('radarScan', 'automatic', 'trackLine',
                       'trackLineCV', 'trackHand', 'keepDistance', 'stop')
        if func in valid_funcs:
            if func == 'trackLineCV':
                state.init_camera()
                if state.camera:
                    state.autonomous.set_camera(state.camera)
            if func == 'trackHand':
                state.init_camera()
                if state.camera:
                    state.autonomous.set_camera(state.camera)
            if func == 'stop':
                state.autonomous.stop()
            else:
                state.autonomous.start(func)
            r = {'ok': True, 'func': func}
        else:
            r['error'] = f'Unknown auto function: {func}'

    elif cmd == 'get_info':
        r = {'ok': True}
        r.update(state.get_status())

    elif cmd == 'ds4_status':
        r = {'ok': True}
        r.update(state.ds4.get_status() if state.ds4 else {'enabled': False, 'connected': False})

    elif cmd == 'get_log':
        after = p.get('after_ts', 0.0)
        lines = log_buffer.get_lines_since(after_ts=after, max_lines=500)
        r = {'ok': True, 'lines': [[ts, txt] for ts, txt in lines]}

    elif cmd == 'clear_log':
        with log_buffer._lock:
            log_buffer._lines.clear()
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
            else:
                r['error'] = f'Unknown voice action: {action}'
        else:
            r['error'] = 'Voice control not available'

    return r
