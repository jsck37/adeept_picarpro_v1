"""v1.5.3: миссии и обновление по воздуху с откатом."""
import io
import json
import os
import tarfile
import time
import zipfile

import pytest


def wait_for(cond, timeout=4.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


# ===========================================================================
# Миссии
# ===========================================================================
@pytest.fixture
def mr(clean_state, tmp_path):
    from picarpro.functions.missions import MissionRunner
    return MissionRunner(clean_state, directory=tmp_path / 'm')


class TestMissionValidation:
    def test_valid_normalised_and_clamped(self):
        from picarpro.functions.missions import prepare
        out = prepare([{'op': 'drive', 'dir': 'forward', 'speed': 500, 'sec': 9999}])
        assert out[0]['speed'] == 100 and out[0]['sec'] == 60.0

    @pytest.mark.parametrize('bad', [
        [], 'x', [1], [{'op': 'rm -rf'}], [{'op': 'drive', 'dir': 'sideways'}],
        [{'op': 'turn'}], [{'op': 'servo', 'id': 1}], [{'op': 'auto', 'mode': 'x'}],
        [{'op': 'drive', 'dir': 'forward', 'sec': 'abc'}],
        [{'op': 'repeat', 'times': 2, 'steps': []}],
    ])
    def test_rejected(self, bad):
        from picarpro.functions.missions import MissionError, prepare
        with pytest.raises(MissionError):
            prepare(bad)

    def test_too_many_steps(self):
        from picarpro.functions.missions import MissionError, prepare
        with pytest.raises(MissionError):
            prepare([{'op': 'repeat', 'times': 50, 'steps': [
                {'op': 'repeat', 'times': 50, 'steps': [{'op': 'wait', 'sec': 0}]}]}])

    def test_too_deep(self):
        from picarpro.functions.missions import MissionError, prepare
        s = [{'op': 'wait', 'sec': 0}]
        for _ in range(6):
            s = [{'op': 'repeat', 'times': 1, 'steps': s}]
        with pytest.raises(MissionError):
            prepare(s)

    def test_builtins_valid(self):
        from picarpro.functions.missions import BUILTIN, prepare
        for steps in BUILTIN.values():
            assert prepare(steps)


class TestMissionStorage:
    def test_save_list_load_delete(self, mr):
        mr.save('my_run', [{'op': 'wait', 'sec': 1}])
        assert any(m['name'] == 'my_run' and not m['builtin'] for m in mr.list())
        assert mr.load('my_run')[0]['op'] == 'wait'
        mr.delete('my_run')
        assert not any(m['name'] == 'my_run' for m in mr.list())

    @pytest.mark.parametrize('name', ['../evil', 'a/b', '', 'x' * 40, 'a b', 'a.json'])
    def test_bad_names(self, mr, name):
        from picarpro.functions.missions import MissionError
        with pytest.raises(MissionError):
            mr.save(name, [{'op': 'wait', 'sec': 1}])

    def test_builtin_protected(self, mr):
        from picarpro.functions.missions import MissionError
        with pytest.raises(MissionError):
            mr.save('square', [{'op': 'wait', 'sec': 1}])
        with pytest.raises(MissionError):
            mr.delete('square')


class TestMissionRun:
    def test_drive_runs_and_stops(self, mr, clean_state, fake_gpio):
        from config import MOTOR_A_EN
        mr.start(steps=[{'op': 'drive', 'dir': 'backward', 'speed': 50, 'sec': 0.4}])
        assert wait_for(lambda: fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle > 0, 2)
        assert wait_for(lambda: mr.get_status()['state'] == 'done', 3)
        assert fake_gpio.pwm_for(MOTOR_A_EN).duty_cycle == 0
        assert mr.get_status()['done'] == 1 and not mr.is_active()

    def test_repeat_counts(self, mr):
        mr.start(steps=[{'op': 'repeat', 'times': 3, 'steps': [{'op': 'wait', 'sec': 0.02}]}])
        assert wait_for(lambda: mr.get_status()['state'] == 'done')
        assert mr.get_status()['done'] == 3 and mr.get_status()['total'] == 3

    def test_stop_interrupts(self, mr, clean_state):
        mr.start(steps=[{'op': 'drive', 'dir': 'backward', 'speed': 50, 'sec': 30}])
        assert wait_for(lambda: clean_state.motors.is_moving, 2)
        assert mr.stop() is True
        assert wait_for(lambda: not mr.is_active(), 2)
        assert mr.get_status()['state'] == 'stopped' and not clean_state.motors.is_moving

    def test_obstacle_aborts_forward(self, mr, clean_state):
        clean_state.ultrasonic._measure = lambda: 8.0         # 8 см впереди
        mr.start(steps=[{'op': 'drive', 'dir': 'forward', 'speed': 50, 'sec': 5},
                        {'op': 'wait', 'sec': 0.1}])
        assert wait_for(lambda: mr.get_status()['state'] == 'obstacle', 4)
        assert not clean_state.motors.is_moving

    def test_backward_ignores_front_sensor(self, mr, clean_state):
        clean_state.ultrasonic._measure = lambda: 8.0
        mr.start(steps=[{'op': 'drive', 'dir': 'backward', 'speed': 50, 'sec': 0.3}])
        assert wait_for(lambda: mr.get_status()['state'] == 'done', 3)

    def test_turn_timed_without_gyro(self, mr, clean_state, monkeypatch):
        # v1.6.2: разворот — дуга (drive.arc_turn), скорость — AUTO_ARC_RATE_DPS
        monkeypatch.setattr('picarpro.drive.AUTO_ARC_RATE_DPS', 10000.0)
        clean_state.mpu6050 = None
        mr.start(steps=[{'op': 'turn', 'deg': 90}])
        assert wait_for(lambda: mr.get_status()['state'] == 'done', 3)

    def test_manual_move_interrupts_mission(self, mr, clean_state):
        from picarpro.commands import process
        clean_state.missions = mr
        mr.start(steps=[{'op': 'wait', 'sec': 30}])
        assert wait_for(mr.is_active, 2)
        r = process(clean_state, {'cmd': 'move', 'params': {'dir': 'forward'}})
        assert r.get('interrupted_auto') is True
        assert wait_for(lambda: not mr.is_active(), 2)
        clean_state.motors.stop()

    def test_estop_stops_mission(self, mr, clean_state):
        clean_state.missions = mr
        mr.start(steps=[{'op': 'wait', 'sec': 30}])
        assert wait_for(mr.is_active, 2)
        clean_state.emergency_stop()
        assert wait_for(lambda: not mr.is_active(), 2)

    def test_led_beep_steps_are_skipped(self, mr, clean_state):
        """v1.6.5: подсветка/буззер убраны из миссий — шаги пропускаются."""
        from picarpro.functions.missions import validate_steps
        assert validate_steps([{'op': 'led', 'mode': 'solid'},
                               {'op': 'beep', 'sec': 0}]) == []
        mr.start(steps=[{'op': 'servo', 'id': 1, 'angle': 40},
                        {'op': 'led', 'mode': 'solid'}, {'op': 'beep', 'sec': 0}])
        assert wait_for(lambda: mr.get_status()['state'] == 'done', 3)
        assert clean_state.servos.get_angle(1) == 40


class TestRecordAndHome:
    def test_reverse_dir(self):
        from picarpro.functions.missions import reverse_dir
        assert reverse_dir('forward') == 'backward'
        assert reverse_dir('backward') == 'forward'
        assert reverse_dir('forward_left') == 'backward_left'
        assert reverse_dir('left') == 'backward_left'
        assert reverse_dir('backward_right') == 'forward_right'
        assert reverse_dir('spin_left') == 'spin_right'
        assert reverse_dir('stop') == 'stop'

    def test_record_and_return_home(self, mr, monkeypatch):
        import picarpro.functions.missions as mm
        monkeypatch.setattr(mm, 'MISSION_RECORD_MIN_SEGMENT_S', 0.05)
        mr.record_start()
        mr.note_move('forward', 40); time.sleep(0.12)
        mr.note_move('forward_left', 40); time.sleep(0.12)
        mr.note_move('stop', 0); time.sleep(0.06)
        steps = mr.record_stop()
        assert [s['dir'] for s in steps] == ['forward', 'forward_left']
        home = mr.return_home_steps()
        assert [s['dir'] for s in home] == ['backward_left', 'backward']   # в обратном порядке

    def test_return_home_without_path(self, mr):
        from picarpro.functions.missions import MissionError
        with pytest.raises(MissionError):
            mr.return_home_steps()

    def test_note_move_ignored_when_not_recording(self, mr):
        mr.note_move('forward', 40)
        assert mr._events == []

    def test_record_save_as(self, mr, monkeypatch):
        import picarpro.functions.missions as mm
        monkeypatch.setattr(mm, 'MISSION_RECORD_MIN_SEGMENT_S', 0.05)
        mr.handle('record_start', {})
        mr.note_move('forward', 40); time.sleep(0.12)
        r = mr.handle('record_stop', {'save_as': 'trip'})
        assert r['ok'] and r['saved']
        assert mr.load('trip')[0]['dir'] == 'forward'


class TestMissionCommand:
    def test_roundtrip_and_arbiter(self, clean_state):
        from picarpro.commands import process
        r = process(clean_state, {'cmd': 'mission', 'params': {'action': 'list'}}, 'a')
        assert r['ok'] and any(m['name'] == 'square' for m in r['missions'])
        # другой клиент: чтение можно, запуск — нет
        process(clean_state, {'cmd': 'speed', 'params': {'value': 30}}, 'a')
        assert process(clean_state, {'cmd': 'mission', 'params': {'action': 'status'}}, 'b')['ok']
        r = process(clean_state, {'cmd': 'mission', 'params': {'action': 'start', 'name': 'square'}}, 'b')
        assert r['code'] == 'not_driver'

    def test_start_unknown_and_status_in_telemetry(self, clean_state):
        from picarpro.commands import process
        r = process(clean_state, {'cmd': 'mission', 'params': {'action': 'start', 'name': 'nope'}})
        assert r['ok'] is False and 'not found' in r['error']
        assert 'mission' in clean_state.get_status()


# ===========================================================================
# Обновление
# ===========================================================================
def _skeleton(root, version='1.0.0', marker_text='old'):
    (root / 'picarpro').mkdir(parents=True, exist_ok=True)
    (root / 'picarpro' / '__init__.py').write_text(f"__version__ = '{version}'\n")
    (root / 'picarpro' / 'state.py').write_text(f"MARK = '{marker_text}'\n")
    (root / 'boot.py').write_text('print(1)\n')
    (root / 'config.py').write_text(f"SPEED = {10 if marker_text == 'old' else 20}\nKEEP = 1\n")
    (root / 'tools').mkdir(exist_ok=True)
    (root / 'tools' / 'a.py').write_text("X = 1\n")
    return root


def _make_tar(src, out, prefix='proj'):
    with tarfile.open(out, 'w:gz') as t:
        t.add(src, arcname=prefix, filter=lambda ti: None if 'pycache' in ti.name else ti)
    return str(out)


@pytest.fixture
def proj(tmp_path):
    root = _skeleton(tmp_path / 'live')
    new = _skeleton(tmp_path / 'new', version='2.0.0', marker_text='new')
    (new / 'tools' / 'b.py').write_text("Y = 2\n")
    (root / 'tools' / 'stale.py').write_text("Z = 3\n")
    return root, new, tmp_path


class TestSafeExtract:
    def test_traversal_rejected(self, tmp_path):
        from picarpro import updater
        p = tmp_path / 'evil.tar.gz'
        with tarfile.open(p, 'w:gz') as t:
            ti = tarfile.TarInfo('../escape.txt'); ti.size = 1
            t.addfile(ti, io.BytesIO(b'x'))
        with pytest.raises(updater.UpdateError):
            updater.safe_extract(str(p), str(tmp_path / 'out'))
        assert not (tmp_path / 'escape.txt').exists()

    def test_absolute_rejected(self, tmp_path):
        from picarpro import updater
        p = tmp_path / 'abs.tar.gz'
        with tarfile.open(p, 'w:gz') as t:
            ti = tarfile.TarInfo('/etc/x'); ti.size = 1
            t.addfile(ti, io.BytesIO(b'x'))
        with pytest.raises(updater.UpdateError):
            updater.safe_extract(str(p), str(tmp_path / 'out'))

    def test_symlink_rejected(self, tmp_path):
        from picarpro import updater
        p = tmp_path / 'ln.tar.gz'
        with tarfile.open(p, 'w:gz') as t:
            ti = tarfile.TarInfo('proj/link'); ti.type = tarfile.SYMTYPE; ti.linkname = '/etc/passwd'
            t.addfile(ti)
        with pytest.raises(updater.UpdateError):
            updater.safe_extract(str(p), str(tmp_path / 'out'))

    def test_zip_traversal_rejected(self, tmp_path):
        from picarpro import updater
        p = tmp_path / 'e.zip'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('../../evil', 'x')
        with pytest.raises(updater.UpdateError):
            updater.safe_extract(str(p), str(tmp_path / 'out'))

    def test_garbage_rejected(self, tmp_path):
        from picarpro import updater
        p = tmp_path / 'g.tar.gz'
        p.write_bytes(b'not an archive' * 10)
        with pytest.raises(updater.UpdateError):
            updater.safe_extract(str(p), str(tmp_path / 'out'))


class TestApplyAndRollback:
    def test_full_cycle(self, proj):
        from picarpro import updater
        root, new, tmp = proj
        (root / 'missions').mkdir(); (root / 'missions' / 'm.json').write_text('{}')
        (root / 'config_local.py').write_text('SPEED = 99\n')
        arc = _make_tar(new, tmp / 'u.tar.gz')
        r = updater.apply_update(arc, str(root))
        assert r['ok'] and r['from'] == '1.0.0' and r['to'] == '2.0.0'
        assert 'SPEED' in r['config_changed'] and 'KEEP' not in r['config_changed']
        assert "MARK = 'new'" in (root / 'picarpro' / 'state.py').read_text()
        assert (root / 'tools' / 'b.py').exists() and not (root / 'tools' / 'stale.py').exists()
        assert (root / 'missions' / 'm.json').exists()                  # данные сохранены
        assert (root / 'config_local.py').read_text() == 'SPEED = 99\n'  # локальные настройки
        assert 'SPEED = 10' in (root / 'config_user_prev.py').read_text()
        assert (root / '.update_pending').exists() and not (root / '.update_tmp').exists()
        # откат
        rb = updater.rollback(None, str(root))
        assert rb['to'] == '1.0.0'
        assert "MARK = 'old'" in (root / 'picarpro' / 'state.py').read_text()
        assert (root / 'tools' / 'stale.py').exists() and not (root / 'tools' / 'b.py').exists()
        assert not (root / '.update_pending').exists()

    def test_zip_with_toplevel_dir(self, proj):
        from picarpro import updater
        root, new, tmp = proj
        p = tmp / 'u.zip'
        with zipfile.ZipFile(p, 'w') as z:
            for f in new.rglob('*'):
                if f.is_file():
                    z.write(f, 'adeept-main/' + str(f.relative_to(new)))
        assert updater.apply_update(str(p), str(root))['to'] == '2.0.0'

    def test_syntax_error_blocks_and_changes_nothing(self, proj):
        from picarpro import updater
        root, new, tmp = proj
        (new / 'picarpro' / 'broken.py').write_text('def (:\n')
        with pytest.raises(updater.UpdateError, match='syntax'):
            updater.apply_update(_make_tar(new, tmp / 'u.tar.gz'), str(root))
        assert "MARK = 'old'" in (root / 'picarpro' / 'state.py').read_text()
        assert not (root / '.update_pending').exists()

    def test_not_a_project(self, proj):
        from picarpro import updater
        root, new, tmp = proj
        os.remove(new / 'boot.py')
        with pytest.raises(updater.UpdateError, match='boot.py'):
            updater.apply_update(_make_tar(new, tmp / 'u.tar.gz'), str(root))

    def test_backup_pruning(self, proj, monkeypatch):
        from picarpro import updater
        root, new, tmp = proj
        monkeypatch.setattr(updater, 'UPDATE_KEEP_BACKUPS', 2)
        for i in range(4):
            updater.create_backup(str(root))
            time.sleep(1.05)                 # имя содержит секунды
        assert len(updater.list_backups(str(root))) == 2

    def test_rollback_validates_name(self, proj):
        from picarpro import updater
        root, _, _ = proj
        updater.create_backup(str(root))
        for bad in ('../../etc/passwd', 'backup_x.tar.gz', 'foo'):
            with pytest.raises(updater.UpdateError):
                updater.rollback(bad, str(root))

    def test_no_backups(self, proj):
        from picarpro import updater
        with pytest.raises(updater.UpdateError):
            updater.rollback(None, str(proj[0]))


class TestStartupGuard:
    def _after_update(self, proj):
        from picarpro import updater
        root, new, tmp = proj
        updater.apply_update(_make_tar(new, tmp / 'u.tar.gz'), str(root))
        return updater, root

    def test_first_boot_pending_then_healthy(self, proj):
        updater, root = self._after_update(proj)
        assert updater.startup_check(str(root), now=1000.0) == 'pending'
        assert json.loads((root / '.update_pending').read_text())['boots'] == 1
        assert updater.mark_healthy(str(root)) is True
        assert updater.startup_check(str(root), now=2000.0) is None

    def test_crash_loop_rolls_back_automatically(self, proj):
        updater, root = self._after_update(proj)
        assert updater.startup_check(str(root), now=1000.0) == 'pending'
        # сервис упал и перезапустился через 5 с — раньше UPDATE_HEALTHY_AFTER_S
        assert updater.startup_check(str(root), now=1005.0) == 'rolled_back'
        assert "MARK = 'old'" in (root / 'picarpro' / 'state.py').read_text()
        assert updater.startup_check(str(root), now=1010.0) is None

    def test_restart_after_healthy_window_is_not_a_crash(self, proj):
        updater, root = self._after_update(proj)
        updater.startup_check(str(root), now=1000.0)
        assert updater.startup_check(str(root), now=1000.0 + 500) == 'pending'


class TestUpdateRoutes:
    @pytest.fixture
    def client(self, clean_state, proj, monkeypatch):
        import picarpro.web.app as appmod
        import picarpro.web.auth as auth_mod
        from picarpro import updater
        root, new, tmp = proj
        monkeypatch.setattr(updater, 'ROOT', str(root))
        monkeypatch.setattr(appmod, 'BASE_DIR', root)
        monkeypatch.setattr(updater, 'schedule_restart', lambda delay=1.5: True)
        monkeypatch.setattr('config.AUTH_TOKEN', '')
        monkeypatch.setattr(auth_mod, 'AUTH_TOKEN', '')
        app = appmod.create_app(clean_state)
        app.config['TESTING'] = True
        return app.test_client(), root, new, tmp

    def test_upload_and_info_and_rollback(self, client):
        c, root, new, tmp = client
        arc = _make_tar(new, tmp / 'u.tar.gz')
        r = c.post('/api/update', data={'file': (open(arc, 'rb'), 'u.tar.gz')},
                   content_type='multipart/form-data')
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body['to'] == '2.0.0' and body['restarting'] is True
        info = c.get('/api/update/info').get_json()
        assert info['version'] == '2.0.0' and info['backups'] and info['pending']
        rb = c.post('/api/update/rollback', json={}).get_json()
        assert rb['ok'] and rb['to'] == '1.0.0'

    def test_big_upload_allowed_beyond_default_limit(self, client):
        """Общий лимит 1 МБ не должен блокировать загрузку обновления."""
        c, root, new, tmp = client
        (new / 'picarpro' / 'blob.bin').write_bytes(os.urandom(2 * 1024 * 1024))
        arc = _make_tar(new, tmp / 'big.tar.gz')
        r = c.post('/api/update', data={'file': (open(arc, 'rb'), 'big.tar.gz')},
                   content_type='multipart/form-data')
        assert r.status_code == 200, r.get_data()[:200]

    def test_bad_archive_400(self, client):
        c, *_ = client
        r = c.post('/api/update', data={'file': (io.BytesIO(b'junk' * 100), 'x.tar.gz')},
                   content_type='multipart/form-data')
        assert r.status_code == 400 and r.get_json()['ok'] is False

    def test_missing_file_400(self, client):
        assert client[0].post('/api/update', data={}).status_code == 400

    def test_non_driver_rejected(self, client, clean_state):
        c, root, new, tmp = client
        clean_state.control.take('someone')
        arc = _make_tar(new, tmp / 'u.tar.gz')
        r = c.post('/api/update', data={'file': (open(arc, 'rb'), 'u.tar.gz')},
                   content_type='multipart/form-data', headers={'X-Client-Id': 'me'})
        assert r.status_code == 409 and r.get_json()['code'] == 'not_driver'
