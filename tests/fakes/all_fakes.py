"""Фейки железа для pytest на обычном ПК (без Raspberry Pi).

install() подменяет в sys.modules: RPi.GPIO, smbus2, spidev, picamera2,
evdev, busio, adafruit_pca9685, adafruit_motor, luma.*, cv2.
После этого все контроллеры picarpro считают, что железо присутствует,
и вся логика становится тестируемой.
"""
import sys
import time
import types

import numpy as np


# ===========================================================================
# RPi.GPIO
# ===========================================================================
class FakePWM:
    def __init__(self, gpio, pin, frequency):
        self._gpio = gpio
        self.pin = pin
        self.frequency = frequency
        self.duty_cycle = 0.0
        self.started = False
        self.stopped = False

    def start(self, duty=0.0):
        self.started = True
        self.duty_cycle = duty

    def ChangeDutyCycle(self, duty):
        self.duty_cycle = max(0.0, min(100.0, float(duty)))

    def ChangeFrequency(self, freq):
        self.frequency = freq

    def stop(self):
        self.stopped = True


class FakeGPIO:
    BCM = 'BCM'
    OUT = 'out'
    IN = 'in'
    HIGH = 1
    LOW = 0
    PUD_DOWN = 'pud_down'
    PUD_UP = 'pud_up'

    def __init__(self):
        self.modes = {}          # pin -> OUT/IN
        self.outputs = {}        # pin -> 0/1
        self.input_values = {}   # pin -> статическое значение
        self.pwms = []           # все созданные PWM
        self.mode_set = None
        self.fail_setup = False
        self.output_log = []     # [(pin, value, time)]
        # HC-SR04 echo script
        self.echo_pin = None
        self.trigger_pin = None
        self._trig_rise_ts = None
        self._echo_high_from = None
        self._echo_high_to = None
        self._echo_wave_s = 0.0

    # --- базовые вызовы ----------------------------------------------------
    def setmode(self, mode):
        self.mode_set = mode

    def setwarnings(self, flag):
        pass

    def setup(self, pin, direction, initial=None, pull_up_down=None):
        if self.fail_setup:
            raise OSError('fake setup failure')
        self.modes[pin] = direction
        if direction == self.OUT:
            self.outputs[pin] = initial if initial is not None else self.LOW
        else:
            self.input_values.setdefault(pin, self.LOW)

    def output(self, pin, value):
        self.outputs[pin] = value
        self.output_log.append((pin, value, time.monotonic()))
        # имитация HC-SR04: после спада TRIG запускается эхо-импульс
        if self.trigger_pin is not None and pin == self.trigger_pin:
            if value == self.HIGH:
                self._trig_rise_ts = time.monotonic()
            elif value == self.LOW and self._trig_rise_ts is not None:
                self._echo_high_from = time.monotonic() + 0.0005
                self._echo_high_to = self._echo_high_from + self._echo_wave_s

    def input(self, pin):
        if pin == self.echo_pin and self._echo_high_from is not None:
            now = time.monotonic()
            if self._echo_high_from <= now <= self._echo_high_to:
                return self.HIGH
            return self.LOW
        return self.input_values.get(pin, self.LOW)

    def cleanup(self):
        pass

    def PWM(self, pin, frequency):
        pwm = FakePWM(self, pin, frequency)
        self.pwms.append(pwm)
        return pwm

    # --- вспомогательное для тестов ----------------------------------------
    def reset(self):
        self.modes.clear()
        self.outputs.clear()
        self.input_values.clear()
        self.pwms = []
        self.output_log = []
        self.fail_setup = False
        self.echo_pin = None
        self.trigger_pin = None
        self._trig_rise_ts = None
        self._echo_high_from = None
        self._echo_high_to = None
        self._echo_wave_s = 0.0

    def set_input(self, pin, value):
        self.input_values[pin] = value

    def arm_ultrasonic(self, trigger_pin, echo_pin, wave_seconds=0.00088):
        """wave 0.88 мс ~ 15 см (distance = wave_s * 17150 * 100 / 2 ... см.
        ultrasonic.py: distance = duration * 17150 (при duration в сек))."""
        self.trigger_pin = trigger_pin
        self.echo_pin = echo_pin
        self._echo_wave_s = wave_seconds
        self._echo_high_from = None

    def pwm_for(self, pin):
        for p in self.pwms:
            if p.pin == pin:
                return p
        return None


