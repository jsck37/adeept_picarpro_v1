"""Pytest-окружение: установка фейков железа ДО импорта picarpro и path."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.fakes import all_fakes  # noqa: E402

FAKES = all_fakes.install()

import pytest  # noqa: E402


@pytest.fixture
def fake_gpio():
    """Глобальный FakeGPIO (сброс выполняется в autouse-фикстуре)."""
    return FAKES['gpio']


@pytest.fixture
def fake_i2c_devices():
    """Реестр I2C-устройств (сброс выполняется в autouse-фикстуре)."""
    return FAKES['smbus'].shared_devices


@pytest.fixture(autouse=True)
def _reset_singletons(monkeypatch, tmp_path):
    """Полный сброс фейков и синглтонов перед КАЖДЫМ тестом.

    Важно: autouse выполняется до пользовательских фикстур, поэтому
    контроллеры, созданные в фикстурах, видят чистое «железо».
    """
    FAKES['gpio'].reset()
    FAKES['smbus'].reset()
    FAKES['spi'].reset()
    FAKES['pca9685'].reset()
    FAKES['servo'].reset()
    FAKES['ssd1306'].reset()
    FakePicamera2 = FAKES['picamera2']
    FakePicamera2.frame = None
    FakePicamera2.fail_capture_after = None
    FakePicamera2.fail_start = False
    FakePicamera2.instance = None
    from tests.fakes.all_fakes import FakeInputDevice
    FakeInputDevice.reset()
    yield


@pytest.fixture
def clean_state(monkeypatch, tmp_path):
    """SharedState с инициализированным «железом» на фейках (с MPU6050)."""
    # на шине присутствует MPU6050 (ADS7830 отсутствует — dmesg-режим);
    # 0x42 = 68 — temp_raw: «живой» сенсор (не пустая шина), но данные
    # не удовлетворяют wait-условиям тестов (v1.3.0)
    FAKES['smbus'].reset()
    FAKES['smbus'].shared_devices[0x68] = {0x75: 0x68, 0x42: 68}

    from picarpro.state import SharedState
    state = SharedState()
    state.init_hardware()
    yield state
    state.shutdown()
