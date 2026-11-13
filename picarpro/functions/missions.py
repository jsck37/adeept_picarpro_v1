"""Миссии: JSON-сценарии движения, запись пути и «вернуться домой».

Шаги (op): drive, turn, wait, servo, auto, repeat — см. BUILTIN.
v1.6.5: подсветка и буззер убраны из миссий — шаги led/beep из старых
сохранённых сценариев валидатор молча пропускает, исполнитель их не знает.
Все значения проходят whitelist-валидацию и ограничиваются (время шага,
число шагов, общая длительность), сценарий из веба не может ни зациклить
робота навсегда, ни вызвать произвольный код.

Запись пути и «домой» — счисление пути по командам (направление × время),
без одометрии: на гладком полу погрешность единицы-десятки сантиметров,
на ковре и с севшей батареей — больше. Это «примерно туда, откуда выехали».
"""
import json
import os
import re
import threading
import time

from picarpro.logger import logger
from picarpro.drive import drive, parse_direction, arc_turn
from picarpro.utils.heading import HeadingTracker
from config import (
    MISSIONS_DIR, MISSION_MAX_STEP_S, MISSION_MAX_TOTAL_S, MISSION_MAX_STEPS,
    MISSION_ABORT_ON_OBSTACLE, MISSION_RECORD_MIN_SEGMENT_S,
    AUTO_OBSTACLE_STOP_CM, AUTO_ARC_RATE_DPS,
    SERVO_COUNT,
)

NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
SPIN_DIRS = ('spin_left', 'spin_right', 'stop')
# v1.6.2: A/D-руление без движения — записанный маршрут, в котором водитель
# крутил руль (steer_left/steer_right), раньше ОТКЛОНЯЛСЯ валидатором
# («bad dir: steer_left») и НЕ СОХРАНЯЛСЯ. Теперь такие шаги легальны
# (drive() умеет их исполнять — руль без хода), а из «вернуться домой»
# они выкидываются: руление на месте не смещает робота.
STEER_DIRS = ('steer_left', 'steer_right')
MAX_DEPTH = 3

BUILTIN = {
    'square': [
        {'op': 'repeat', 'times': 4, 'steps': [
            {'op': 'drive', 'dir': 'forward', 'speed': 40, 'sec': 1.5},
            {'op': 'turn', 'deg': 90},
        ]},
    ],
    'zigzag': [
        {'op': 'repeat', 'times': 3, 'steps': [
            {'op': 'drive', 'dir': 'forward_left', 'speed': 40, 'sec': 1.2},
            {'op': 'drive', 'dir': 'forward_right', 'speed': 40, 'sec': 1.2},
        ]},
    ],
    # v1.6.2: бывший spin_show. Аккерман-шасси не умеет крутиться на
    # месте — показ заменили на двойной разворот дугой.
    # v1.6.5: без подсветки и буззера — миссия только двигает шасси.
    'turn_show': [
        {'op': 'turn', 'deg': 180},
        {'op': 'wait', 'sec': 0.3},
        {'op': 'turn', 'deg': -180},
    ],
    'guard_1min': [
        {'op': 'auto', 'mode': 'watchDog', 'sec': 60},
    ],
}


class MissionError(ValueError):
    pass


