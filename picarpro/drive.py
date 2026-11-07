"""Единая точка «команда направления -> моторы + рулевое серво».

Раньше один и тот же разбор направлений ('forward', 'left', 'forward_left',
'stop', ...) был скопирован в commands.py (веб), routes/nav_server.py (TCP) и
functions/aruco_nav.py, причём с мелкими расхождениями (серво руля ставилось
не везде, неизвестные направления в commands.py тихо превращались в поворот
вправо). Теперь — здесь.
"""
from config import SERVO_STEERING, STEER_MAP

TURN_SHARP = 0.3     # радиус дуги при движении с поворотом
TURN_NONE = 0.5

_TURNS = ('left', 'right')


def parse_direction(d):
    """'forward_left' -> ('forward', 'left'); неизвестное -> None."""
    if d in ('forward', 'backward'):
        return d, 'no'
    if d in _TURNS:
        return 'forward', d
    if '_' in d:
        head, _, turn = d.partition('_')
        if head in ('forward', 'backward') and turn in _TURNS:
            return head, turn
    return None


def drive(state, d, speed=None):
    """Выполнить направление d. Возвращает True, если команда распознана.

    d: forward | backward | left | right | forward_left | forward_right |
       backward_left | backward_right | spin_left | spin_right | stop
    spin_* — разворот на месте (борта в противоположные стороны).
    """
    motors = state.motors
    if not motors:
        return False
    speed = state.speed if speed is None else speed
    d = str(d)
    if d == 'stop':
        motors.stop()
    elif d in ('spin_left', 'spin_right'):
        motors.spin(speed, d.split('_')[1])
    else:
        parsed = parse_direction(d)
        if parsed is None:
            return False
        direction, turn = parsed
        radius = TURN_NONE if turn == 'no' else TURN_SHARP
        motors.move(speed, direction, turn, radius)
    if state.servos:
        state.servos.set_angle(SERVO_STEERING, STEER_MAP.get(d, 90))
    return True
