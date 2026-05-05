"""Headlight switch control via gpiozero (GPIO 6, 13)."""

from Server.config import SWITCH_PINS


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
            print(f"[Switch] {len(self._leds)} switches (GPIO: {SWITCH_PINS})")
        except Exception as e:
            print(f"[Switch] Init failed: {e}")

    def on(self, switch_id):
        if 0 <= switch_id < len(self._leds):
            self._leds[switch_id].on()
            self._states[switch_id] = True

    def off(self, switch_id):
        if 0 <= switch_id < len(self._leds):
            self._leds[switch_id].off()
            self._states[switch_id] = False

    def toggle(self, switch_id):
        if 0 <= switch_id < len(self._leds):
            if self._states[switch_id]:
                self.off(switch_id)
            else:
                self.on(switch_id)

    def all_off(self):
        for i in range(len(self._leds)):
            self.off(i)

    def all_on(self):
        for i in range(len(self._leds)):
            self.on(i)

    def get_state(self, switch_id):
        if 0 <= switch_id < len(self._states):
            return self._states[switch_id]
        return False

    def shutdown(self):
        self.all_off()
        for led in self._leds:
            try:
                led.close()
            except Exception:
                pass
        print("[Switch] Shutdown")