class _Abort(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _num(v, lo, hi, default=None, name='value'):
    if v is None:
        if default is None:
            raise MissionError(f'{name} required')
        return default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise MissionError(f'{name} must be a number')
    if v != v or v in (float('inf'), float('-inf')):
        raise MissionError(f'{name} out of range')
    return max(lo, min(hi, v))


def validate_steps(steps, depth=0):
    """Нормализованная копия сценария или MissionError."""
    if not isinstance(steps, list) or not steps:
        raise MissionError('steps must be a non-empty list')
    if depth > MAX_DEPTH:
        raise MissionError('repeat nested too deep')
    out = []
    for st in steps:
        if not isinstance(st, dict):
            raise MissionError('step must be an object')
        op = st.get('op')
        if op == 'drive':
            d = str(st.get('dir', ''))
            if not (parse_direction(d) or d in SPIN_DIRS or d in STEER_DIRS):
                raise MissionError(f'bad dir: {d[:20]}')
            out.append({'op': 'drive', 'dir': d,
                        'speed': int(_num(st.get('speed'), 10, 100, 40, 'speed')),
                        'sec': _num(st.get('sec'), 0.05, MISSION_MAX_STEP_S, 1.0, 'sec')})
        elif op == 'turn':
            out.append({'op': 'turn', 'deg': _num(st.get('deg'), -360, 360, None, 'deg')})
        elif op == 'wait':
            out.append({'op': 'wait', 'sec': _num(st.get('sec'), 0.0, MISSION_MAX_STEP_S, 1.0, 'sec')})
        elif op == 'servo':
            sid = int(_num(st.get('id'), 0, SERVO_COUNT - 1, None, 'id'))
            out.append({'op': 'servo', 'id': sid,
                        'angle': int(_num(st.get('angle'), 0, 180, None, 'angle'))})
        elif op in ('beep', 'led'):
            # v1.6.5: подсветка и буззер убраны из миссий. Шаги из старых
            # сохранённых сценариев пропускаются без ошибки валидации.
            continue
        elif op == 'auto':
            from picarpro.functions.autonomous import ALL_MODES
            mode = str(st.get('mode', ''))
            if mode not in ALL_MODES:
                raise MissionError(f'bad auto mode: {mode[:20]}')
            out.append({'op': 'auto', 'mode': mode,
                        'sec': _num(st.get('sec'), 1.0, MISSION_MAX_STEP_S, 10.0, 'sec')})
        elif op == 'repeat':
            out.append({'op': 'repeat',
                        'times': int(_num(st.get('times'), 1, 50, None, 'times')),
                        'steps': validate_steps(st.get('steps'), depth + 1)})
        else:
            raise MissionError(f'unknown op: {str(op)[:20]}')
    return out


def count_steps(steps):
    """Число шагов после раскрытия repeat."""
    n = 0
    for st in steps:
        n += (st['times'] * count_steps(st['steps'])) if st['op'] == 'repeat' else 1
    return n


def estimate_seconds(steps):
    t = 0.0
    for st in steps:
        if st['op'] == 'repeat':
            t += st['times'] * estimate_seconds(st['steps'])
        elif st['op'] == 'turn':
            t += abs(st['deg']) / AUTO_ARC_RATE_DPS + 0.3
        else:
            t += st.get('sec', 0.0)
    return t


def prepare(steps):
    steps = validate_steps(steps)
    if count_steps(steps) > MISSION_MAX_STEPS:
        raise MissionError(f'too many steps (max {MISSION_MAX_STEPS})')
    if estimate_seconds(steps) > MISSION_MAX_TOTAL_S:
        raise MissionError('mission too long')
    return steps


def reverse_dir(d):
    """Направление, отменяющее d (разворот и «дуга назад» с тем же бортом)."""
    if d == 'spin_left':
        return 'spin_right'
    if d == 'spin_right':
        return 'spin_left'
    if d in STEER_DIRS:
        # руление на месте робота не смещает; для «домой» такие шаги
        # отфильтровываются в return_home_steps, тут — формально
        return d
    parsed = parse_direction(d)
    if not parsed:
        return 'stop'
    head, turn = parsed
    head = 'backward' if head == 'forward' else 'forward'
    return head if turn == 'no' else f'{head}_{turn}'


class MissionRunner:
    def __init__(self, state, directory=None):
        self.state = state
        self.dir = str(directory or MISSIONS_DIR)
        self._lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()
        self._active = False
        self._name = ''
        self._status = 'idle'     # idle|running|done|stopped|obstacle|error
        self._error = ''
        self._done = 0
        self._total = 0
        self._recording = False
        self._events = []         # (t, dir, speed)
        self._last_path = None    # список drive-шагов последней записи
        self._heading = None
        self._gyro_ready = False

    # ---- хранение -------------------------------------------------------
    def list(self):
        items = [{'name': n, 'builtin': True, 'steps': count_steps(prepare(s)),
                  'sec': round(estimate_seconds(s), 1)} for n, s in BUILTIN.items()]
        try:
            for fn in sorted(os.listdir(self.dir)):
                if fn.endswith('.json') and NAME_RE.match(fn[:-5]):
                    try:
                        steps = self._load_file(fn[:-5])
                        items.append({'name': fn[:-5], 'builtin': False,
                                      'steps': count_steps(steps),
                                      'sec': round(estimate_seconds(steps), 1)})
                    except Exception:
                        pass
        except FileNotFoundError:
            pass
        return items

    def _path(self, name):
        if not NAME_RE.match(name or ''):
            raise MissionError('bad name (A-Z a-z 0-9 _ -, up to 32)')
        return os.path.join(self.dir, name + '.json')

    def _load_file(self, name):
        with open(self._path(name), encoding='utf-8') as f:
            return prepare(json.load(f)['steps'])

    def load(self, name):
        if name in BUILTIN:
            return prepare(BUILTIN[name])
        try:
            return self._load_file(name)
        except FileNotFoundError:
            raise MissionError('mission not found')

    def save(self, name, steps):
        if name in BUILTIN:
            raise MissionError('built-in mission cannot be overwritten')
        steps = prepare(steps)
        os.makedirs(self.dir, exist_ok=True)
        path = self._path(name)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'name': name, 'created': int(time.time()), 'steps': steps},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return steps

    def delete(self, name):
        if name in BUILTIN:
            raise MissionError('built-in mission cannot be deleted')
        try:
            os.remove(self._path(name))
        except FileNotFoundError:
            raise MissionError('mission not found')

    # ---- запись пути -----------------------------------------------------------
    def note_move(self, d, speed):
        """Вызывается из командного слоя при каждой ручной команде движения."""
        if not self._recording:
            return
        with self._lock:
            if self._events and self._events[-1][1] == d:
                return
            self._events.append((time.monotonic(), d, int(speed)))

    def record_start(self):
        with self._lock:
            if self._active:
                raise MissionError('mission is running')
            self._recording = True
            self._events = []

    def record_stop(self):
        """Завершить запись. Возвращает шаги drive (может быть пусто)."""
        with self._lock:
            if not self._recording:
                raise MissionError('not recording')
            self._recording = False
            ev = list(self._events)
            if ev:
                ev.append((time.monotonic(), 'stop', 0))
            steps = []
            for (t0, d, sp), (t1, _, _) in zip(ev, ev[1:]):
                dur = t1 - t0
                if d == 'stop' or dur < MISSION_RECORD_MIN_SEGMENT_S:
                    continue
                steps.append({'op': 'drive', 'dir': d, 'speed': max(10, min(100, sp)),
                              'sec': round(min(dur, MISSION_MAX_STEP_S), 2)})
            self._last_path = steps or None
            return steps

    def return_home_steps(self):
        if not self._last_path:
            raise MissionError('no recorded path (use Record first)')
        # v1.6.2: steer_* шаги (руление на месте) не смещают робота —
        # для обратного пути они бесполезны, выбрасываем
        return [{'op': 'drive', 'dir': reverse_dir(s['dir']), 'speed': s['speed'],
                 'sec': s['sec']} for s in reversed(self._last_path)
                if s['dir'] not in STEER_DIRS]

    # ---- выполнение ---------------------------------------------------------------
    def is_active(self):
        return self._active

    @property
    def recording(self):
        return self._recording

    def get_status(self):
        return {'active': self._active, 'name': self._name, 'state': self._status,
                'done': self._done, 'total': self._total, 'error': self._error,
                'recording': self._recording, 'rec_events': len(self._events),
                'has_path': bool(self._last_path)}

    def start(self, name='', steps=None):
        steps = prepare(steps) if steps is not None else self.load(name)
        if self._recording:
            raise MissionError('stop recording first')
        st = self.state
        if not st.motors:
            raise MissionError('motors not available')
        st.stop_autonomy()                 # в т.ч. прежняя миссия
        with self._lock:
            self._stop = threading.Event()
            self._active = True
            self._name = name or 'custom'
            self._status = 'running'
            self._error = ''
            self._done = 0
            self._total = count_steps(steps)
            self._gyro_ready = False
            self._heading = HeadingTracker(st.mpu6050)
            self._thread = threading.Thread(
                target=self._run, args=(steps, self._stop), daemon=True)
            self._thread.start()
        logger.info(f'[Mission] started: {self._name} ({self._total} steps)')

    def stop(self):
        with self._lock:
            was = self._active
            self._stop.set()
        if was:
            self._halt()
        return was

    def _halt(self):
        st = self.state
        try:
            if st.motors:
                st.motors.stop()
            if st.autonomous is not None and st.autonomous.is_active():
                st.autonomous.stop()
        except Exception:
            pass

    def _run(self, steps, stop):
        deadline = time.monotonic() + MISSION_MAX_TOTAL_S
        result, error = 'done', ''
        try:
            self._exec(steps, stop, deadline)
            if stop.is_set():
                result = 'stopped'
        except _Abort as a:
            result = a.reason
        except Exception as e:
            result, error = 'error', str(e)[:120]
            logger.error(f'[Mission] error: {e}')
        finally:
            self._halt()
            with self._lock:
                if self._stop is stop:
                    self._active = False
                    self._status = result
                    self._error = error
            if self._heading:
                self._heading.end()
            logger.info(f'[Mission] finished: {result}')

    def _exec(self, steps, stop, deadline):
        for st in steps:
            if stop.is_set():
                return
            if time.monotonic() > deadline:
                raise _Abort('error')
            if st['op'] == 'repeat':
                for _ in range(st['times']):
                    if stop.is_set():
                        return
                    self._exec(st['steps'], stop, deadline)
                continue
            self._step(st, stop)
            self._done += 1

    def _obstacle_ahead(self, d):
        if not MISSION_ABORT_ON_OBSTACLE:
            return False
        head = (parse_direction(d) or ('', ''))[0]
        if head != 'forward':
            return False
        u = self.state.ultrasonic
        dist = u.get_distance_cm() if (u and getattr(u, '_initialized', False)) else None
        return dist is not None and dist <= AUTO_OBSTACLE_STOP_CM

    def _step(self, st, stop):
        op, s = st['op'], self.state
        if op == 'drive':
            u = s.ultrasonic
            head = (parse_direction(st['dir']) or ('', ''))[0]
            use_us = u is not None and getattr(u, '_initialized', False) and head == 'forward'
            if use_us:
                u.acquire()
            try:
                drive(s, st['dir'], st['speed'])
                end = time.monotonic() + st['sec']
                while not stop.is_set():
                    if self._obstacle_ahead(st['dir']):
                        self._halt()
                        raise _Abort('obstacle')
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    stop.wait(min(0.05, left))
            finally:
                if use_us:
                    u.release()
                drive(s, 'stop')
        elif op == 'turn':
            self._turn(st['deg'], stop)
        elif op == 'wait':
            stop.wait(st['sec'])
        elif op == 'servo':
            if s.servos:
                s.servos.set_angle(st['id'], st['angle'])
        elif op == 'auto':
            a = s.autonomous
            if a is None:
                raise MissionError('autonomous controller not available')
            if st['mode'] in ('trackLineCV', 'trackHand', 'findColor', 'watchDog'):
                if not s.camera:
                    s.init_camera()
                if s.camera:
                    a.set_camera(s.camera)
            ok, msg = a.start(st['mode'])
            if not ok:
                raise MissionError(msg)
            stop.wait(st['sec'])
            a.stop()

    def _turn(self, deg, stop):
        """Разворот на deg (>0 вправо): дугой (Аккерман), по гироскопу,
        иначе по времени. Раньше был spin на месте — на этом шасси машина
        не вращалась (v1.6.2)."""
        s = self.state
        hd = self._heading
        heading = None
        if hd is not None and hd.available:
            if not self._gyro_ready:
                self._gyro_ready = hd.calibrate(stop.wait)
            if self._gyro_ready:
                heading = hd
        arc_turn(s.motors, s.servos, deg,
                 alive=lambda: not stop.is_set(), wait=stop.wait,
                 heading=heading)

    # ---- единая точка для команды `mission` -----------------------------------------
    def handle(self, action, p):
        try:
            if action == 'list':
                return {'ok': True, 'missions': self.list(), **self.get_status()}
            if action == 'status':
                return {'ok': True, **self.get_status()}
            if action == 'start':
                self.start(name=str(p.get('name', '')), steps=p.get('steps'))
            elif action == 'stop':
                self.stop()
            elif action == 'save':
                self.save(str(p.get('name', '')), p.get('steps'))
            elif action == 'delete':
                self.delete(str(p.get('name', '')))
            elif action == 'record_start':
                self.record_start()
            elif action == 'record_stop':
                steps = self.record_stop()
                name = str(p.get('save_as') or '')
                if name and steps:
                    self.save(name, steps)
                return {'ok': True, 'steps': len(steps), 'saved': bool(name and steps),
                        **self.get_status()}
            elif action == 'return_home':
                self.start(name='return_home', steps=self.return_home_steps())
            else:
                return {'ok': False, 'error': f'unknown action: {action[:20]}'}
        except MissionError as e:
            return {'ok': False, 'error': str(e)}
        return {'ok': True, 'action': action, **self.get_status()}
