# PiCar Pro v1 — Optimized Mod

Fork of [Adeept PiCar Pro v1](https://github.com/adeept/adeept_picarpro) with clean architecture, bug fixes, and optional hardware support.

## Changes from Original

### Bug Fixes
- **MPU6050**: Added proper wake-up sequence (PWR_MGMT_1 = 0x00 + 100ms delay). The #1 reason it "doesn't work" is the chip ships in SLEEP mode. Also added WHO_AM_I verification, auto-retry on I2C errors, and background re-init.
- **Buzzer GPIO 24**: Added dual-driver support (RPi.GPIO PWM + gpiozero TonalBuzzer fallback). If one driver can't claim the pin, the other is tried. Also handles GPIO cleanup for pin conflicts.
- **Voice commands**: Removed references to non-existent servo channels 3/4/6. Now uses only the 3 available servos (0=steering, 1=cam_pan, 2=cam_tilt).
- **Buzzer scale module**: Fixed — was playing "beep" for every note instead of actual frequencies.
- **OLED scroll text**: Changed from personal message to generic "PiCar Pro v1".

### Architecture Improvements
- **Centralized config** with feature flags (`ULTRASONIC_ENABLED`, `LINE_TRACKER_ENABLED`)
- **Optional hardware**: Ultrasonic and line tracker can be disabled in config.py — the robot starts cleanly without them
- **Graceful degradation**: MPU6050, ultrasonic, buzzer — all handle missing hardware without crashing
- **Hardware status summary** printed at startup
- **Clean module metadata** with disabled module indicators

### Disabled Modules (no hardware)
- `ultrasonic.py` — HC-SR04 not present (set `ULTRASONIC_ENABLED=True` when you get one)
- `line_track.py` — IR tracker not present (set `LINE_TRACKER_ENABLED=True` when you get one)
- `battery.py` — ADS7830 ADC not present on v1 hardware

## Hardware Configuration

Edit `Server/config.py` to match your hardware:

```python
ULTRASONIC_ENABLED = False   # Set True when HC-SR04 is connected
LINE_TRACKER_ENABLED = False # Set True when IR line tracker is connected
```

## Installation

```bash
sudo python3 setup.py
```

Or manual:
```bash
sudo apt install i2c-tools python3-smbus python3-opencv python3-picamera2
pip3 install -r requirements.txt
sudo raspi-config  # Enable I2C, Camera
```

## Running

```bash
cd Server
python3 WebServer.py
```

Then open `http://<RASPBERRY_PI_IP>:5000` in a browser.

## Adding Line Tracker Later

When you get an IR line tracker module:
1. Connect to GPIO pins 20 (left), 16 (middle), 19 (right)
2. Set `LINE_TRACKER_ENABLED = True` in `Server/config.py`
3. Restart the server

## Adding Ultrasonic Sensor Later

When you get an HC-SR04:
1. Connect Trig to GPIO 11, Echo to GPIO 8
2. Set `ULTRASONIC_ENABLED = True` in `Server/config.py`
3. Restart the server
