import os, threading, time
from picarpro.logger import logger
from config import (LED_COUNT, LED_BRIGHTNESS, LED_ANIM_FPS,
                    LED_BACKEND, LED_PWM_PIN, LED_PWM_DMA)

# v1.6.2 — НАЙДЕН КОРЕНЬ «неработающей ленты». Лента подключена к GPIO12
# (PWM0, физ. пин 32) — так в оригинальной прошивке Adeept
# (Server/RobotLight.py: LED_PIN=12, LED_DMA=10, LED_CHANNEL=0 через
# rpi_ws281x) и в docs/components/ws2812.json. Все версии мода
# v1.0..v1.6.1 выводили сигнал в SPI MOSI (GPIO10, пин 19), куда лента НЕ
# подключена, поэтому она не зажигалась ни разу при любых таймингах
# SPI-кодирования.
#
# Основной бэкенд теперь rpi_ws281x (PWM/DMA на GPIO12, тайминги выдаёт
# сам драйвер — ровно по спецификации WS2812). SPI-бэкенд оставлен как
# фолбэк для сборок с DIN на пине 19 (LED_BACKEND='spi'), его тайминги
# тоже исправлены: классическая схема «3 SPI-бита на бит WS2812» при
# ЗАПРОСЕ 2.4 МГц. bcm2835 округляет делитель до степени двойки, факт.
# частота получается 1.95–3.9 МГц — в обоих случаях тайминги в окне
# декодирования: T1H = 512–1024 нс, T0H = 256–512 нс, период бита
# 768–1536 нс (спека: T1H 800±150, T0H 400±150, период 1250±600 нс).
# Раньше (v1.6.1) запрашивали 8 МГц => факт. 7.8125 МГц, T1H = 256 нс —
# чип декодировал ВСЕ биты как «0», лента оставалась чёрной.
try:
    from rpi_ws281x import PixelStrip as _PixelStrip, Color as _WSColor
    _HAS_WS281X = True
except ImportError:
    _PixelStrip = None
    _WSColor = None
    _HAS_WS281X = False

try:
    import spidev
    _HAS_SPI = True
except ImportError:
    _HAS_SPI = False
    spidev = None

_BIT0 = 0b100   # высокий 1 SPI-бит + два низких
_BIT1 = 0b110   # два высоких + один низкий
_LATCH_BYTES = 90   # 720 SPI-бит x ~128-512 нс — тишина >= 50 мкс latch

# Троттлинг диагностического лога при недоступном выводе (v1.4.0)
LED_DIAG_INTERVAL_S = 300.0
_SPI_DEV = '/dev/spidev0.0'
_WS2812_FREQ_HZ = 800_000   # как в RobotLight.py (LED_FREQ_HZ)


def _byte_to_spi(byte):
    """Байт цвета -> 3 SPI-байта (8 бит x 3 SPI-бита = 24 бита)."""
    v = 0
    for i in range(8):
        v = (v << 3) | (_BIT1 if (byte & (1 << (7 - i))) else _BIT0)
    return v.to_bytes(3, 'big')


# Таблица кодирования байта цвета в 3 SPI-байта: раньше каждый кадр гонял
# 16x3x8 итераций чистого Python (на 50 кадр/с — заметная доля ядра Pi).
_SPI_TABLE = tuple(_byte_to_spi(i) for i in range(256))
_FRAME_S = 1.0 / max(1, LED_ANIM_FPS)
_SPEED_K = _FRAME_S / 0.02    # во сколько раз кадр длиннее прежних 20 мс


