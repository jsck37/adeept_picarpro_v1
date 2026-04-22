"""
WS2812 RGB LED strip control via SPI.
Backported from v2: uses spidev instead of rpi_ws281x for better Pi 5 compatibility.

v1 used rpi_ws281x which requires DMA and kernel module - problematic on newer kernels.
v2 uses SPI (spidev) which is more reliable and compatible.
"""

import time
import threading
from Server.config import LED_COUNT, LED_BRIGHTNESS, LED_SPI_BUS, LED_SPI_DEVICE


class LEDController:
    """
    WS2812 LED strip controller using SPI.
    
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
        self._running = True
        self._mode = "solid"
        self._color = (255, 0, 0)  # Default red
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._flag = threading.Event()
        self._flag.set()
        self._initialized = False

        self._init_spi()

    def _init_spi(self):
        """Initialize SPI for WS2812 communication."""
        try:
            import spidev

            self._spi = spidev.SpiDev()
            self._spi.open(LED_SPI_BUS, LED_SPI_DEVICE)
            self._spi.max_speed_hz = 4000000  # WS2812 requires ~3.2MHz
            self._spi.mode = 0

            self._pixels = [(0, 0, 0)] * LED_COUNT
            self._initialized = True
            print(f"[LEDs] SPI WS2812 initialized: {LED_COUNT} LEDs")

            # Start animation thread
            self._thread.start()

        except Exception as e:
            print(f"[LEDs] Failed to initialize SPI: {e}")
            print("[LEDs] Try: sudo raspi-config -> Interface Options -> SPI -> Enable")

    def _ws2812_encode(self, pixels):
        """Encode pixel data into WS2812 SPI format."""
        data = bytearray()
        for r, g, b in pixels:
            # WS2812 expects GRB order
            for byte in [g, r, b]:
                # Each bit becomes 3 SPI bits: 1=110, 0=100
                for bit in range(7, -1, -1):
                    if byte & (1 << bit):
                        data.extend(b'\x06')  # 110
                    else:
                        data.extend(b'\x04')  # 100
        # Reset signal (low for >50us)
        data.extend(b'\x00' * 60)
        return data

    def show(self):
        """Send pixel data to the LED strip."""
        if not self._initialized:
            return

        try:
            scaled = []
            for r, g, b in self._pixels:
                brightness = LED_BRIGHTNESS / 255.0
                scaled.append((
                    int(r * brightness),
                    int(g * brightness),
                    int(b * brightness),
                ))

            data = self._ws2812_encode(scaled)
            self._spi.writebytes(data)
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
            # Red on left, blue on right
            for i in range(half):
                self._pixels[i] = (255, 0, 0)
            for i in range(half, LED_COUNT):
                self._pixels[i] = (0, 0, 255)
            self.show()
            time.sleep(0.15)

            # Swap
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
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass
        print("[LEDs] Shutdown complete")
