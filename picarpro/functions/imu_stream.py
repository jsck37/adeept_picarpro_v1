"""Стриминг гироскопа MPU6050 по UDP — порт IMU-канала Demo.py из репозитория
hugo-Peltier/Adeept-picarPro.

Хост-скрипт tools/aruco_nav_host.py слушает пакеты {"t": <sec>, "gz": <rad/s>}
на IMU_UDP_PORT (5005) и сливает их с визуальным направлением робота
(alpha-фильтр) — навигация не теряет курс, когда маркер перекрыт.

Адрес подписчика определяется автоматически: как только хост подключается
к NavTCP (state.nav_tcp_host), пакеты уходят на его IP. Частота 20 Гц.
"""
import json
import socket
import threading
import time

from picarpro.logger import logger
from config import IMU_UDP_PORT, IMU_STREAM_HZ

GZ_SCALE = 0.017453292519943295  # °/с -> рад/с (pi/180)


class ImuUdpStreamer:
    def __init__(self, state, port=IMU_UDP_PORT, hz=IMU_STREAM_HZ):
        self.state = state
        self.port = port
        self._interval = 1.0 / max(1, hz)
        self._running = False
        self._thread = None
        self._sock = None
        self._last_target_ip = None

    def start(self):
        if self._running:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except Exception as e:
            logger.error(f'[IMU-UDP] socket failed: {e}')
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f'[IMU-UDP] streaming gz -> {self.port} '
                    f'({1/self._interval:.0f} Hz)')

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        logger.info('[IMU-UDP] stopped')

    def _loop(self):
        """Оптимизация v1.2.0: без подписчика спим 0.25 с (раньше — 50
        пробуждений/с впустую); при активном стриме выдерживаем точный
        интервал короткими ожиданиями."""
        last_send = 0.0
        while self._running:
            host_ip = self.state.nav_tcp_host
            if not (host_ip and mpu_ready(self.state)):
                time.sleep(0.25)
                continue
            now = time.time()
            dt = self._interval - (now - last_send)
            if dt > 0:
                # досыпаем до ритма, но не более 50 мс — чтобы быстро
                # подхватить появление/пропажу подписчика
                time.sleep(min(dt, 0.05))
                continue
            last_send = time.time()
            data = self.state.mpu6050.get_data()
            if data:
                pkt = json.dumps({
                    't': now,
                    'gz': data['gyro']['z'] * GZ_SCALE,
                }).encode('utf-8')
                try:
                    self._sock.sendto(pkt, (host_ip, self.port))
                except Exception:
                    pass
                if host_ip != self._last_target_ip:
                    self._last_target_ip = host_ip
                    logger.info(f'[IMU-UDP] subscriber: {host_ip}')


def mpu_ready(state):
    return state.mpu6050 is not None and state.mpu6050.initialized
