"""WS2812 RGB LED strip via rpi_ws281x (DMA/PWM on GPIO 12) with SPI fallback."""

import time
import threading
from Server.config import LED_COUNT, LED_BRIGHTNESS

LED_PIN = 12
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_INVERT = False
LED_CHANNEL = 0


class LEDController:

    def __init__(self):
        self._strip = None
        self._spi = None
        self._use_spi = False
        self._running = True
        self._mode = "solid"
        self._color = (255, 0, 0)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._flag = threading.Event()
        self._flag.set()
        self._initialized = False
        self._pixels = [(0, 0, 0)] * LED_COUNT
        self._init_strip()

    def _init_strip(self):
        try:
            import rpi_ws281x as ws
            self._strip = ws.PixelStrip(
                LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA,
                LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL
            )
            self._strip.begin()
            self._initialized = True
            print(f"[LEDs] WS2812: {LED_COUNT} LEDs on GPIO {LED_PIN}")
            self._thread.start()
        except ImportError:
            print("[LEDs] rpi_ws281x not installed!")
            self._try_spi_fallback()
        except Exception as e:
            print(f"[LEDs] Init failed: {e}")
            self._try_spi_fallback()

    def _try_spi_fallback(self):
        try:
            import spidev
            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            self._spi.max_speed_hz = 4000000
            self._spi.mode = 0
            self._use_spi = True
            self._initialized = True
            print(f"[LEDs] SPI fallback: {LED_COUNT} LEDs")
            self._thread.start()
        except Exception as e:
            print(f"[LEDs] SPI fallback failed: {e}")

    def _ws2812_spi_encode(self, pixels):
        data = bytearray()
        for r, g, b in pixels:
            for byte in [g, r, b]:
                for bit in range(7, -1, -1):
                    if byte & (1 << bit):
                        data.extend(b'\x06')
                    else:
                        data.extend(b'\x04')
        data.extend(b'\x00' * 60)
        return data

    def show(self):
        if not self._initialized:
            return
        try:
            brightness = LED_BRIGHTNESS / 255.0
            if self._use_spi:
                scaled = [
                    (int(r * brightness), int(g * brightness), int(b * brightness))
                    for r, g, b in self._pixels
                ]
                self._spi.writebytes(self._ws2812_spi_encode(scaled))
            else:
                for i, (r, g, b) in enumerate(self._pixels):
                    self._strip.setPixelColor(
                        i,
                        int(r * brightness) << 16 |
                        int(g * brightness) << 8 |
                        int(b * brightness)
                    )
                self._strip.show()
        except Exception as e:
            print(f"[LEDs] Write error: {e}")

    def set_pixel(self, index, r, g, b):
        if 0 <= index < LED_COUNT:
            self._pixels[index] = (r, g, b)

    def fill(self, r, g, b):
        self._pixels = [(r, g, b)] * LED_COUNT
        self._show_safe()

    def _show_safe(self):
        """Show with error handling — safe for animation loops."""
        try:
            self.show()
        except Exception:
            pass

    def clear(self):
        self.fill(0, 0, 0)

    def set_mode(self, mode, color=(255, 0, 0)):
        self._mode = mode
        self._color = color
        if mode == "off":
            self.clear()
            self._flag.clear()
        elif mode == "solid":
            self.fill(*color)
            self._flag.clear()
        else:
            self._flag.set()

    def _run(self):
        while self._running:
            self._flag.wait()
            if not self._running:
                break
            try:
                if self._mode == "breath":
                    self._animate_breath()
                elif self._mode == "flowing":
                    self._animate_flowing()
                elif self._mode == "rainbow":
                    self._animate_rainbow()
                elif self._mode == "police":
                    self._animate_police()
                elif self._mode == "colorWipe":
                    self._animate_color_wipe()
            except Exception as e:
                print(f"[LEDs] Animation error: {e}")
                time.sleep(0.1)

    def _animate_breath(self):
        r, g, b = self._color
        while self._flag.is_set() and self._mode == "breath":
            for brightness in range(0, 256, 5):
                if not self._flag.is_set() or self._mode != "breath":
                    return
                s = brightness / 255.0
                self.fill(int(r * s), int(g * s), int(b * s))
                time.sleep(0.02)
            for brightness in range(255, -1, -5):
                if not self._flag.is_set() or self._mode != "breath":
                    return
                s = brightness / 255.0
                self.fill(int(r * s), int(g * s), int(b * s))
                time.sleep(0.02)

    def _animate_flowing(self):
        offset = 0
        while self._flag.is_set() and self._mode == "flowing":
            for i in range(LED_COUNT):
                hue = (i * 256 // LED_COUNT + offset) % 256
                self._pixels[i] = self._wheel(hue)
            self._show_safe()
            offset = (offset + 1) % 256
            time.sleep(0.02)

    def _animate_rainbow(self):
        offset = 0
        while self._flag.is_set() and self._mode == "rainbow":
            for i in range(LED_COUNT):
                hue = (i * 256 // LED_COUNT + offset) & 255
                self._pixels[i] = self._wheel(hue)
            self._show_safe()
            offset = (offset + 2) % 256
            time.sleep(0.02)

    def _animate_police(self):
        half = LED_COUNT // 2
        while self._flag.is_set() and self._mode == "police":
            for i in range(half):
                self._pixels[i] = (255, 0, 0)
            for i in range(half, LED_COUNT):
                self._pixels[i] = (0, 0, 255)
            self._show_safe()
            time.sleep(0.15)
            for i in range(half):
                self._pixels[i] = (0, 0, 255)
            for i in range(half, LED_COUNT):
                self._pixels[i] = (255, 0, 0)
            self._show_safe()
            time.sleep(0.15)

    def _animate_color_wipe(self):
        r, g, b = self._color
        while self._flag.is_set() and self._mode == "colorWipe":
            for i in range(LED_COUNT):
                if not self._flag.is_set() or self._mode != "colorWipe":
                    return
                self._pixels[i] = (r, g, b)
                self._show_safe()
                time.sleep(0.03)
            time.sleep(0.5)
            self.clear()
            time.sleep(0.2)

    @staticmethod
    def _wheel(pos):
        if pos < 85:
            return (pos * 3, 255 - pos * 3, 0)
        elif pos < 170:
            pos -= 85
            return (255 - pos * 3, 0, pos * 3)
        else:
            pos -= 170
            return (0, pos * 3, 255 - pos * 3)

    def shutdown(self):
        self._running = False
        self._flag.set()
        time.sleep(0.1)
        self.clear()
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass
        print("[LEDs] Shutdown")
