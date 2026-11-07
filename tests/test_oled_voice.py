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

    def test_marquee_scrolls_frames(self, oled):
        """Бегущая строка рисует кадры непрерывно даже при статичных строках."""
        import time
        from tests.fakes.all_fakes import FakeSSD1306
        oled.set_lines(['a', 'b', 'c'])
        n0 = len(FakeSSD1306.instances[0].frames)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if len(FakeSSD1306.instances[0].frames) - n0 >= 6:
                break
            time.sleep(0.05)
        assert len(FakeSSD1306.instances[0].frames) - n0 >= 6, \
            'бегущая строка не прокручивается (нет серии кадров)'

    def test_marquee_bitmap_built(self):
        from PIL import Image, ImageDraw
        from config import OLED_FONT_SIZE, OLED_MARQUEE_TEXT
        from picarpro.hardware.oled import OLEDDisplay, MARQUEE_Y
        _, reg = OLEDDisplay._load_fonts()
        img, w = OLEDDisplay._build_marquee(reg.get(OLED_FONT_SIZE))
        assert img is not None, 'битовая карта бегущей строки не собрана'
        assert img.size[1] == 64 - MARQUEE_Y
        assert w > 0
        tmp = ImageDraw.Draw(Image.new('1', (8, 8)))
        assert w >= int(tmp.textlength(OLED_MARQUEE_TEXT,
                                       font=reg.get(OLED_FONT_SIZE)))

    def test_warning_rows_fit_width(self):
        """Предупреждение: <=2 строки, каждая помещается в 128 px."""
        from PIL import Image, ImageDraw
        from picarpro.hardware.oled import OLEDDisplay, _WARN_MAX_W
        _, reg = OLEDDisplay._load_fonts()
        draw = ImageDraw.Draw(Image.new('1', (8, 8)))
        rows, font, size = OLEDDisplay._layout_warning(draw, reg)
        assert 1 <= len(rows) <= 2
        assert size in (9, 8, 7)
        if font is not None:
            for r in rows:
                assert draw.textlength(r, font=font) <= _WARN_MAX_W, \
                    f'строка предупреждения шире экрана: {r!r}'

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
