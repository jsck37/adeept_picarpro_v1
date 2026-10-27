"""Диспетчер команд (порт Server/commands.py).

Исправления:
* все числовые параметры парсятся безопасно (safe_int/safe_float) — раньше
  int(params) мог бросить ValueError и убить обработчик;
* неизвестная команда возвращает явную ошибку;
* новые команды: steady (стабилизация камеры из v2), watchDog/findColor
  (автономные режимы из v1/v2), battery (статус питания).
"""
from picarpro.logger import logger
from picarpro.camera.camera_opencv import (CV_NONE, CV_LINE, CV_HAND,
                                           CV_YOLO)
from config import (
    SERVO_COUNT, SERVO_STEERING, SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED,
    CRANE_GRIP_LOW, CRANE_GRIP_MID, CRANE_GRIP_HIGH,
    SWITCH_PINS, DEFAULT_SPEED, STEER_MAP,
)


def safe_int(value, default=0, lo=None, hi=None):
    """Безопасное целое: None/мусор -> default, с ограничением диапазона."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def process(state, data):
    if not isinstance(data, dict):
        return {'ok': False, 'error': 'object expected'}
    cmd = str(data.get('cmd', ''))
    p = data.get('params') or {}
    if not isinstance(p, dict):
        p = {}
    r = {'ok': False, 'cmd': cmd}

    if cmd == 'move':
        d = str(p.get('dir', 'stop'))
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
        state.speed = safe_int(p.get('value', DEFAULT_SPEED),
                               DEFAULT_SPEED, 0, 100)
        r = {'ok': True, 'speed': state.speed}

    elif cmd == 'servo':
        sid = safe_int(p.get('id', 0), 0, 0, SERVO_COUNT - 1)
        ang = safe_int(p.get('angle', 90), 90, 0, 190)
        if state.servos:
            ok = state.servos.set_angle(sid, ang)
            r = {'ok': ok, 'id': sid, 'angle': ang}

    elif cmd == 'servo_home':
        if state.servos:
            state.servos.move_init()
            state.crane_arm_closed = False
            state.crane_grip_position = 'high'
            r = {'ok': True}

    elif cmd == 'servo_get_limits':
        # только чтение: лимиты заданы в config.py (SERVO_LIMITS)
        r = {'ok': True, 'limits': state.servos.get_limits() if state.servos else {}}

    elif cmd == 'led':
        mode = str(p.get('mode', 'off'))
        color = p.get('color', [255, 0, 0]) or [255, 0, 0]
        if mode in ('off', 'solid', 'breath', 'flow', 'flowing', 'rainbow',
                    'police', 'colorWipe'):
            try:
                color = tuple(max(0, min(255, int(c))) for c in list(color)[:3])
                while len(color) < 3:
                    color += (0,)
            except Exception:
                color = (255, 0, 0)
            if state.leds:
                state.leds.set_mode(mode, color)
            state.led_mode = mode
            state.led_color = color
            r = {'ok': True, 'mode': mode}

    elif cmd == 'buzzer':
        key = {'beep': 'beep', 'birthday': 'happy_birthday',
               'alarm': 'alarm'}.get(str(p.get('melody', 'beep')))
        if key and state.buzzer:
            state.buzzer.play_melody(key)
            r = {'ok': True}

    elif cmd == 'buzzer_stop':
        if state.buzzer:
            state.buzzer.stop()
            r = {'ok': True}

    elif cmd == 'crane':
        act = str(p.get('action', ''))
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
            angle = safe_int(p.get('angle', 90), 90, 0, 190)
            state.servos.set_angle(SERVO_CRANE_GRIP, angle)
            state.crane_grip_position = 'custom'
            r = {'ok': True, 'angle': angle}

    elif cmd == 'switch':
        sid = safe_int(p.get('id', 0), 0)
        st = bool(p.get('state', False))
        mx = len(SWITCH_PINS) if (state.switches and state.switches._initialized) else 0
        if 0 <= sid < mx:
            (state.switches.on if st else state.switches.off)(sid)
            r = {'ok': True}

    elif cmd == 'headlight':
        action = str(p.get('action', 'toggle'))
        if state.switches and state.switches._initialized:
            if action == 'on':
                state.switches.headlight_on()
            elif action == 'off':
                state.switches.headlight_off()
            else:
                state.switches.headlight_toggle()
            r = {'ok': True, 'headlight': state.switches.headlight_state}

    elif cmd == 'blinker':
        side = str(p.get('side', ''))
        active = bool(p.get('active', True))
        if state.switches and state.switches._initialized:
            if side == 'left':
                state.left_blinker = active
                state.right_blinker = False
                state.switches.set_blinker('right', False)
                state.switches.set_blinker('left', active)
                r = {'ok': True, 'left': state.left_blinker,
                     'right': state.right_blinker}
            elif side == 'right':
                state.right_blinker = active
                state.left_blinker = False
                state.switches.set_blinker('left', False)
                state.switches.set_blinker('right', active)
                r = {'ok': True, 'left': state.left_blinker,
                     'right': state.right_blinker}
            elif side == 'both_off':
                state.left_blinker = False
                state.right_blinker = False
                state.switches.set_blinker('left', False)
                state.switches.set_blinker('right', False)
                r = {'ok': True, 'left': False, 'right': False}

    elif cmd == 'web_active':
        state.web_active = bool(p.get('active', True))
        r = {'ok': True, 'web_active': state.web_active}

    elif cmd == 'cv_mode':
        mode_map = {'none': CV_NONE, 'findlineCV': CV_LINE,
                    'trackHand': CV_HAND, 'yoloDetect': CV_YOLO}
        m = mode_map.get(str(p.get('mode', 'none')))
        if m is not None:
            state.init_camera()
            if state.camera:
                state.camera.set_cv_mode(m)
                r = {'ok': True, 'mode': p.get('mode')}

    elif cmd == 'yolo_status':
        r = {'ok': True}
        if state.camera:
            r.update(state.camera.get_yolo_status())
        else:
            r.update({'loaded': False, 'objects': []})

    # ------------------------------------------------------------------
    # Портированные режимы из hugo-Peltier/Adeept-picarPro
    # ------------------------------------------------------------------
    elif cmd == 'aruco_nav':
        # бортовая ArUco-навигация (порт Demo.py, камера Pi)
        action = str(p.get('action', 'status'))
        if action == 'status' and not state.aruco_nav:
            r = {'ok': True, 'active': False, 'finished': False}
        else:
            state.init_camera()
            if not state.aruco_nav and state.camera:
                from picarpro.functions.aruco_nav import ArucoNavigator
                state.aruco_nav = ArucoNavigator(state, state.camera)
            if not state.aruco_nav:
                r = {'ok': False, 'error': 'Камера недоступна'}
            elif action == 'start':
                ok, msg = state.aruco_nav.start()
                r = {'ok': ok, 'action': 'start'}
                if not ok:
                    r['error'] = msg
            elif action == 'stop':
                state.aruco_nav.stop()
                r = {'ok': True, 'action': 'stop'}
            else:
                r = state.aruco_nav.get_status()
                r['ok'] = True

    elif cmd == 'push':
        # расталкивание препятствий клешней (порт Phase 2)
        action = str(p.get('action', 'status'))
        if not state.pusher:
            from picarpro.functions.arm_push import ObstaclePusher
            state.pusher = ObstaclePusher(state)
        if action == 'start':
            ok, msg = state.pusher.start()
            r = {'ok': ok, 'action': 'start'}
            if not ok:
                r['error'] = msg
        elif action == 'stop':
            state.pusher.stop()
            r = {'ok': True, 'action': 'stop'}
        else:
            r = state.pusher.get_status()
            r['ok'] = True

    elif cmd == 'color_scan':
        # сканирование рукой + цветовой захват (порт colortracking.py)
        action = str(p.get('action', 'status'))
        if action == 'status' and not state.color_scan:
            r = {'ok': True, 'active': False, 'mode': 'IDLE'}
        else:
            state.init_camera()
            if not state.color_scan and state.camera:
                from picarpro.functions.color_scan import ColorArmScanner
                state.color_scan = ColorArmScanner(state, state.camera)
            if not state.color_scan:
                r = {'ok': False, 'error': 'Камера недоступна'}
            elif action == 'start':
                ok, msg = state.color_scan.start()
                r = {'ok': ok, 'action': 'start'}
                if not ok:
                    r['error'] = msg
            elif action == 'stop':
                state.color_scan.stop()
                r = {'ok': True, 'action': 'stop'}
            else:
                r = state.color_scan.get_status()
                r['ok'] = True

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
                c = (safe_int(p.get('h_low', 0), 0, 0, 179),
                     safe_int(p.get('s_low', 30), 30, 0, 255),
                     safe_int(p.get('v_low', 50), 50, 0, 255),
                     safe_int(p.get('h_high', 25), 25, 0, 179),
                     safe_int(p.get('s_high', 255), 255, 0, 255),
                     safe_int(p.get('v_high', 255), 255, 0, 255))
            state.camera.set_hand_color(*c)
            r = {'ok': True, 'color': list(c)}

    elif cmd == 'i2c_scan':
        from picarpro.hardware.mpu6050 import i2c_scan, find_mpu6050_on_bus
        devs = i2c_scan()
        addr, who = find_mpu6050_on_bus()
        r = {'ok': True,
             'devices': [f'0x{a:02X}' for a in devs],
             'mpu6050_found': addr is not None,
             'mpu6050_addr': f'0x{addr:02X}' if addr else None,
             'mpu6050_who_am_i': f'0x{who:02X}' if who else None}

    elif cmd == 'auto':
        func = str(p.get('func', 'stop'))
        valid = ('radarScan', 'automatic', 'trackLine', 'trackLineCV',
                 'trackHand', 'findColor', 'watchDog', 'keepDistance', 'stop')
        if func not in valid:
            r['error'] = f'Unknown mode: {func}'
        else:
            if func in ('trackLineCV', 'trackHand', 'findColor', 'watchDog'):
                state.init_camera()
                if state.camera:
                    state.autonomous.set_camera(state.camera)
            if func == 'stop':
                state.autonomous.stop()
                r = {'ok': True, 'func': func}
            else:
                ok, msg = state.autonomous.start(func)
                r = {'ok': ok, 'func': func}
                if not ok:
                    r['error'] = msg

    elif cmd == 'steady':
        action = str(p.get('action', 'toggle'))
        if state.steady:
            if action == 'on':
                ok, msg = state.steady.start()
                r = {'ok': ok, 'steady': True}
                if not ok:
                    r['error'] = msg
            elif action == 'off':
                state.steady.stop()
                r = {'ok': True, 'steady': False}
            else:
                if state.steady.active:
                    state.steady.stop()
                    r = {'ok': True, 'steady': False}
                else:
                    ok, msg = state.steady.start()
                    r = {'ok': ok, 'steady': ok}
                    if not ok:
                        r['error'] = msg

    elif cmd == 'battery':
        r = {'ok': True,
             'battery': state.voltage.get_status() if state.voltage else None}

    elif cmd == 'ds4_status':
        r = {'ok': True}
        r.update(state.ds4.get_status() if state.ds4 else {'connected': False})

    elif cmd == 'bt_scan':
        from picarpro.routes.bluetooth import scan_devices
        r = {'ok': True, 'devices': scan_devices()}

    elif cmd == 'bt_connect':
        from picarpro.routes.bluetooth import pair_and_connect
        mac = str(p.get('mac', '')).strip()
        if mac:
            ok, msg = pair_and_connect(mac, state.ds4)
            r = {'ok': ok, 'message': msg}
        else:
            r['error'] = 'MAC required'

    elif cmd == 'bt_disconnect':
        from picarpro.routes.bluetooth import disconnect_device
        disconnect_device(str(p.get('mac', '')).strip())
        r = {'ok': True}

    elif cmd == 'voice':
        action = str(p.get('action', 'stop'))
        if state.voice:
            if action == 'start':
                state.voice.start()
                r = {'ok': True, 'action': 'start'}
            elif action == 'stop':
                state.voice.stop()
                r = {'ok': True, 'action': 'stop'}

    elif cmd == 'get_log':
        after = 0.0
        try:
            after = float(p.get('after_ts', 0.0))
        except (TypeError, ValueError):
            after = 0.0
        lines = log_buffer_lines(after)
        r = {'ok': True, 'lines': [[ts, txt] for ts, txt in lines]}

    elif cmd == 'clear_log':
        from picarpro.logger import log_buffer
        log_buffer.clear()
        r = {'ok': True}

    else:
        r['error'] = f'Unknown command: {cmd}'

    return r


def log_buffer_lines(after_ts):
    from picarpro.logger import log_buffer
    return log_buffer.get_lines_since(after_ts=after_ts, max_lines=500)