# ===========================================================================
# smbus2
# ===========================================================================
class FakeSMBus:
    """I2C-шина с реестром устройств: {addr: {reg: value}}.
    Отсутствующее устройство -> OSError (как в реальности)."""

    shared_devices = {}

    def __init__(self, bus_id=1):
        self.bus_id = bus_id
        self.closed = False

    @classmethod
    def reset(cls):
        cls.shared_devices = {}

    def _dev(self, addr):
        if self.closed:
            raise OSError('bus closed')
        if addr not in FakeSMBus.shared_devices:
            raise OSError(f'no device at 0x{addr:02X}')
        return FakeSMBus.shared_devices[addr]

    def read_byte(self, addr):
        dev = self._dev(addr)
        return dev.get(0, 0)

    def write_byte(self, addr, value):
        dev = self._dev(addr)
        dev[0] = value & 0xFF

    def read_byte_data(self, addr, reg):
        dev = self._dev(addr)
        return dev.get(reg & 0xFF, 0)

    def write_byte_data(self, addr, reg, value):
        dev = self._dev(addr)
        dev[reg & 0xFF] = value & 0xFF

    def read_i2c_block_data(self, addr, reg, length):
        dev = self._dev(addr)
        return [dev.get((reg + i) & 0xFF, 0) for i in range(length)]

    def write_i2c_block_data(self, addr, reg, values):
        dev = self._dev(addr)
        for i, v in enumerate(values):
            dev[(reg + i) & 0xFF] = v & 0xFF

    def close(self):
        self.closed = True


def make_mpu6050_device(addr=0x68, who=0x68):
    """Заполняет регистры MPU6050: who_am_i + акселерометр/гироскоп в нуле."""
    regs = {0x75: who}
    FakeSMBus.shared_devices[addr] = regs
    return regs


def set_mpu6050_sample(regs, ax=0, ay=0, az=16384, gx=0, gy=0, gz=0, temp_raw=0):
    def put16(reg, value):
        value = int(value) & 0xFFFF
        regs[reg] = (value >> 8) & 0xFF
        regs[reg + 1] = value & 0xFF
    put16(0x3B, ax)
    put16(0x3D, ay)
    put16(0x3F, az)
    put16(0x41, temp_raw)
    put16(0x43, gx)
    put16(0x45, gy)
    put16(0x47, gz)


def make_ads7830_device(addr=0x48, channel_value=200):
    """ADS7830: значение канала задаётся как {0x00..0x07: value} (cmd=канал)."""
    regs = {i: 0 for i in range(256)}
    FakeSMBus.shared_devices[addr] = regs
    return regs


# ===========================================================================
# spidev
# ===========================================================================
class FakeSpiDev:
    instances = []

    def __init__(self):
        self.opened = False
        self.closed = False
        self.written = []
        self.max_speed_hz = None
        self.mode = None
        FakeSpiDev.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    def open(self, bus, dev):
        self.opened = True
        self.bus, self.dev = bus, dev

    def writebytes(self, data):
        if not self.opened or self.closed:
            raise OSError('spi not open')
        self.written.append(bytes(data))

    def close(self):
        self.closed = True


