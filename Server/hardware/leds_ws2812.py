"""
WS2812 RGB LED strip control via rpi_ws281x (PWM/DMA).
Uses GPIO 10 for data — standard RobotHat configuration.

Why rpi_ws281x instead of SPI (spidev):
- SPI claims GPIO 8 (CS0) and GPIO 11 (SCLK), which conflict with
  the HC-SR04 ultrasonic sensor (Echo=GPIO8, Trig=GPIO11)
- rpi_ws281x uses DMA/PWM on GPIO 10 only — no SPI conflict
- This is the same approach used by the original Adeept software

Requires: rpi_ws281x (pip install rpi_ws281x), root access for DMA
"""

import time
import threading
from Server.config import LED_COUNT, LED_BRIGHTNESS


# WS2812 configuration for rpi_ws281x
LED_PIN = 10        # GPIO 10 (SPI0_MOSI on RobotHat)
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_INVERT = False
LED_CHANNEL = 0


class LEDController:
    """
    WS2812 LED strip controller using rpi_ws281x (DMA/PWM).

    Supports light modes:
    - breath: Pulsing brightness
    - flowing: Color cycling along strip
    - rainbow: Rainbow gradient
    - police: Red/blue alternating
    - colorWipe: Sequential color fill
    - solid: Static color
    """

    def __init__(self):
        self._strip = None
        self._spi = None
        self._use_spi = False
        self._running = True
        self._mode = "solid"
        self._color = (255, 0, 0)  # Default red
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._flag = threading.Event()
        self._flag.set()
        self._initialized = False

        self._init_strip()

    def _init_strip(self):
        """Initialize WS2812 via rpi_ws281x."""
        try:
            import rpi_ws281x as ws

            self._strip = ws.PixelStrip(
                LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA,
                LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL
            )
            self._strip.begin()

            self._pixels = [(0, 0, 0)] * LED_COUNT
            self._initialized = True
            print(f"[LEDs] WS2812 initialized via rpi_ws281x: {LED_COUNT} LEDs on GPIO {LED_PIN}")

            # Start animation thread
            self._thread.start()

        except ImportError:
            print("[LEDs] rpi_ws281x not installed! Install with: pip3 install rpi_ws281x")
            print("[LEDs] Also requires root access (sudo) for DMA")
            self._try_spi_fallback()
        except Exception as e:
            print(f"[LEDs] Failed to initialize rpi_ws281x: {e}")
            print("[LEDs] Common fixes:")
            print("[LEDs]   1. Run with sudo (rpi_ws281x needs DMA access)")
            print("[LEDs]   2. Install: pip3 install rpi_ws281x")
            print("[LEDs]   3. Check WS2812 wiring: DIN → GPIO10 (pin 19)")
            self._try_spi_fallback()

    def _try_spi_fallback(self):
        """Fallback: try SPI (spidev) if rpi_ws281x is not available."""
        try:
            import spidev

            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            self._spi.max_speed_hz = 4000000
            self._spi.mode = 0

            self._pixels = [(0, 0, 0)] * LED_COUNT
            self._use_spi = True
            self._initialized = True
            print(f"[LEDs] SPI fallback: WS2812 initialized via spidev ({LED_COUNT} LEDs)")
            print("[LEDs] WARNING: SPI mode may conflict with ultrasonic sensor on GPIO 8/11")

            self._thread.start()

        except Exception as e:
            print(f"[LEDs] SPI fallback also failed: {e}")

    def _ws2812_spi_encode(self, pixels):
        """Encode pixel data into WS2812 SPI format (fallback only)."""
        data = bytearray()
        for r, g, b in pixels:
            for byte in [g, r, b]:
                for bit in range(7, -1, -1):
                    if byte & (1 << bit):
                        data.extend(b'\x06')  # 110
                    else:
                        data.extend(b'\x04')  # 100
        data.extend(b'\x00' * 60)
        return data

    def show(self):
        """Send pixel data to the LED strip."""
        if not self._initialized:
            return

        try:
            if self._use_spi:
                # SPI fallback path
                scaled = []
                for r, g, b in self._pixels:
                    brightness = LED_BRIGHTNESS / 255.0
                    scaled.append((
                        int(r * brightness),
                        int(g * brightness),
                        int(b * brightness),
                    ))
                data = self._ws2812_spi_encode(scaled)
                self._spi.writebytes(data)
            else:
                # rpi_ws281x path
                brightness = LED_BRIGHTNESS / 255.0
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
        """Set a single pixel color."""
        if 0 <= index < LED_COUNT:
            self._pixels[index] = (r, g, b)

    def fill(self, r, g, b):
        """Fill all pixels with one color."""
        self._pixels = [(r, g, b)] * LED_COUNT
        self.show()

    def clear(self):
        """Turn off all LEDs."""
        self.fill(0, 0, 0)

    def set_mode(self, mode, color=(255, 0, 0)):
        """
        Set the light animation mode.

        Args:
            mode: 'breath', 'flowing', 'rainbow', 'police', 'colorWipe', 'solid', 'off'
            color: RGB tuple for modes that use it
        """
        self._mode = mode
        self._color = color
        self._flag.set()

        if mode == "off":
            self.clear()
            self._flag.clear()
        elif mode == "solid":
            self.fill(*color)
            self._flag.clear()
        else:
            self._flag.set()

    def _run(self):
        """Animation thread main loop."""
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
        """Pulsing brightness animation."""
        r, g, b = self._color
        while self._flag.is_set() and self._mode == "breath":
            for brightness in range(0, 256, 5):
                if not self._flag.is_set() or self._mode != "breath":
                    return
                scale = brightness / 255.0
                self.fill(int(r * scale), int(g * scale), int(b * scale))
                time.sleep(0.02)
            for brightness in range(255, -1, -5):
                if not self._flag.is_set() or self._mode != "breath":
                    return
                scale = brightness / 255.0
                self.fill(int(r * scale), int(g * scale), int(b * scale))
                time.sleep(0.02)

    def _animate_flowing(self):
        """Color cycling along the strip."""
        offset = 0
        while self._flag.is_set() and self._mode == "flowing":
            for i in range(LED_COUNT):
                hue = (i * 256 // LED_COUNT + offset) % 256
                self._pixels[i] = self._wheel(hue)
            self.show()
            offset = (offset + 1) % 256
            time.sleep(0.02)

    def _animate_rainbow(self):
        """Rainbow gradient animation."""
        offset = 0
        while self._flag.is_set() and self._mode == "rainbow":
            for i in range(LED_COUNT):
                hue = (i * 256 // LED_COUNT + offset) & 255
                self._pixels[i] = self._wheel(hue)
            self.show()
            offset = (offset + 2) % 256
            time.sleep(0.02)

    def _animate_police(self):
        """Red/blue alternating (police lights)."""
        half = LED_COUNT // 2
        while self._flag.is_set() and self._mode == "police":
            for i in range(half):
                self._pixels[i] = (255, 0, 0)
            for i in range(half, LED_COUNT):
                self._pixels[i] = (0, 0, 255)
            self.show()
            time.sleep(0.15)

            for i in range(half):
                self._pixels[i] = (0, 0, 255)
            for i in range(half, LED_COUNT):
                self._pixels[i] = (255, 0, 0)
            self.show()
            time.sleep(0.15)

    def _animate_color_wipe(self):
        """Sequential color fill animation."""
        r, g, b = self._color
        while self._flag.is_set() and self._mode == "colorWipe":
            for i in range(LED_COUNT):
                if not self._flag.is_set() or self._mode != "colorWipe":
                    return
                self._pixels[i] = (r, g, b)
                self.show()
                time.sleep(0.03)
            time.sleep(0.5)
            self.clear()
            time.sleep(0.2)

    @staticmethod
    def _wheel(pos):
        """Convert 0-255 position to RGB color (rainbow wheel)."""
        if pos < 85:
            return (pos * 3, 255 - pos * 3, 0)
        elif pos < 170:
            pos -= 85
            return (255 - pos * 3, 0, pos * 3)
        else:
            pos -= 170
            return (0, pos * 3, 255 - pos * 3)

    def shutdown(self):
        """Stop animations and turn off LEDs."""
        self._running = False
        self._flag.set()  # Unblock thread
        time.sleep(0.1)
        self.clear()
        if self._strip is not None:
            try:
                # rpi_ws281x cleanup
                pass
            except Exception:
                pass
        if hasattr(self, '_spi') and self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass
        print("[LEDs] Shutdown complete")
