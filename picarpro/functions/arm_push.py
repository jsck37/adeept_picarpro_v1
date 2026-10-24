"""Расталкивание препятствий клешней — порт идеи Phase 2 «Obstacle handling»
из репозитория hugo-Peltier/Adeept-picarPro (README: «the robotic arm deploys
and pushes the object out of the way before navigation resumes»).

Оригинальный ultrasonic_push.py в репозитории отсутствует (файл не залит),
поэтому поведение реализовано по описанию: если HC-SR04 видит препятствие
ближе OBSTACLE_PUSH_DISTANCE_M — машина останавливается и клешня делает
сгребающее движение, после чего препятствие перепроверяется.

Режим фоновый: включается командой 'push' {action: 'start'/'stop'}.
"""
import threading
import time

from picarpro.logger import logger
from config import (
    OBSTACLE_PUSH_DISTANCE_M, OBSTACLE_PUSH_COOLDOWN_S,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED, CRANE_GRIP_LOW, CRANE_GRIP_MID,
    SERVO_CRANE_ARM, SERVO_CRANE_GRIP,
)


class ObstaclePusher:
    def __init__(self, state):
        self.state = state
        self._active = False
        self._running = False
        self._thread = None
        self.pushes_done = 0
        self.last_distance = None

    @property
    def active(self):
        return self._active

    def get_status(self):
        return {'active': self._active, 'pushes_done': self.pushes_done,
                'last_distance_m': self.last_distance}

    def start(self):
        if self._active:
            return True, 'already running'
        ultra = self.state.ultrasonic
        if not (ultra and ultra._initialized):
            return False, 'HC-SR04 недоступен'
        if not (self.state.servos and self.state.servos._pwm_initialized):
            return False, 'серво недоступны'
        self._running = True
        self._active = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f'[Push] started (threshold {OBSTACLE_PUSH_DISTANCE_M} м)')
        return True, 'ok'

    def stop(self):
        self._active = False
        self._running = False
        logger.info('[Push] stopped')
        return True, 'ok'

    def _loop(self):
        last_push = 0.0
        while self._running:
            try:
                ultra = self.state.ultrasonic
                dist = (ultra.get_last_distance()
                        if ultra and ultra._initialized else None)
                self.last_distance = dist
                if (dist is not None and 0 < dist <= OBSTACLE_PUSH_DISTANCE_M
                        and time.time() - last_push > OBSTACLE_PUSH_COOLDOWN_S):
                    last_push = time.time()
                    self._push_sequence()
                time.sleep(0.2)
            except Exception as e:
                logger.warning(f'[Push] error: {e}')
                time.sleep(1.0)

    def _push_sequence(self):
        """Сгребающее движение клешней (останавливает машину на время манёвра)."""
        motors, servos = self.state.motors, self.state.servos
        logger.info(f'[Push] obstacle at {self.last_distance} м — pushing')
        try:
            if motors:
                motors.stop()
            # 1. клешня опускается вниз
            servos.smooth_move(SERVO_CRANE_GRIP, CRANE_GRIP_LOW,
                               steps=8, delay=0.03)
            # 2. сгребание: раскрытая клешня идёт от открытого к закрытому
            servos.smooth_move(SERVO_CRANE_ARM, CRANE_ARM_CLOSED,
                               steps=14, delay=0.05)
            self.pushes_done += 1
            time.sleep(0.3)
            # 3. возврат в рабочее положение
            servos.smooth_move(SERVO_CRANE_ARM, CRANE_ARM_OPEN,
                               steps=14, delay=0.05)
            servos.smooth_move(SERVO_CRANE_GRIP, CRANE_GRIP_MID,
                               steps=8, delay=0.03)
            self.state.crane_arm_closed = False
            self.state.crane_grip_position = 'mid'
            logger.info('[Push] obstacle pushed — resuming')
        except Exception as e:
            logger.warning(f'[Push] sequence error: {e}')