# ===========================================================================
# picamera2
# ===========================================================================
class FakePicamera2:
    """Управляемая фейковая камера: классовый кадр + режим отказа."""
    frame = None            # numpy array (H, W, 3) uint8
    fail_capture_after = None
    fail_start = False

    instance = None

    def __init__(self):
        self.configured = False
        self.started = False
        self.closed = False
        self.controls = {}
        self._captures = 0
        FakePicamera2.instance = self

    def create_preview_configuration(self, main=None, **kw):
        return {'main': main}

    def configure(self, cfg):
        self.configured = True
        self.config = cfg

    def set_control(self, name, value):
        self.controls[name] = value

    def set_controls(self, controls):
        self.controls.update(controls)

    def start(self):
        if FakePicamera2.fail_start:
            raise RuntimeError('fake camera start failure')
        self.started = True

    def capture_array(self):
        self._captures += 1
        if (FakePicamera2.fail_capture_after is not None
                and self._captures > FakePicamera2.fail_capture_after):
            return None
        frame = FakePicamera2.frame
        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
        return frame.copy()

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


# ===========================================================================
# evdev (DS4)
# ===========================================================================
class FakeEcodes:
    EV_SYN = 0
    EV_KEY = 1
    EV_ABS = 3
    ABS_X = 0
    ABS_Y = 1
    ABS_Z = 2
    ABS_RX = 3
    ABS_RY = 4
    ABS_RZ = 5
    ABS_THROTTLE = 6
    ABS_BRAKE = 7
    ABS_HAT0X = 16
    ABS_HAT0Y = 17
    BTN_SOUTH = 0x130
    BTN_A = 0x130
    BTN_EAST = 0x131
    BTN_B = 0x131
    BTN_C = 0x132
    BTN_NORTH = 0x133
    BTN_X = 0x133
    BTN_WEST = 0x134
    BTN_Y = 0x134
    BTN_TL = 0x136
    BTN_TR = 0x137
    BTN_TL2 = 0x138
    BTN_TR2 = 0x139
    BTN_SELECT = 0x13A
    BTN_START = 0x13B
    BTN_MODE = 0x13C


class FakeAbsInfo:
    def __init__(self, min_=0, max_=255):
        self.min = min_
        self.max = max_


class FakeInputDevice:
    devices = {}          # path -> FakeInputDevice
    next_fd = 10

    def __init__(self, path=None, name='Wireless Controller'):
        self.path = path or '/dev/input/event9'
        self.name = name
        self.fd = FakeInputDevice.next_fd
        FakeInputDevice.next_fd += 1
        self.grabbed = False
        self.closed = False
        self.event_queue = []
        self._abs_caps = {
            FakeEcodes.ABS_X: FakeAbsInfo(0, 255),
            FakeEcodes.ABS_Y: FakeAbsInfo(0, 255),
            FakeEcodes.ABS_RX: FakeAbsInfo(0, 255),
            FakeEcodes.ABS_RY: FakeAbsInfo(0, 255),
            FakeEcodes.ABS_Z: FakeAbsInfo(0, 255),
            FakeEcodes.ABS_RZ: FakeAbsInfo(0, 255),
            FakeEcodes.ABS_HAT0X: FakeAbsInfo(-1, 1),
            FakeEcodes.ABS_HAT0Y: FakeAbsInfo(-1, 1),
        }
        FakeInputDevice.devices[self.path] = self

    def capabilities(self, absinfo=False):
        if not absinfo:
            return {FakeEcodes.EV_KEY: list(FakeEcodes.__dict__.values()
                                            if False else [])}
        return {
            FakeEcodes.EV_ABS: [(code, self._abs_caps[code])
                                for code in self._abs_caps],
            FakeEcodes.EV_KEY: [FakeEcodes.BTN_SOUTH, FakeEcodes.BTN_EAST,
                                FakeEcodes.BTN_NORTH, FakeEcodes.BTN_WEST,
                                FakeEcodes.BTN_TL, FakeEcodes.BTN_TR,
                                FakeEcodes.BTN_TL2, FakeEcodes.BTN_TR2,
                                FakeEcodes.BTN_SELECT, FakeEcodes.BTN_START,
                                FakeEcodes.BTN_MODE],
        }

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        self.closed = True
        FakeInputDevice.devices.pop(self.path, None)

    def read(self):
        events, self.event_queue = self.event_queue, []
        return events

    def push(self, ev_type, code, value):
        from collections import namedtuple
        Ev = namedtuple('InputEvent', 'type code value')
        self.event_queue.append(Ev(ev_type, code, value))

    @staticmethod
    def reset():
        FakeInputDevice.devices = {}


