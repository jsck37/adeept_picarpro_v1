import os, subprocess, threading, time
from picarpro.logger import logger
from config import (
    VOICE_MODEL_PATH, VOICE_ALSA_DEVICE, VOICE_OUTPUT_FILE,
    SERVO_CAM_PAN, SERVO_CAM_TILT, DEFAULT_SPEED,
)


class VoiceCommandController:
    # варианты с точкой не нужны: проверка идёт по вхождению подстроки
    COMMAND_MAP = {
        'look left': 'lookLeft', 'look right': 'lookRight',
        'camera up': 'camUp', 'camera down': 'camDown',
        'forward': 'forward', 'backward': 'backward',
        'stop': 'stop',
    }

    def __init__(self, servos, motors):
        self.servos = servos
        self.motors = motors
        self._running = True
        self._active = False
        self._flag = threading.Event()
        self._flag.clear()
        self._thread = None
        self._sherpa_process = None
        self._initialized = False
        self._last_command = ''
        self._last_size = 0
        sherpa_binary = os.path.join(
            os.path.dirname(VOICE_MODEL_PATH), '..', 'sherpa-ncnn-alsa')
        if os.path.exists(sherpa_binary) and os.path.exists(VOICE_MODEL_PATH):
            self._sherpa_binary = sherpa_binary
            self._initialized = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            logger.info('[Voice] Sherpa-NCNN ready')
        else:
            logger.warning('[Voice] Sherpa-NCNN not found — voice control disabled')

    def start(self):
        if not self._initialized:
            return
        self._active = True
        self._start_sherpa()
        self._flag.set()

    def stop(self):
        self._active = False
        self._flag.clear()
        self._stop_sherpa()

    def _start_sherpa(self):
        if self._sherpa_process is not None:
            return
        try:
            # Баг исходной версии (voice_command.py:57-63): дескриптор файла
            # открывался в with-блоке и ЗАКРЫВАЛСЯ до Popen(stdout=f) —
            # Popen получал закрытый файл и голосовой запуск всегда падал.
            self._sherpa_log = open(VOICE_OUTPUT_FILE, 'w')
            self._sherpa_process = subprocess.Popen(
                [self._sherpa_binary, VOICE_MODEL_PATH, VOICE_ALSA_DEVICE],
                stdout=self._sherpa_log, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f'[Voice] failed to start Sherpa-NCNN: {e}')
            self._close_sherpa_log()

    def _close_sherpa_log(self):
        if getattr(self, '_sherpa_log', None):
            try:
                self._sherpa_log.close()
            except Exception:
                pass
            self._sherpa_log = None

    def _stop_sherpa(self):
        if self._sherpa_process is None:
            return
        try:
            self._sherpa_process.terminate()
            self._sherpa_process.wait(timeout=5)
        except Exception:
            try:
                self._sherpa_process.kill()
            except Exception:
                pass
        self._sherpa_process = None
        self._close_sherpa_log()

    def _run(self):
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            try:
                self._read_and_execute()
            except Exception:
                pass
            time.sleep(0.2)

    @classmethod
    def _match(cls, line):
        """Команда из ОДНОЙ строки распознавания: побеждает ключ, стоящий
        правее (последнее произнесённое слово)."""
        best, best_pos = None, -1
        for key, command in cls.COMMAND_MAP.items():
            pos = line.rfind(key)
            if pos >= 0 and (pos > best_pos or (
                    pos == best_pos and len(key) > len(best[0]))):
                best, best_pos = (key, command), pos
        return best[1] if best else None

    def _read_and_execute(self):
        """Прочитать вывод распознавателя и выполнить НОВУЮ команду.

        Раньше обрабатывался весь файл целиком: после «look left» и затем
        «stop» файл содержал оба слова, и по порядку словаря снова
        выполнялась «look left», а не «stop». Повтор одной и той же команды
        подряд («stop», «stop») тоже терялся — содержимое совпадало.
        Теперь берётся только последняя непустая строка, а новизну
        определяет размер файла.
        """
        try:
            if not os.path.exists(VOICE_OUTPUT_FILE):
                return
            with open(VOICE_OUTPUT_FILE) as f:
                content = f.read().lower()
            size = len(content)
            if size < self._last_size:          # файл перезапущен — начинаем заново
                self._last_size = 0
                self._last_command = ''
            lines = [x.strip() for x in content.replace('\r', '\n').split('\n')
                     if x.strip()]
            if size == self._last_size or not lines:
                return
            self._last_size = size
            line = lines[-1]
            # частичное «дорастание» той же строки не считаем новой командой
            if self._last_command and line.startswith(self._last_command):
                self._last_command = line
                return
            command = self._match(line)
            if command:
                self._last_command = line
                self._execute_command(command)
        except Exception:
            pass

    def _execute_command(self, command):
        logger.info(f'[Voice] command: {command}')
        if command == 'lookLeft':
            self.servos.move_angle(SERVO_CAM_PAN, -30)
        elif command == 'lookRight':
            self.servos.move_angle(SERVO_CAM_PAN, 30)
        elif command == 'camUp':
            self.servos.move_angle(SERVO_CAM_TILT, 15)
        elif command == 'camDown':
            self.servos.move_angle(SERVO_CAM_TILT, -15)
        elif command == 'forward':
            self.motors.move(DEFAULT_SPEED, 'forward', 'no', 0.5)
        elif command == 'backward':
            self.motors.move(DEFAULT_SPEED, 'backward', 'no', 0.5)
        elif command == 'stop':
            self.motors.stop()

    def shutdown(self):
        self.stop()
        self._running = False
        self._flag.set()
        logger.info('[Voice] shutdown')
