"""TCP nav-сервер на Raspberry Pi — порт robot/pi_server.py из репозитория
hugo-Peltier/Adeept-picarPro (ArUco-навигация с хост-ПК).

Протокол (построчный, "\\n"-терминированный, как в оригинале):
    FORWARD / BACKWARD / LEFT / RIGHT / STOP   — движение
    SPEED <0-100>                              — скорость
    PICK_BLUE                                  — схват (клешня закрыть)
    DROP                                       — отпустить (клешня открыть)
    STATUS                                     — JSON-статус в ответ
    QUIT                                       — закрыть соединение

Важно: в оригинале сервер слушал порт 5000 и конфликтовал с Flask —
здесь используется NAV_TCP_PORT (по умолчанию 5001, см. config.py).

Команды безопасности: при разрыве связи машина останавливается (watchdog).
"""
import json
import socket
import socketserver
import threading
import time

from picarpro.logger import logger
from config import (
    NAV_TCP_PORT, STEER_MAP, SERVO_STEERING,
    CRANE_ARM_OPEN, CRANE_ARM_CLOSED, SERVO_CRANE_ARM,
)

LINE_TIMEOUT_S = 10.0     # без команд дольше этого — STOP (потеря связи хоста)


class NavCommandHandler(socketserver.StreamRequestHandler):
    timeout = LINE_TIMEOUT_S

    def handle(self):
        state = self.server.state
        addr = self.client_address[0]
        logger.info(f'[NavTCP] host connected: {addr}')
        state.nav_tcp_host = addr
        try:
            while state.running:
                try:
                    # timeout задаётся через class attr (socketserver выставляет
                    # settimeout на соединении) — io.readline не принимает timeout
                    line = self.rfile.readline()
                except (socket.timeout, TimeoutError):
                    # хост молчит — аварийный стоп (оригинал молча уезжал)
                    self._safe_stop(state, 'host timeout -> STOP')
                    continue
                if not line:
                    break
                try:
                    text = line.decode('utf-8').strip()
                except UnicodeDecodeError:
                    continue
                if not text:
                    continue
                reply = self._execute(state, text)
                if reply:
                    try:
                        self.wfile.write((reply + '\n').encode('utf-8'))
                    except Exception:
                        break
        except Exception as e:
            logger.warning(f'[NavTCP] connection error: {e}')
        finally:
            self._safe_stop(state, 'host disconnected -> STOP')
            state.nav_tcp_host = None
            logger.info(f'[NavTCP] host disconnected: {addr}')

    def _safe_stop(self, state, reason):
        try:
            if state.motors:
                state.motors.stop()
        except Exception:
            pass
        logger.info(f'[NavTCP] {reason}')

    def _execute(self, state, text):
        """Выполнить команду навигации; вернуть строку-ответ или None."""
        parts = text.split()
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else None
        motors, servos = state.motors, state.servos
        speed = state.speed
        state.nav_last_cmd = cmd
        state.nav_last_cmd_ts = time.time()

        if cmd in ('FORWARD', 'LEFT', 'RIGHT', 'BACKWARD', 'STOP'):
            if not motors:
                return 'ERR no motors'
            d = cmd.lower()
            if d == 'stop':
                motors.stop()
            elif d == 'forward':
                motors.move(speed, 'forward', 'no', 0.5)
            elif d == 'backward':
                motors.move(speed, 'backward', 'no', 0.5)
            else:
                motors.move(speed, 'forward', d, 0.3)
            if servos and d != 'stop':
                try:
                    servos.set_angle(SERVO_STEERING, STEER_MAP.get(d, 90))
                except Exception:
                    pass
            return f'OK {cmd}'

        elif cmd == 'SPEED' and arg is not None:
            try:
                state.speed = max(0, min(100, int(arg)))
                return f'OK SPEED {state.speed}'
            except ValueError:
                return 'ERR SPEED int expected'

        elif cmd == 'PICK_BLUE':
            if servos:
                servos.set_angle(SERVO_CRANE_ARM, CRANE_ARM_CLOSED)
                state.crane_arm_closed = True
                return 'OK PICK_BLUE'
            return 'ERR no servos'

        elif cmd == 'DROP':
            if servos:
                servos.set_angle(SERVO_CRANE_ARM, CRANE_ARM_OPEN)
                state.crane_arm_closed = False
                return 'OK DROP'
            return 'ERR no servos'

        elif cmd == 'STATUS':
            st = {
                'ok': True,
                'speed': state.speed,
                'distance': (state.ultrasonic.get_last_distance()
                             if state.ultrasonic
                             and state.ultrasonic._initialized else 0),
                'mpu6050': (state.mpu6050.get_data()
                            if state.mpu6050 and state.mpu6050.initialized
                            else None),
                'last_cmd': state.nav_last_cmd,
                'host': state.nav_tcp_host,
            }
            return json.dumps(st)

        elif cmd == 'QUIT':
            return 'BYE'

        return f'ERR unknown {cmd}'


class NavTcpServer:
    """Потоковый TCP-сервер (threading) — не мешает asyncio WebSocket-циклу."""

    def __init__(self, state, port=NAV_TCP_PORT):
        self.state = state
        self.port = port
        self._httpd = None
        self._thread = None

    def start(self):
        try:
            self._httpd = socketserver.ThreadingTCPServer(
                ('0.0.0.0', self.port), NavCommandHandler)
            self._httpd.daemon_threads = True
            self._httpd.state = self.state
        except OSError as e:
            logger.error(f'[NavTCP] cannot bind :{self.port} — {e}')
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f'[NavTCP] listening on :{self.port} '
                    '(протокол: FORWARD/LEFT/RIGHT/STOP/PICK_BLUE/STATUS)')

    def shutdown(self):
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
        logger.info('[NavTCP] stopped')