def fake_list_devices():
    return list(FakeInputDevice.devices.keys())


# ===========================================================================
# busio / adafruit_pca9685 / adafruit_motor
# ===========================================================================
PCA_PWM_PERIOD = 4096


class FakeChannel:
    def __init__(self, pca, index):
        self._pca = pca
        self.index = index
        self._duty = 0

    @property
    def duty_cycle(self):
        return self._duty

    @duty_cycle.setter
    def duty_cycle(self, value):
        self._duty = max(0, min(0xFFFF, int(value)))


class FakePCA9685:
    instances = []

    def __init__(self, i2c=None, address=None):
        self.address = address
        self._frequency = None
        self.channels = [FakeChannel(self, i) for i in range(16)]
        self.deinited = False
        FakePCA9685.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    @property
    def frequency(self):
        return self._frequency

    @frequency.setter
    def frequency(self, value):
        self._frequency = value

    def deinit(self):
        self.deinited = True


class FakeI2C:
    instances = []

    def __init__(self, scl=None, sda=None):
        self.scl, self.sda = scl, sda
        FakeI2C.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    def deinit(self):
        pass


class FakeAdafruitServo:
    """Servo-объект adafruit_motor: угол -> duty_cycle канала PCA9685."""
    instances = []

    def __init__(self, channel, min_pulse=500, max_pulse=2400,
                 actuation_range=180):
        self.channel = channel
        self.min_pulse = min_pulse
        self.max_pulse = max_pulse
        self.actuation_range = actuation_range
        self._angle = None
        FakeAdafruitServo.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    @property
    def angle(self):
        return self._angle

    @angle.setter
    def angle(self, value):
        if value is None:
            self._angle = None
            return
        if value < 0 or value > self.actuation_range:
            raise ValueError(f'angle {value} out of range 0..{self.actuation_range}')
        self._angle = float(value)
        pulse_us = self.min_pulse + (self.max_pulse - self.min_pulse) \
            * (float(value) / self.actuation_range)
        duty = int(pulse_us / 20000.0 * 0xFFFF)
        self.channel.duty_cycle = duty


# ===========================================================================
# luma.oled (SSD1306)
# ===========================================================================
class FakeLumaI2C:
    instances = []

    def __init__(self, port=None, address=None):
        self.port, self.address = port, address
        FakeLumaI2C.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []


class FakeSSD1306:
    instances = []

    def __init__(self, serial=None, width=None, height=None):
        self.serial = serial
        self.width = width
        self.height = height
        self.frames = []
        self.cleaned = False
        FakeSSD1306.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    def display(self, img):
        self.frames.append(img)

    def cleanup(self):
        self.cleaned = True


