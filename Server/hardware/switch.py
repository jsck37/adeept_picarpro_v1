"""
GPIO switch/LED control module.
Controls 2 headlight switches (GPIO 6, 13).
GPIO 5 is reserved for the buzzer (RobotHat 3-pin port 0/3).
"""

from Server.config import SWITCH_PINS


class SwitchController:
    """2-port LED/headlight switch controller via gpiozero (GPIO 6, 13).
    Note: GPIO 5 is reserved for the buzzer (RobotHat port 0/3)."""

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
            print(f"[Switch] {len(self._leds)} switches initialized (GPIO: {SWITCH_PINS})")
        except Exception as e:
            print(f"[Switch] Failed to initialize: {e}")

    def on(self, switch_id):
        """Turn on a switch (0-2)."""
        if 0 <= switch_id < len(self._leds):
            self._leds[switch_id].on()
            self._states[switch_id] = True

    def off(self, switch_id):
        """Turn off a switch (0-2)."""
        if 0 <= switch_id < len(self._leds):
            self._leds[switch_id].off()
            self._states[switch_id] = False

    def toggle(self, switch_id):
        """Toggle a switch."""
        if 0 <= switch_id < len(self._leds):
            if self._states[switch_id]:
                self.off(switch_id)
            else:
                self.on(switch_id)

    def all_off(self):
        """Turn off all switches."""
        for i in range(len(self._leds)):
            self.off(i)

    def all_on(self):
        """Turn on all switches."""
        for i in range(len(self._leds)):
            self.on(i)

    def get_state(self, switch_id):
        """Get the current state of a switch."""
        if 0 <= switch_id < len(self._states):
            return self._states[switch_id]
        return False

    def shutdown(self):
        """Turn off all switches."""
        self.all_off()
        for led in self._leds:
            try:
                led.close()
            except Exception:
                pass
        print("[Switch] Shutdown complete")