class LEDController:
    _last_diag_ts = 0.0   # классовое поле: общее для всех инстансов

    def __init__(self):
        self._spi = None
        self._strip = None      # rpi_ws281x PixelStrip (бэкенд pwm)
        self._backend = None    # 'pwm' | 'spi'
        self._mode = 'off'
        self._color = (255, 0, 0)
        self._flag = threading.Event()
        self._wake = threading.Event()      # прерывает паузы анимации
        self._show_lock = threading.Lock()  # вывод не терпит параллельных записей
        self._gen = 0                       # поколение анимации (смена режима)
        self._power_scale = 1.0             # режим экономии: множитель яркости
        self._running = True
        self._thread = None
        self._pixels = [(0, 0, 0)] * LED_COUNT
        self._initialized = False
        mode = LED_BACKEND if LED_BACKEND in ('pwm', 'spi') else 'auto'
        if mode in ('pwm', 'auto'):
            if _HAS_WS281X:
                try:
                    self._open_pwm()
                    self._backend = 'pwm'
                except Exception as e:
                    self._log_diag(e, backend='pwm')
            else:
                self._log_diag(None, backend='pwm')   # пакет не установлен
        if self._backend is None and mode in ('spi', 'auto'):
            try:
                if self._open_spi():
                    self._backend = 'spi'
            except Exception as e:
                self._log_diag(e, backend='spi')
        if self._backend is None:
            return
        self._initialized = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f'[LEDs] OK — {LED_COUNT} WS2812 via {self._backend}'
                    + (' (GPIO12, как в оригинале Adeept)'
                       if self._backend == 'pwm' else ' (GPIO10/MOSI)'))

    @classmethod
    def _throttle(cls):
        """True, если диагностический лог подавлен (не истёк интервал)."""
        now = time.monotonic()
        if cls._last_diag_ts and now - cls._last_diag_ts < LED_DIAG_INTERVAL_S:
            return True
        cls._last_diag_ts = now
        return False

    @classmethod
    def _log_diag(cls, err=None, backend='spi'):
        """Диагностика неработающей WS2812-ленты — не чаще
        LED_DIAG_INTERVAL_S (v1.4.0)."""
        if cls._throttle():
            return
        if err is not None:
            logger.error(f'[LEDs] init failed ({backend}): {err}')
        else:
            logger.error(f'[LEDs] {backend} backend unavailable')
        if backend == 'pwm':
            if not _HAS_WS281X:
                logger.error(
                    '[LEDs] подсказки: пакет rpi_ws281x не установлен — '
                    'лейта сидит на GPIO12 (физ. пин 32), как в оригинале '
                    'Adeept. Установите: sudo pip3 install rpi-ws281x '
                    '(или запустите setup.py заново) и перезапустите '
                    'сервис; модуль подхватится автоматически '
                    '(авто-ретраи v1.4.0).')
            else:
                logger.error(
                    '[LEDs] подсказки: rpi_ws281x требует root (DMA/PWM0 '
                    'GPIO12) и свободного PWM-канала — не запускайте '
                    'аналоговый звук (3.5 мм) одновременно с лентой.')
            return
        if not os.path.exists(_SPI_DEV):
            logger.error(
                '[LEDs] подсказки: /dev/spidev0.0 отсутствует — включите SPI '
                '(raspi-config Interface Options -> SPI, или строка '
                'dtparam=spi=on в /boot/firmware/config.txt) и перезагрузите; '
                'установщик v1.4.0 делает это сам. Фолбэк-проводка ленты — '
                'GPIO10/MOSI (пин 19); штатная проводка этой сборки — '
                'GPIO12 (пин 32) через rpi_ws281x (LED_BACKEND="pwm"). '
                'Модуль подхватится автоматически (авто-ретраи v1.4.0) после '
                'появления SPI.')
        else:
            logger.error(
                '[LEDs] подсказки: /dev/spidev0.0 есть — проверьте провод DIN '
                '(GPIO10/MOSI, физ. пин 19; штатная проводка — GPIO12/пин 32) '
                'и питание 5V ленты; при просадке питания лента гаснет '
                '(см. комментарий LED_COUNT в config.py).')

    def _open_pwm(self):
        """Открыть ленту через rpi_ws281x (PWM0/DMA, GPIO12) — как в
        оригинальной прошивке Adeept RobotLight.py."""
        self._strip = _PixelStrip(LED_COUNT, LED_PWM_PIN, _WS2812_FREQ_HZ,
                                  LED_PWM_DMA, False, 255, 0)
        self._strip.begin()

    def _open_spi(self):
        """Открыть SPI (с закрытием предыдущего дескриптора при retry)."""
        try:
            if self._spi:
                try:
                    self._spi.close()
                except Exception:
                    pass
            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            # 2.4 МГц запрос => факт. 1.95-3.9 МГц (округление делителя
            # bcm2835): тайминги WS2812 в окне декодирования при любом
            # варианте округления — см. расчёт в шапке файла (v1.6.2).
            self._spi.max_speed_hz = 2_400_000
            self._spi.mode = 0b00
            return True
        except Exception:
            self._spi = None
            return False

    def _show(self, gen=None):
        """Вывести self._pixels на ленту. gen — поколение анимации: кадр
        устаревшей анимации (после смены режима) молча отбрасывается, иначе
        «хвост» старой анимации мог зажечь ленту уже после set_mode('off')."""
        if not self._initialized:
            return
        with self._show_lock:
            if gen is not None and gen != self._gen:
                return
            b = LED_BRIGHTNESS / 255.0 * self._power_scale
            if self._backend == 'pwm':
                # rpi_ws281x: Color(Red, Green, Blue) — порядок GRB на ленте
                # драйвер учитывает сам; ретрай с переоткрытием при сбое
                try:
                    for i, (r, g, bl) in enumerate(self._pixels):
                        self._strip.setPixelColor(
                            i, _WSColor(int(r * b) & 255, int(g * b) & 255,
                                        int(bl * b) & 255))
                    self._strip.show()
                except Exception:
                    try:
                        self._open_pwm()
                        for i, (r, g, bl) in enumerate(self._pixels):
                            self._strip.setPixelColor(
                                i, _WSColor(int(r * b) & 255,
                                            int(g * b) & 255,
                                            int(bl * b) & 255))
                        self._strip.show()
                    except Exception:
                        pass
                return
            parts = []
            for r, g, bl in self._pixels:
                parts.append(_SPI_TABLE[int(g * b) & 255])
                parts.append(_SPI_TABLE[int(r * b) & 255])
                parts.append(_SPI_TABLE[int(bl * b) & 255])
            data = b''.join(parts) + b'\x00' * _LATCH_BYTES
            try:
                self._spi.writebytes(data)
            except Exception:
                # сбой SPI (глитч питания/драйвера): переоткрываем устройство
                # и повторяем один раз — иначе лента гаснет навсегда
                if self._open_spi():
                    try:
                        self._spi.writebytes(data)
                    except Exception:
                        pass

    def fill(self, r, g, b, gen=None):
        self._pixels = [(r, g, b)] * LED_COUNT
        self._show(gen)

    def clear(self):
        self.fill(0, 0, 0)

    def set_power_scale(self, scale):
        """Множитель яркости 0..1 (режим экономии при низком заряде)."""
        scale = max(0.0, min(1.0, float(scale)))
        if abs(scale - self._power_scale) < 0.01:
            return
        self._power_scale = scale
        if self._mode != 'off':
            self._show()

    def set_mode(self, mode, color=(255, 0, 0)):
        self._color = color
        with self._show_lock:
            self._gen += 1          # все кадры старой анимации теперь «чужие»
        self._flag.clear()
        self._wake.set()            # не ждать конца паузы старой анимации
        self._mode = mode
        if mode in ('off', 'solid'):
            if mode == 'solid':
                self.fill(*color)
            else:
                self.clear()
        else:
            self._flag.set()

    def _sleep(self, seconds):
        self._wake.wait(seconds)

    def _alive(self, gen, *modes):
        return (self._flag.is_set() and gen == self._gen
                and self._running and self._mode in modes)

    def _run(self):
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            self._wake.clear()
            gen = self._gen
            try:
                fn = getattr(self, f'_anim_{self._mode}', None)
                if fn is None:
                    self._flag.clear()      # неизвестный режим — не крутимся вхолостую
                else:
                    fn(gen)
            except Exception:
                self._sleep(0.1)

    def _anim_breath(self, gen):
        r, g, b = self._color
        step = int(8 * _SPEED_K)
        while self._alive(gen, 'breath'):
            for i in list(range(0, 256, step)) + list(range(255, -1, -step)):
                if not self._alive(gen, 'breath'):
                    return
                s = i / 255.0
                self.fill(int(r * s), int(g * s), int(b * s), gen)
                self._sleep(_FRAME_S)

    def _anim_flow(self, gen):
        off = 0
        step = max(1, int(_SPEED_K))
        while self._alive(gen, 'flow', 'flowing'):
            r, g, b = self._color
            for i in range(LED_COUNT):
                base = self._wheel((i * 256 // LED_COUNT + off) % 256)
                self._pixels[i] = (
                    (base[0] * r) >> 8,
                    (base[1] * g) >> 8,
                    (base[2] * b) >> 8,
                )
            self._show(gen)
            off = (off + step) % 256
            self._sleep(_FRAME_S)

    _anim_flowing = _anim_flow

    def _anim_rainbow(self, gen):
        off = 0
        step = max(1, int(2 * _SPEED_K))
        while self._alive(gen, 'rainbow'):
            for i in range(LED_COUNT):
                self._pixels[i] = self._wheel((i * 256 // LED_COUNT + off) & 255)
            self._show(gen)
            off = (off + step) % 256
            self._sleep(_FRAME_S)

    def _anim_police(self, gen):
        while self._alive(gen, 'police'):
            for phase in (0, 1):
                if not self._alive(gen, 'police'):
                    return
                for i in range(LED_COUNT):
                    blue = (i + phase) % 2 == 0
                    self._pixels[i] = (0, 0, 255) if blue else (255, 0, 0)
                self._show(gen)
                self._sleep(0.15)

    def _anim_colorWipe(self, gen):
        r, g, b = self._color
        while self._alive(gen, 'colorWipe'):
            for i in range(LED_COUNT):
                if not self._alive(gen, 'colorWipe'):
                    return
                self._pixels[i] = (r, g, b)
                self._show(gen)
                self._sleep(0.03)
            self._sleep(0.5)
            self.fill(0, 0, 0, gen)
            self._sleep(0.2)

    @staticmethod
    def _wheel(p):
        if p < 85:
            return (p * 3, 255 - p * 3, 0)
        if p < 170:
            p -= 85
            return (255 - p * 3, 0, p * 3)
        p -= 170
        return (0, p * 3, 255 - p * 3)

    def shutdown(self):
        self._running = False
        self._flag.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.clear()
        if self._strip:
            try:
                self._strip = None      # rpi_ws281x не требует закрытия
            except Exception:
                pass
        if self._spi:
            try:
                self._spi.close()
            except Exception:
                pass
        logger.info('[LEDs] shutdown')
