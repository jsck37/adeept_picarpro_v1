import threading, time
from config import SWITCH_PINS, HEADLIGHT_PIN
from Server.logger import logger


class SwitchController:
    """Headlight + side-light + blinker controller.

    Exposes:
      * SWITCH_PINS[0]   -> left side light / left blinker
      * SWITCH_PINS[1]   -> right side light / right blinker
      * HEADLIGHT_PIN    -> main headlight (robot-hat pin 5)

    Side lights can be driven two ways:
      * on(i)/off(i)   — direct, persistent on/off (no blinking)
      * set_blinker(side, active) — starts a server-side blink thread
        that toggles the corresponding side light at 0.4s intervals.
        Calling set_blinker(side, False) cancels the blink thread and
        turns the light off.
    """

    def __init__(self):
        self._leds = []
        self._states = [False] * len(SWITCH_PINS)
        self._headlight = None
        self._headlight_on = False
        self._initialized = False
        self._blink_threads = [None, None]
        self._blink_flags = [threading.Event(), threading.Event()]
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

    # ---- direct side-light control ------------------------------------
    def on(self, i):
        if 0 <= i < len(self._leds):
            # Stop any blinker on this side first.
            self.set_blinker('left' if i == 0 else 'right', False)
            self._leds[i].on()
            self._states[i] = True

    def off(self, i):
        if 0 <= i < len(self._leds):
            self.set_blinker('left' if i == 0 else 'right', False)
            self._leds[i].off()
            self._states[i] = False

    def get_state(self, i):
        if 0 <= i < len(self._states):
            return self._states[i]
        return False

    # ---- headlight ----------------------------------------------------
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

    # ---- blinker (server-side) ----------------------------------------
    def set_blinker(self, side, active):
        """Start or stop a blinker on the given side ('left' / 'right').

        When active=True, the corresponding side light toggles every
        ~400 ms until set_blinker(side, False) is called. The blink
        thread also clears the light when stopped.
        """
        idx = 0 if side == 'left' else 1 if side == 'right' else -1
        if idx < 0 or idx >= len(self._leds):
            return

        # Always signal stop first so any running thread exits cleanly.
        self._blink_flags[idx].clear()
        if self._blink_threads[idx] and self._blink_threads[idx].is_alive():
            # Wait briefly for it to notice and exit.
            self._blink_threads[idx].join(timeout=0.6)

        if active:
            self._blink_flags[idx].set()
            self._blink_threads[idx] = threading.Thread(
                target=self._blink_loop, args=(idx,), daemon=True
            )
            self._blink_threads[idx].start()
        else:
            # Ensure the light is off when we stop blinking.
            try:
                self._leds[idx].off()
            except Exception:
                pass
            self._states[idx] = False

    def _blink_loop(self, idx):
        while self._blink_flags[idx].is_set():
            try:
                self._leds[idx].on()
                self._states[idx] = True
            except Exception:
                pass
            time.sleep(0.4)
            if not self._blink_flags[idx].is_set():
                break
            try:
                self._leds[idx].off()
                self._states[idx] = False
            except Exception:
                pass
            time.sleep(0.4)

    # ---- shutdown ------------------------------------------------------
    def shutdown(self):
        for f in self._blink_flags:
            f.clear()
        for t in self._blink_threads:
            if t and t.is_alive():
                try:
                    t.join(timeout=1.0)
                except Exception:
                    pass
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
