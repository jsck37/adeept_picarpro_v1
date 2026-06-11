from config import SWITCH_PINS, HEADLIGHT_PIN
from Server.logger import logger

class SwitchController:
    def __init__(self):
        self._leds = []
        self._states = [False] * len(SWITCH_PINS)
        self._headlight = None
        self._headlight_on = False
        self._initialized = False
        try:
            from gpiozero import LED
            for pin in SWITCH_PINS:
                led = LED(pin)
                led.off()
                self._leds.append(led)
            if HEADLIGHT_PIN is not None:
                self._headlight = LED(HEADLIGHT_PIN)
                self._headlight.off()
            self._initialized = True
            logger.info(f"[Switch] {len(self._leds)} switches + headlight OK")
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

    def get_state(self, i):
        if 0 <= i < len(self._states):
            return self._states[i]
        return False

    def headlight_on(self):
        if self._headlight:
            self._headlight.on()
            self._headlight_on = True

    def headlight_off(self):
        if self._headlight:
            self._headlight.off()
            self._headlight_on = False

    def headlight_toggle(self):
        if self._headlight_on:
            self.headlight_off()
        else:
            self.headlight_on()
        return self._headlight_on

    @property
    def headlight_state(self):
        return self._headlight_on

    def shutdown(self):
        for l in self._leds:
            try:
                l.off()
                l.close()
            except Exception:
                pass
        if self._headlight:
            try:
                self._headlight.off()
                self._headlight.close()
            except Exception:
                pass
        logger.info("[Switch] Shutdown")
