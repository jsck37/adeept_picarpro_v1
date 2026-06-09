import threading, time
from config import BUZZER_PIN, BUZZER_PASSIVE
from Server.logger import logger

NOTES = {
    'C3':131,'D3':147,'E3':165,'F3':175,'G3':196,'A3':220,'B3':247,
    'C4':262,'D4':294,'E4':330,'F4':349,'G4':392,'A4':440,'B4':494,
    'C5':523,'D5':587,'E5':659,'F5':698,'G5':784,'A5':880,'B5':988,
    'REST':0,
}
BIRTHDAY = [('C4',.3),('C4',.1),('D4',.4),('C4',.4),('F4',.4),('E4',.8),
            ('C4',.3),('C4',.1),('D4',.4),('C4',.4),('G4',.4),('F4',.8),
            ('C4',.3),('C4',.1),('C5',.4),('A4',.4),('F4',.4),('E4',.4),('D4',.8),
            ('B4',.3),('B4',.1),('A4',.4),('F4',.4),('G4',.4),('F4',.8)]

class BuzzerController:
    def __init__(self):
        self._running = True
        self._flag = threading.Event()
        self._thread = None
        self._initialized = False
        self._driver = None
        self._pwm = None
        self._GPIO = None
        self._pin = BUZZER_PIN
        self._freq = 0
        self._passive = BUZZER_PASSIVE
        self._try_rpi_gpio()
        if not self._initialized:
            self._try_gpiozero()

    def _try_rpi_gpio(self):
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self._pin, GPIO.OUT)
            if self._passive:
                self._pwm = GPIO.PWM(self._pin, 440)
                self._pwm.start(0)
            else:
                GPIO.output(self._pin, GPIO.LOW)
            self._GPIO = GPIO
            self._driver = 'rpigpio'
            self._initialized = True
            logger.info(f"[Buzzer] RPi.GPIO {'PWM' if self._passive else 'on/off'} on GPIO {self._pin}")
        except Exception:
            pass

    def _try_gpiozero(self):
        try:
            if self._passive:
                from gpiozero import TonalBuzzer
                self._buzzer_gz = TonalBuzzer(self._pin)
            else:
                from gpiozero import Buzzer
                self._buzzer_gz = Buzzer(self._pin)
            self._driver = 'gpiozero'
            self._initialized = True
            logger.info(f"[Buzzer] gpiozero on GPIO {self._pin}")
        except Exception:
            logger.warning(f"[Buzzer] NOT available on GPIO {self._pin}")

    def play_melody(self, name="beep"):
        if not self._initialized:
            return
        notes = {'happy_birthday': BIRTHDAY,
                 'beep': [('A4',.15),('REST',.1)]}.get(name)
        if not notes:
            return
        self._flag.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._flag.set()
        self._thread = threading.Thread(target=self._play, args=(notes,), daemon=True)
        self._thread.start()

    def _play(self, notes):
        try:
            for n, d in notes:
                if not self._flag.is_set():
                    break
                if isinstance(n, int):
                    freq = n
                elif n == 'REST' or n not in NOTES:
                    freq = 0
                else:
                    freq = NOTES[n]
                if freq <= 0:
                    self._off()
                else:
                    self._tone(freq)
                time.sleep(d)
        finally:
            self._off()

    def _tone(self, freq):
        if freq <= 0:
            self._off()
            return
        if self._driver == 'rpigpio' and self._pwm:
            try:
                if self._freq != freq:
                    self._pwm.ChangeFrequency(freq)
                    self._freq = freq
                self._pwm.ChangeDutyCycle(50)
            except Exception:
                pass
        elif self._driver == 'gpiozero':
            try:
                from gpiozero.tones import Tone
                self._buzzer_gz.play(Tone(freq))
            except Exception:
                pass

    def _off(self):
        if self._driver == 'rpigpio':
            try:
                if self._passive and self._pwm:
                    self._pwm.ChangeDutyCycle(0)
                    self._freq = 0
                elif self._GPIO:
                    self._GPIO.output(self._pin, self._GPIO.LOW)
            except Exception:
                pass
        elif self._driver == 'gpiozero':
            try:
                self._buzzer_gz.off()
            except Exception:
                pass

    def beep(self):
        self.play_melody("beep")

    def stop(self):
        self._flag.clear()
        self._off()

    def shutdown(self):
        self._running = False
        self.stop()
        if self._driver == 'rpigpio':
            if self._passive and self._pwm:
                try:
                    self._pwm.stop()
                except Exception:
                    pass
            if self._GPIO:
                try:
                    self._GPIO.cleanup(self._pin)
                except Exception:
                    pass
        elif self._driver == 'gpiozero':
            try:
                self._buzzer_gz.close()
            except Exception:
                pass
        logger.info("[Buzzer] Shutdown")
