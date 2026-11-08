"""Единая точка «команда направления -> моторы + рулевое серво».

Раньше один и тот же разбор направлений ('forward', 'left', 'forward_left',
'stop', ...) был скопирован в commands.py (веб), routes/nav_server.py (TCP) и
functions/aruco_nav.py, причём с мелкими расхождениями (серво руля ставилось
не везде, неизвестные направления в commands.py тихо превращались в поворот
вправо). Теперь — здесь.
"""
import time

from config import (SERVO_STEERING, STEER_MAP, AUTO_TURN_ARC_SPEED,
                    AUTO_ARC_RATE_DPS, AUTO_SPIN_TIMEOUT_S)

TURN_SHARP = 0.3     # радиус дуги при движении с поворотом
TURN_NONE = 0.5

_TURNS = ('left', 'right')
# v1.6.0: поворот руля БЕЗ движения (веб-клавиши A/D). По модели «как у
# автомобиля»: руль поворачивается, машина стоит; едет она только от W/S.
# Раньше 'left'/'right' включали моторы (дуга вперёд) — при повороте колёс
# на месте машина сама ехала вперёд.
_STEER_ONLY = ('steer_left', 'steer_right')


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
       backward_left | backward_right | spin_left | spin_right | stop |
       steer_left | steer_right
    steer_* — повернуть рулевые колёса БЕЗ движения (веб-клавиши A/D);
    left/right — дуга с движением (автономные режимы, TCP, голос).
    spin_* — разворот на месте (борта в противоположные стороны).
    """
    motors = state.motors
    if not motors:
        return False
    speed = state.speed if speed is None else speed
    d = str(d)
    if d == 'stop':
        motors.stop()
    elif d in _STEER_ONLY:
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
        if d in _STEER_ONLY:
            # A/D без W/S: только руль, моторы стоят
            state.servos.set_angle(SERVO_STEERING,
                                   STEER_MAP.get(d.partition('_')[2], 90))
        else:
            state.servos.set_angle(SERVO_STEERING, STEER_MAP.get(d, 90))
    return True


def arc_turn(motors, servos, deg, alive, wait, heading=None, speed=None):
    """Разворот на угол deg дугой — руль до упора + ход вперёд (v1.6.2).

    Шасси PiCarPro — Аккерман (рулевое серво, передние управляемые
    колёса): «танковый» разворот на месте (spin) физически не вращает
    машину — передние колёса скребут пол, и робот «при разворотах не
    мог повернуться» (миссии, объезд препятствий). Поэтому разворот
    делаем как на настоящей машине: выворачиваем руль до упора и едем
    вперёд, пока не наберём угол.

    deg: >0 — вправо, <0 — влево (величина |deg| — на сколько градусов).
    alive(): False — внешняя остановка (миссия/режим остановлены).
    wait(sec): прерываемая пауза (True = прервано).
    heading: HeadingTracker с уже проведённой калибровкой нуля или None —
        тогда разворот по времени (AUTO_ARC_RATE_DPS). Для каждого вызова
        аккумулятор угла обнуляется (heading.begin()).
    speed: скорость дуги, по умолчанию AUTO_TURN_ARC_SPEED.
    """
    need = abs(deg)
    if need < 3:
        return
    side = 'right' if deg > 0 else 'left'
    speed = int(max(10, min(100, AUTO_TURN_ARC_SPEED if speed is None
                            else speed)))
    try:
        if servos:
            # руль до упора (STEER_MAP['left']=150 / ['right']=30 —
            # крайние значения внутри SERVO_LIMITS[0])
            servos.set_angle(SERVO_STEERING, STEER_MAP[side])
        if motors:
            motors.move(speed, 'forward', 'no', TURN_NONE)
        if heading is not None and heading.available:
            heading.begin()          # обнулить аккумулятор угла
            lead = AUTO_ARC_RATE_DPS * 0.2    # упреждение на инерцию
            t0 = time.monotonic()
            while alive():
                if heading.update() >= need - lead:
                    break
                if time.monotonic() - t0 > AUTO_SPIN_TIMEOUT_S:
                    break            # предохранитель: не крутимся вечно
                wait(0.01)
        else:
            wait(need / AUTO_ARC_RATE_DPS)
    finally:
        if motors:
            motors.stop()
        if servos:
            servos.set_angle(SERVO_STEERING, 90)   # руль прямо
    wait(0.12)                     # дать машине успокоиться
