from Server.config import SWITCH_PINS
from Server.logger import logger

class SwitchController:
    def __init__(self):
        self._leds = []
        self._states = [False] * len(SWITCH_PINS)
        self._initialized = False
        try:
            from gpiozero import LED
            for pin in SWITCH_PINS:
                led = LED(pin)
                led.off()
                self._leds.append(led)
            self._initialized = True
            logger.info(f"[Switch] {len(self._leds)} switches OK")
        except Exception as e:
            logger.error(f"[Switch] Failed: {e}")

    def on(self, i):
        if 0 <= i < len(self._leds):
            self._leds[i].on()
            self._states[i] = True

    def off(self, i):
        if 0 <= i < len(self._leds):
            self._leds[i].off()
            self._states[i] = False

    def shutdown(self):
        for l in self._leds:
            try:
                l.off()
                l.close()
            except Exception:
                pass
        logger.info("[Switch] Shutdown")
