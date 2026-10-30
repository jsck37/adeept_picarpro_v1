"""Валидация конфигурации: уникальность пинов, диапазоны серво, команды."""
import config


class TestPins:
    def test_pin_uniqueness(self):
        pins = [
            config.MOTOR_A_EN, config.MOTOR_A_IN1, config.MOTOR_A_IN2,
            config.MOTOR_B_EN, config.MOTOR_B_IN1, config.MOTOR_B_IN2,
            config.BUZZER_PIN, config.ULTRASONIC_TRIGGER, config.ULTRASONIC_ECHO,
            config.HEADLIGHT_PIN, *config.SWITCH_PINS,
            config.LINE_LEFT_PIN, config.LINE_MIDDLE_PIN, config.LINE_RIGHT_PIN,
        ]
        assert len(pins) == len(set(pins)), f'дубли пинов: {sorted(pins)}'

    def test_pin_ranges(self):
        for p in (config.MOTOR_A_EN, config.MOTOR_A_IN1, config.MOTOR_A_IN2,
                  config.MOTOR_B_EN, config.MOTOR_B_IN1, config.MOTOR_B_IN2,
                  config.BUZZER_PIN, config.ULTRASONIC_TRIGGER,
                  config.ULTRASONIC_ECHO, config.HEADLIGHT_PIN,
                  config.LINE_LEFT_PIN, config.LINE_MIDDLE_PIN,
                  config.LINE_RIGHT_PIN, *config.SWITCH_PINS):
            assert 0 <= p <= 27, f'пин {p} вне диапазона BCM'


class TestServos:
    def test_servo_count_matches_maps(self):
        assert config.SERVO_COUNT == len(config.SERVO_INIT_ANGLES)
        assert config.SERVO_COUNT == len(config.SERVO_LIMITS)

    def test_servo_channels_valid(self):
        for key in ('SERVO_STEERING', 'SERVO_CAM_PAN', 'SERVO_CAM_TILT',
                    'SERVO_CRANE_ARM', 'SERVO_CRANE_GRIP'):
            v = getattr(config, key)
            assert 0 <= v < config.SERVO_COUNT, f'{key}={v} вне диапазона'

    def test_limits_sane(self):
        for sid, lim in config.SERVO_LIMITS.items():
            assert lim['min'] < lim['max'], f'servo {sid}: min>=max'

    def test_crane_angles_within_limits(self):
        for angle in (config.CRANE_ARM_OPEN, config.CRANE_ARM_CLOSED):
            assert config.SERVO_LIMITS[config.SERVO_CRANE_ARM]['min'] <= angle \
                <= config.SERVO_LIMITS[config.SERVO_CRANE_ARM]['max']
        for angle in (config.CRANE_GRIP_LOW, config.CRANE_GRIP_MID,
                      config.CRANE_GRIP_HIGH):
            assert config.SERVO_LIMITS[config.SERVO_CRANE_GRIP]['min'] <= angle \
                <= config.SERVO_LIMITS[config.SERVO_CRANE_GRIP]['max']


class TestSteerMap:
    def test_steer_map_complete(self):
        for d in ('forward', 'backward', 'left', 'right', 'forward_left',
                  'forward_right', 'backward_left', 'backward_right', 'stop'):
            assert d in config.STEER_MAP
            angle = config.STEER_MAP[d]
            assert 0 <= angle <= 180

    def test_steer_map_within_limits(self):
        lo = config.SERVO_LIMITS[config.SERVO_STEERING]['min']
        hi = config.SERVO_LIMITS[config.SERVO_STEERING]['max']
        for d, angle in config.STEER_MAP.items():
            assert lo <= angle <= hi, f'STEER_MAP[{d}]={angle} вне лимитов руля'


class TestNetwork:
    def test_ports_dont_clash(self):
        assert config.FLASK_PORT != config.WEBSOCKET_PORT

    def test_hotspot_password_min_length(self):
        assert len(config.HOTSPOT_PASSWORD) >= 8

    def test_hotspot_channel_valid(self):
        assert 1 <= int(config.HOTSPOT_CHANNEL) <= 13


class TestBattery:
    def test_battery_thresholds(self):
        assert config.BATTERY_MIN_V < config.BATTERY_VREF