# ===========================================================================
# cv2 (минимальный numpy-бэкенд)
# ===========================================================================
class FakeCV2:
    COLOR_RGB2BGR = 'RGB2BGR'
    COLOR_BGR2RGB = 'BGR2RGB'
    COLOR_BGR2GRAY = 'BGR2GRAY'
    COLOR_RGB2GRAY = 'RGB2GRAY'
    COLOR_GRAY2BGR = 'GRAY2BGR'
    COLOR_BGR2HSV = 'BGR2HSV'
    COLOR_HSV2BGR = 'HSV2BGR'
    MORPH_RECT = 1
    MORPH_ELLIPSE = 2
    MORPH_OPEN = 3
    MORPH_CLOSE = 4
    THRESH_BINARY = 0
    THRESH_BINARY_INV = 1
    THRESH_OTSU = 8  # как в OpenCV (1<<3)
    RETR_EXTERNAL = 'external'
    CHAIN_APPROX_SIMPLE = 'simple'
    FONT_HERSHEY_SIMPLEX = 0
    IMWRITE_JPEG_QUALITY = 1

    # ---- color ----
    @classmethod
    def cvtColor(cls, src, code):
        src = np.asarray(src)
        if code in (cls.COLOR_RGB2BGR, cls.COLOR_BGR2RGB):
            return src[..., ::-1].copy()
        if code in (cls.COLOR_BGR2GRAY, cls.COLOR_RGB2GRAY):
            weights = [0.299, 0.587, 0.114]
            gray = sum(src[..., i].astype(np.float32) * w
                       for i, w in enumerate(weights))
            return np.clip(gray, 0, 255).astype(np.uint8)
        if code == cls.COLOR_GRAY2BGR:
            return np.stack([src, src, src], axis=-1).astype(np.uint8)
        if code == cls.COLOR_BGR2HSV:
            return cls._bgr2hsv(src)
        if code == cls.COLOR_HSV2BGR:
            return cls._hsv2bgr(src)
        raise ValueError(f'unknown code {code}')

    @staticmethod
    def _bgr2hsv(src):
        b = src[..., 0].astype(np.float32) / 255.0
        g = src[..., 1].astype(np.float32) / 255.0
        r = src[..., 2].astype(np.float32) / 255.0
        maxc = np.max([r, g, b], axis=0)
        minc = np.min([r, g, b], axis=0)
        v = maxc
        delta = maxc - minc
        s = np.where(maxc > 0, delta / np.maximum(maxc, 1e-9), 0)
        h = np.zeros_like(maxc)
        mask = delta > 1e-9
        rm = mask & (maxc == r)
        gm = mask & (maxc == g) & ~rm
        bm = mask & (maxc == b) & ~rm & ~gm
        h[rm] = ((g - b)[rm] / delta[rm]) % 6
        h[gm] = ((b - r)[gm] / delta[gm]) + 2
        h[bm] = ((r - g)[bm] / delta[bm]) + 4
        h = (h * 30).astype(np.uint8)  # 0..180 как в OpenCV
        return np.stack([
            h,
            (s * 255).astype(np.uint8),
            (v * 255).astype(np.uint8),
        ], axis=-1)

    @staticmethod
    def _hsv2bgr(src):
        return np.zeros_like(src)

    # ---- filters ----
    @classmethod
    def GaussianBlur(cls, src, ksize, sigma):
        k = ksize[0]
        if k <= 1:
            return src.copy()
        pad = k // 2
        if src.ndim == 2:
            padded = np.pad(src, pad, mode='edge').astype(np.float32)
            out = np.zeros_like(src, dtype=np.float32)
            for dy in range(k):
                for dx in range(k):
                    out += padded[dy:dy + src.shape[0], dx:dx + src.shape[1]]
            return np.clip(out / (k * k), 0, 255).astype(np.uint8)
        return np.stack([cls.GaussianBlur(src[..., i], ksize, sigma)
                         for i in range(src.shape[-1])], axis=-1)

    @classmethod
    def threshold(cls, src, thresh, maxval, type_):
        otsu = bool(type_ & cls.THRESH_OTSU)
        base = type_ & ~cls.THRESH_OTSU
        if otsu:
            thresh = cls._otsu(src)
        if base == cls.THRESH_BINARY:
            dst = np.where(src > thresh, maxval, 0).astype(np.uint8)
        elif base == cls.THRESH_BINARY_INV:
            dst = np.where(src <= thresh, maxval, 0).astype(np.uint8)
        else:
            raise ValueError(f'unsupported threshold type {type_}')
        return thresh, dst

    @staticmethod
    def _otsu(src):
        hist, _ = np.histogram(src, bins=256, range=(0, 256))
        total = src.size
        sum_all = np.dot(np.arange(256), hist)
        sum_b = 0.0
        w_b = 0
        best_t, best_var = 0, -1.0
        for t in range(256):
            w_b += hist[t]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * hist[t]
            m_b = sum_b / w_b
            m_f = (sum_all - sum_b) / w_f
            var = w_b * w_f * (m_b - m_f) ** 2
            if var > best_var:
                best_var = var
                best_t = t
        return best_t

    @classmethod
    def absdiff(cls, a, b):
        return np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.uint8)

    @classmethod
    def subtract(cls, a, b):
        d = a.astype(np.int16) - b.astype(np.int16)
        return np.clip(d, 0, 255).astype(np.uint8)

    # ---- morphology ----
    @staticmethod
    def getStructuringElement(shape, ksize):
        return np.ones(ksize, dtype=np.uint8)

    @classmethod
    def morphologyEx(cls, src, op, kernel, iterations=1):
        k_h, k_w = kernel.shape
        out = src.copy()
        for _ in range(max(1, iterations)):
            if op == cls.MORPH_OPEN:
                out = cls._erode(out, k_h, k_w)
                out = cls._dilate(out, k_h, k_w)
            elif op == cls.MORPH_CLOSE:
                out = cls._dilate(out, k_h, k_w)
                out = cls._erode(out, k_h, k_w)
            else:
                raise ValueError(f'unsupported morph op {op}')
        return out

    @staticmethod
    def _erode(src, kh, kw):
        ph, pw = kh // 2, kw // 2
        padded = np.pad(src, ((ph, ph), (pw, pw)), mode='constant')
        h, w = src.shape
        out = np.full((h, w), 255, dtype=np.uint8)
        for dy in range(kh):
            for dx in range(kw):
                out = np.minimum(out, padded[dy:dy + h, dx:dx + w])
        return out

    @staticmethod
    def _dilate(src, kh, kw):
        ph, pw = kh // 2, kw // 2
        padded = np.pad(src, ((ph, ph), (pw, pw)), mode='constant')
        h, w = src.shape
        out = np.zeros((h, w), dtype=np.uint8)
        for dy in range(kh):
            for dx in range(kw):
                out = np.maximum(out, padded[dy:dy + h, dx:dx + w])
        return out

    @classmethod
    def inRange(cls, hsv, low, high):
        low = np.asarray(low).reshape(3)
        high = np.asarray(high).reshape(3)
        mask = np.ones(hsv.shape[:2], dtype=bool)
        for i in range(3):
            mask &= (hsv[..., i].astype(np.int16) >= low[i]) & \
                    (hsv[..., i].astype(np.int16) <= high[i])
        return np.where(mask, 255, 0).astype(np.uint8)

    @staticmethod
    def bitwise_or(a, b):
        return np.bitwise_or(a, b)

    # ---- contours ----
    @classmethod
    def findContours(cls, mask, mode, method):
        """Компоненты связности (4-связность) -> контуры как bbox-полигоны."""
        m = mask > 0
        h, w = m.shape
        labels = np.zeros((h, w), dtype=np.int32)
        current = 0
        contours = []
        for y in range(h):
            xs = np.nonzero(m[y] & (labels[y] == 0))[0]
            for x in xs:
                if labels[y, x]:
                    continue
                current += 1
                # BFS
                stack = [(y, x)]
                labels[y, x] = current
                pts = []
                while stack:
                    cy, cx = stack.pop()
                    pts.append((cx, cy))
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx),
                                   (cy, cx - 1), (cy, cx + 1)):
                        if (0 <= ny < h and 0 <= nx < w and m[ny, nx]
                                and labels[ny, nx] == 0):
                            labels[ny, nx] = current
                            stack.append((ny, nx))
                pts_arr = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
                contours.append(pts_arr)
        return [None], contours, None  # совместимо с [-2] индексацией

    @staticmethod
    def contourArea(contour):
        pts = contour.reshape(-1, 2).astype(np.float64)
        if len(pts) < 3:
            return 0.0
        x, y = pts[:, 0], pts[:, 1]
        # площадь по bbox компоненты (устойчиво для связных областей)
        return float((x.max() - x.min() + 1) * (y.max() - y.min() + 1))

    @classmethod
    def moments(cls, contour):
        pts = contour.reshape(-1, 2).astype(np.float64)
        n = max(1, len(pts))
        m00 = float(n)
        m10 = float(pts[:, 0].sum())
        m01 = float(pts[:, 1].sum())
        return {'m00': m00, 'm10': m10, 'm01': m01}

    @classmethod
    def drawContours(cls, img, contours, idx, color, thickness):
        return img

    # ---- drawing ----
    @staticmethod
    def line(img, p1, p2, color, thickness=1):
        x1, y1 = int(p1[0]), int(p1[1])
        x2, y2 = int(p2[0]), int(p2[1])
        n = max(abs(x2 - x1), abs(y2 - y1)) + 1
        for i in range(n):
            x = x1 + (x2 - x1) * i // max(1, n - 1) if n > 1 else x1
            y = y1 + (y2 - y1) * i // max(1, n - 1) if n > 1 else y1
            if 0 <= y < img.shape[0] and 0 <= x < img.shape[1]:
                img[y, x] = color
        return img

    @staticmethod
    def circle(img, center, radius, color, thickness=1):
        cx, cy = int(center[0]), int(center[1])
        if thickness < 0:
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if dx * dx + dy * dy <= radius * radius:
                        y, x = cy + dy, cx + dx
                        if 0 <= y < img.shape[0] and 0 <= x < img.shape[1]:
                            img[y, x] = color
        return img

    @staticmethod
    def putText(img, text, org, font, scale, color, thickness=1):
        return img

    @staticmethod
    def imencode(ext, frame, params=None):
        header = b'\xff\xd8FAKE'
        body = str(tuple(np.asarray(frame).shape)).encode() + b'END'
        return True, np.frombuffer(header + body, dtype=np.uint8)


