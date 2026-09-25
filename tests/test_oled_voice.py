"""Тесты OLED (фейковый luma.oled) и voice_command (с исправлением бага
закрытого файлового дескриптора в Popen)."""
import os

import pytest


@pytest.fixture
def oled():
    from picarpro.hardware.oled import OLEDDisplay
    o = OLEDDisplay()
    yield o
    o.shutdown()


class TestOLED:
    def test_init(self, oled):
        from tests.fakes.all_fakes import FakeSSD1306, FakeLumaI2C
        assert oled._initialized
        assert FakeSSD1306.instances
        assert FakeSSD1306.instances[0].width == 128
        assert FakeSSD1306.instances[0].height == 64
        assert FakeLumaI2C.instances[0].address == 0x3C

    def test_set_lines_truncated(self, oled):
        oled.set_lines(['X' * 100, 'ok', 12345])
        with oled._lock:
            assert oled._lines[0] == 'X' * 21
            assert oled._lines[1] == 'ok'
            assert oled._lines[2] == '12345'

    def test_loop_draws_frames(self, oled):
        import time
        oled.set_lines(['line1', 'line2'])
        deadline = time.monotonic() + 2.0
        from tests.fakes.all_fakes import FakeSSD1306
        while time.monotonic() < deadline:
            if FakeSSD1306.instances[0].frames:
                break
            time.sleep(0.05)
        assert FakeSSD1306.instances[0].frames, 'OLED не рисует кадры'

    def test_low_voltage_shown(self, oled):
        import time
        from tests.fakes.all_fakes import FakeSSD1306
        oled.set_low_voltage(True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if FakeSSD1306.instances[0].frames:
                break
            time.sleep(0.05)
        assert FakeSSD1306.instances[0].frames

    def test_shutdown(self, oled):
        from tests.fakes.all_fakes import FakeSSD1306
        oled.shutdown()
        assert FakeSSD1306.instances[0].cleaned


# ---------------------------------------------------------------------------
# voice_command
# ---------------------------------------------------------------------------
def make_voice(monkeypatch, tmp_path, binary=True, model=True):
    """VoiceCommandController с подменой путей sherpa.

    Бинарник ищется по пути <model>/../sherpa-ncnn-alsa, т.е. рядом с
    каталогом модели: создаю структуру tmp/opt/sherpa-ncnn/model и
    tmp/opt/sherpa-ncnn-alsa."""
    import picarpro.functions.voice_command as mod
    base = tmp_path / 'opt'
    model_path = base / 'sherpa-ncnn' / ('model' if model else 'missing')
    if model:
        model_path.mkdir(parents=True)
    if binary:
        bin_path = base / 'sherpa-ncnn-alsa'
        bin_path.write_text('#!/bin/sh\n')
        bin_path.chmod(0o755)
    monkeypatch.setattr(mod, 'VOICE_MODEL_PATH', str(model_path))
    monkeypatch.setattr(mod, 'VOICE_OUTPUT_FILE', str(tmp_path / 'voice.txt'))
    monkeypatch.setattr(mod, 'VOICE_ALSA_DEVICE', 'default')
    return mod.VoiceCommandController(servos=None, motors=None)


class TestVoice:
    def test_disabled_without_binary(self, monkeypatch, tmp_path):
        vc = make_voice(monkeypatch, tmp_path, binary=False)
        assert vc._initialized is False

    def test_enabled_with_binary(self, monkeypatch, tmp_path):
        vc = make_voice(monkeypatch, tmp_path, binary=True)
        assert vc._initialized is True

    def test_start_runs_process(self, monkeypatch, tmp_path):
        """Ключевой тест исправления: Popen получает ОТКРЫТЫЙ дескриптор."""
        vc = make_voice(monkeypatch, tmp_path, binary=True)
        vc.start()
        try:
            assert vc._sherpa_process is not None, \
                'Popen упал — баг закрытого файла не исправлен'
            assert vc._active is True
        finally:
            vc.shutdown()

    def test_command_map_execution(self, monkeypatch, tmp_path):
        import picarpro.functions.voice_command as mod
        vc = make_voice(monkeypatch, tmp_path, binary=False)
        moves = []

        class FakeMotors:
            def move(self, *a, **kw):
                moves.append(('move', a))

            def stop(self):
                moves.append(('stop',))

        vc.motors = FakeMotors()
        vc._execute_command('forward')
        vc._execute_command('stop')
        assert ('move', (50, 'forward', 'no', 0.5)) in moves
        assert ('stop',) in moves

    def test_read_and_execute_cycle(self, monkeypatch, tmp_path):
        mod = __import__('picarpro.functions.voice_command',
                         fromlist=['VOICE_OUTPUT_FILE'])
        import picarpro.functions.voice_command as vmod
        vc = make_voice(monkeypatch, tmp_path)
        executed = []
        monkeypatch.setattr(vc, '_execute_command',
                            lambda c: executed.append(c))
        with open(vmod.VOICE_OUTPUT_FILE, 'w') as f:
            f.write('stop')
        vc._read_and_execute()
        assert 'stop' in executed
        # повторное чтение той же команды не выполняется
        vc._read_and_execute()
        assert executed.count('stop') == 1
