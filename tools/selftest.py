#!/usr/bin/env python3
"""Аппаратный самотест прошивки PiCar-Pro — запуск на самой машинке.

Замена папки Examples из оригинальных репозиториев: проверяет все модули
железа по очереди и печатает отчёт. Безопасен: моторы и серво двигаются
кратко и на малой скорости.

Запуск на Raspberry Pi:  sudo python3 tools/selftest.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picarpro.logger import logger  # noqa: E402

PASS, FAIL, SKIP = '[ OK ]', '[FAIL]', '[SKIP]'
results = []


def check(name, fn):
    try:
        ok, msg = fn()
    except Exception as e:
        ok, msg = False, f'exception: {e}'
    results.append((name, ok, msg))
    print(f'{PASS if ok else (FAIL if ok is False else SKIP)} {name}: {msg}')


def main():
    print('=' * 60)
    print(' PiCar-Pro hardware self-test')
    print('=' * 60)

    from picarpro.state import SharedState
    state = SharedState()
    state.init_hardware()

    # --- система ------------------------------------------------------------
    from picarpro.utils.system_info import SystemInfo
    info = SystemInfo.get_all()
    check('system.cpu_temp', lambda: (True, f"{info['cpu_temp']} C"))
    check('system.ram', lambda: (True, f"{info['ram']['used_mb']}/{info['ram']['total_mb']} MB"))

    # --- напряжение ---------------------------------------------------------
    if state.voltage:
        check('voltage.ads7830', lambda: (state.voltage.has_adc,
                                          'ADC found' if state.voltage.has_adc
                                          else 'ADC absent — dmesg mode'))

    # --- моторы -------------------------------------------------------------
    def test_motors():
        if not (state.motors and state.motors._initialized):
            return None, 'not initialized'
        state.motors.move(25, 'forward', 'no', 0.5)
        time.sleep(0.5)
        state.motors.stop()
        return True, 'short forward impulse done'
    check('motors.drive', test_motors)

    # --- серво --------------------------------------------------------------
    def test_servos():
        if not (state.servos and state.servos._pwm_initialized):
            return None, 'not initialized'
        for angle in (60, 120, 90):
            state.servos.set_angle(0, angle)
            time.sleep(0.3)
        return True, 'steering sweep 60-120-90'
    check('servos.steering', test_servos)

    def test_camera_servos():
        if not (state.servos and state.servos._pwm_initialized):
            return None, 'not initialized'
        state.servos.set_angle(1, 60); time.sleep(0.3)
        state.servos.set_angle(1, 120); time.sleep(0.3)
        state.servos.set_angle(1, 90)
        state.servos.set_angle(2, 60); time.sleep(0.3)
        state.servos.set_angle(2, 120); time.sleep(0.3)
        state.servos.set_angle(2, 90)
        return True, 'pan/tilt sweep'
    check('servos.camera_pan_tilt', test_camera_servos)

    # --- ультразвук ---------------------------------------------------------
    def test_ultrasonic():
        if not (state.ultrasonic and state.ultrasonic._initialized):
            return None, 'not initialized'
        d = state.ultrasonic.get_last_distance()
        return (d > 0), f'distance = {d} cm'
    check('ultrasonic.distance', test_ultrasonic)

    # --- MPU6050 ------------------------------------------------------------
    def test_mpu():
        if not (state.mpu6050 and state.mpu6050.initialized):
            return None, 'not found'
        data = state.mpu6050.get_data()
        return (data is not None), \
            f"roll={data['roll']} pitch={data['pitch']} temp={data['temp']}"
    check('mpu6050.data', test_mpu)

    # --- OLED ---------------------------------------------------------------
    def test_oled():
        if not (state.oled and state.oled._initialized):
            return None, 'not initialized'
        state.oled.set_lines(['PiCar selftest', 'display OK', '', ''])
        return True, 'text drawn'
    check('oled.display', test_oled)

    # --- LED ----------------------------------------------------------------
    def test_leds():
        if not (state.leds and state.leds._initialized):
            return None, 'not initialized'
        state.leds.fill(50, 0, 0)
        time.sleep(0.3)
        state.leds.clear()
        return True, 'red flash done'
    check('leds.ws2812', test_leds)

    # --- зуммер -------------------------------------------------------------
    def test_buzzer():
        if not (state.buzzer and state.buzzer._initialized):
            return None, 'not initialized'
        state.buzzer.play_melody('beep')
        time.sleep(0.6)
        return True, 'beep done'
    check('buzzer.beep', test_buzzer)

    # --- фары/переключатели --------------------------------------------------
    def test_switches():
        if not (state.switches and state.switches._initialized):
            return None, 'not initialized'
        state.switches.headlight_on()
        time.sleep(0.3)
        state.switches.headlight_off()
        return True, 'headlight on/off'
    check('switch.headlight', test_switches)

    # --- камера ---------------------------------------------------------------
    def test_camera():
        state.init_camera()
        if not state.camera:
            return None, 'not available'
        frame = state.camera.get_frame()
        return (frame is not None and len(frame) > 100), \
            f"jpeg frame {len(frame or b'')} bytes"
    check('camera.capture', test_camera)

    # --- I2C скан -----------------------------------------------------------
    def test_i2c():
        from picarpro.hardware.mpu6050 import i2c_scan
        devs = i2c_scan()
        return (True, ', '.join(f'0x{d:02X}' for d in devs) or 'empty bus')
    check('i2c.scan', test_i2c)

    # --- итог -----------------------------------------------------------------
    print('=' * 60)
    failed = [r for r in results if r[1] is False]
    skipped = [r for r in results if r[1] is None]
    print(f' Total: {len(results)} | PASS: {len(results) - len(failed) - len(skipped)}'
          f' | FAIL: {len(failed)} | SKIP: {len(skipped)}')
    if failed:
        print(' Failed:')
        for name, _, msg in failed:
            print(f'  - {name}: {msg}')
    state.shutdown()
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
