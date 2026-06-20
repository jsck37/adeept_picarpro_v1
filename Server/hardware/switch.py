import threading, time
from Server.logger import logger
from config import SWITCH_PINS, HEADLIGHT_PIN

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False
    GPIO = None


class SwitchController:
    def __init__(self):
        self._leds = []
        self._states = [False] * len(SWITCH_PINS)
        self._headlight = None
        self._headlight_on = False
        self._initialized = False
        self._blink_threads = [None, None]
        self._blink_flags = [threading.Event(), threading.Event()]
        if not _HAS_GPIO:
            logger.warning('[Switch] RPi.GPIO not available')
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in SWITCH_PINS:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
                self._leds.append(pin)
            if HEADLIGHT_PIN is not None:
                GPIO.setup(HEADLIGHT_PIN, GPIO.OUT, initial=GPIO.LOW)
                self._headlight = HEADLIGHT_PIN
            self._initialized = True
            logger.info(f'[Switch] OK — {len(self._leds)} side + headlight')
        except Exception as e:
            logger.error(f'[Switch] init failed: {e}')

    def on(self, i):
        if 0 <= i < len(self._leds) and self._initialized:
            self.set_blinker('left' if i == 0 else 'right', False)
            GPIO.output(self._leds[i], GPIO.HIGH)
            self._states[i] = True

    def off(self, i):
        if 0 <= i < len(self._leds) and self._initialized:
            self.set_blinker('left' if i == 0 else 'right', False)
            GPIO.output(self._leds[i], GPIO.LOW)
            self._states[i] = False

    def get_state(self, i):
        return self._states[i] if 0 <= i < len(self._states) else False

    def headlight_on(self):
        if self._headlight is not None and self._initialized:
            GPIO.output(self._headlight, GPIO.HIGH)
            self._headlight_on = True

    def headlight_off(self):
        if self._headlight is not None and self._initialized:
            GPIO.output(self._headlight, GPIO.LOW)
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

    def set_blinker(self, side, active):
        idx = 0 if side == 'left' else 1 if side == 'right' else -1
        if idx < 0 or idx >= len(self._leds) or not self._initialized:
            return
        self._blink_flags[idx].clear()
        if self._blink_threads[idx] and self._blink_threads[idx].is_alive():
            self._blink_threads[idx].join(timeout=0.6)
        if active:
            self._blink_flags[idx].set()
            self._blink_threads[idx] = threading.Thread(
                target=self._blink_loop, args=(idx,), daemon=True)
            self._blink_threads[idx].start()
        else:
            try:
                GPIO.output(self._leds[idx], GPIO.LOW)
            except Exception:
                pass
            self._states[idx] = False

    def _blink_loop(self, idx):
        while self._blink_flags[idx].is_set():
            try:
                GPIO.output(self._leds[idx], GPIO.HIGH)
                self._states[idx] = True
            except Exception:
                pass
            time.sleep(0.4)
            if not self._blink_flags[idx].is_set():
                break
            try:
                GPIO.output(self._leds[idx], GPIO.LOW)
                self._states[idx] = False
            except Exception:
                pass
            time.sleep(0.4)

    def shutdown(self):
        for f in self._blink_flags:
            f.clear()
        for t in self._blink_threads:
            if t and t.is_alive():
                t.join(timeout=1.0)
        if self._initialized:
            for pin in self._leds:
                try:
                    GPIO.output(pin, GPIO.LOW)
                except Exception:
                    pass
            if self._headlight is not None:
                try:
                    GPIO.output(self._headlight, GPIO.LOW)
                except Exception:
                    pass
        logger.info('[Switch] shutdown')
