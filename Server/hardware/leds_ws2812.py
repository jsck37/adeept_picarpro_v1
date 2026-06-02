"""WS2812 LED strip — rpi_ws281x with SPI fallback."""

import time, threading
from Server.config import LED_COUNT, LED_BRIGHTNESS

class LEDController:
    def __init__(self):
        self._strip = self._spi = None
        self._use_spi = False
        self._running = True
        self._mode = "solid"
        self._color = (255, 0, 0)
        self._flag = threading.Event()
        self._flag.set()
        self._initialized = False
        self._pixels = [(0, 0, 0)] * LED_COUNT
        self._init_strip()

    def _init_strip(self):
        try:
            import rpi_ws281x as ws
            self._strip = ws.PixelStrip(LED_COUNT, 12, 800000, 10, False, LED_BRIGHTNESS, 0)
            self._strip.begin()
            self._initialized = True
            print(f"[LEDs] WS2812: {LED_COUNT} LEDs")
            threading.Thread(target=self._run, daemon=True).start()
        except Exception:
            self._try_spi()

    def _try_spi(self):
        try:
            import spidev
            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            self._spi.max_speed_hz = 4000000
            self._use_spi = True
            self._initialized = True
            print(f"[LEDs] SPI fallback: {LED_COUNT} LEDs")
            threading.Thread(target=self._run, daemon=True).start()
        except Exception as e:
            print(f"[LEDs] Failed: {e}")

    def _show(self):
        if not self._initialized:
            return
        try:
            b = LED_BRIGHTNESS / 255.0
            if self._use_spi:
                data = bytearray()
                for r, g, bl in self._pixels:
                    for byte in [int(g*b), int(r*b), int(bl*b)]:
                        for bit in range(7, -1, -1):
                            data.extend(b'\x06' if byte & (1 << bit) else b'\x04')
                data.extend(b'\x00' * 60)
                self._spi.writebytes(data)
            else:
                for i, (r, g, bl) in enumerate(self._pixels):
                    self._strip.setPixelColor(i, (int(r*b) << 16) | (int(g*b) << 8) | int(bl*b))
                self._strip.show()
        except Exception:
            pass

    def fill(self, r, g, b):
        self._pixels = [(r, g, b)] * LED_COUNT
        self._show()

    def clear(self):
        self.fill(0, 0, 0)

    def set_mode(self, mode, color=(255, 0, 0)):
        self._color = color
        if mode in ("off", "solid"):
            self._mode = mode
            self.fill(*color) if mode == "solid" else self.clear()
            self._flag.clear()
        else:
            # For animated modes: restart animation so it picks up new color
            self._flag.clear()
            time.sleep(0.05)
            self._mode = mode
            self._flag.set()

    def _run(self):
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            try:
                getattr(self, f'_anim_{self._mode}', lambda: None)()
            except Exception:
                time.sleep(0.1)

    def _anim_breath(self):
        while self._flag.is_set() and self._mode == "breath":
            r, g, b = self._color
            for i in range(0, 256, 5):
                if not self._flag.is_set() or self._mode != "breath":
                    return
                s = i / 255.0
                self.fill(int(r*s), int(g*s), int(b*s))
                time.sleep(0.02)
            for i in range(255, -1, -5):
                if not self._flag.is_set() or self._mode != "breath":
                    return
                s = i / 255.0
                self.fill(int(r*s), int(g*s), int(b*s))
                time.sleep(0.02)

    def _anim_flow(self):
        off = 0
        while self._flag.is_set() and self._mode in ("flow", "flowing"):
            r, g, b = self._color
            for i in range(LED_COUNT):
                base = self._wheel((i * 256 // LED_COUNT + off) % 256)
                self._pixels[i] = (
                    (base[0] * r) >> 8,
                    (base[1] * g) >> 8,
                    (base[2] * b) >> 8,
                )
            self._show()
            off = (off + 1) % 256
            time.sleep(0.02)

    _anim_flowing = _anim_flow

    def _anim_rainbow(self):
        off = 0
        while self._flag.is_set() and self._mode == "rainbow":
            for i in range(LED_COUNT):
                self._pixels[i] = self._wheel((i * 256 // LED_COUNT + off) & 255)
            self._show()
            off = (off + 2) % 256
            time.sleep(0.02)

    def _anim_police(self):
        half = LED_COUNT // 2
        while self._flag.is_set() and self._mode == "police":
            for i in range(half):
                self._pixels[i] = (255, 0, 0)
            for i in range(half, LED_COUNT):
                self._pixels[i] = (0, 0, 255)
            self._show()
            time.sleep(0.15)
            for i in range(half):
                self._pixels[i] = (0, 0, 255)
            for i in range(half, LED_COUNT):
                self._pixels[i] = (255, 0, 0)
            self._show()
            time.sleep(0.15)

    def _anim_colorWipe(self):
        r, g, b = self._color
        while self._flag.is_set() and self._mode == "colorWipe":
            for i in range(LED_COUNT):
                if not self._flag.is_set():
                    return
                self._pixels[i] = (r, g, b)
                self._show()
                time.sleep(0.03)
            time.sleep(0.5)
            self.clear()
            time.sleep(0.2)

    @staticmethod
    def _wheel(p):
        if p < 85:
            return (p * 3, 255 - p * 3, 0)
        elif p < 170:
            p -= 85
            return (255 - p * 3, 0, p * 3)
        else:
            p -= 170
            return (0, p * 3, 255 - p * 3)

    def shutdown(self):
        self._running = False
        self._flag.set()
        time.sleep(0.1)
        self.clear()
        if self._spi:
            try:
                self._spi.close()
            except Exception:
                pass
        print("[LEDs] Shutdown")