# ===========================================================================
# установка фейков в sys.modules
# ===========================================================================
def install():
    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    gpio = FakeGPIO()
    mod('RPi', GPIO=gpio)
    sys.modules['RPi.GPIO'] = gpio

    mod('smbus2', SMBus=FakeSMBus)
    mod('spidev', SpiDev=FakeSpiDev)
    mod('picamera2', Picamera2=FakePicamera2)

    evdev = mod('evdev', InputDevice=FakeInputDevice,
                list_devices=fake_list_devices)
    evdev.ecodes = FakeEcodes
    sys.modules['evdev.ecodes'] = mod('ecodes_unused')

    busio_mod = mod('busio', I2C=FakeI2C)
    mod('adafruit_pca9685', PCA9685=FakePCA9685)
    motor_mod = mod('adafruit_motor')
    motor_mod.servo = types.SimpleNamespace(Servo=FakeAdafruitServo)
    sys.modules['adafruit_motor.servo'] = mod(
        'adafruit_motor.servo_inner', Servo=FakeAdafruitServo)

    mod('luma', core=None)
    luma_core = mod('luma.core')
    luma_core.interface = types.SimpleNamespace()
    sys.modules['luma.core.interface'] = mod('luma.core.interface.dummy')
    sys.modules['luma.core.interface.serial'] = mod(
        'luma.core.interface.serial', i2c=FakeLumaI2C)
    luma_oled = mod('luma.oled')
    sys.modules['luma.oled.device'] = mod('luma.oled.device',
                                          ssd1306=FakeSSD1306)

    sys.modules['cv2'] = FakeCV2

    return {
        'gpio': gpio,
        'smbus': FakeSMBus,
        'spi': FakeSpiDev,
        'picamera2': FakePicamera2,
        'pca9685': FakePCA9685,
        'servo': FakeAdafruitServo,
        'ssd1306': FakeSSD1306,
    }
