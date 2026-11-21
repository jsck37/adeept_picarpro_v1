"""v1.6.13: слежение за ЛАДОНЬЮ (не запястьем), адаптивный декодер kpt,
подсчёт устройств hotspot (iw/dnsmasq/arp) и проверка обновлений на GitHub."""
import io

import numpy as np
import pytest


@pytest.fixture
def app(clean_state, monkeypatch):
    monkeypatch.setenv('PICARPRO_SECRET_KEY', 'test-secret')
    monkeypatch.setattr('config.AUTH_TOKEN', '')
    import picarpro.web.auth as auth_mod
    monkeypatch.setattr(auth_mod, 'AUTH_TOKEN', '')
    from picarpro.web.app import create_app
    application = create_app(clean_state)
    application.config['TESTING'] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Ладонь: геометрия по запястью+локтю, раскладка hand21 (дообученная модель)
# ---------------------------------------------------------------------------
class TestPalmTarget:
    def test_palm_extended_from_wrist_towards_fingers(self):
        from picarpro.camera.pose_detector import palm_target
        # локоть (100, 300) -> запястье (100, 200): вектор (0, -100)
        kps = [(0, 0, 0)] * 17
        kps[8] = (100.0, 300.0, 0.9)
        kps[10] = (100.0, 200.0, 0.9)
        x, y = palm_target(kps, 10, 8)
        assert (x, y) == (100.0, 170.0)          # +0.3 * предплечье за запястье

    def test_no_confident_elbow_keeps_wrist(self):
        from picarpro.camera.pose_detector import palm_target
        kps = [(0, 0, 0)] * 17
        kps[10] = (55.0, 44.0, 0.9)              # локтя нет / не уверен
        assert palm_target(kps, 10, 8) == (55.0, 44.0)

    def test_pick_front_hand_aims_at_palm(self):
        from picarpro.camera.pose_detector import pick_front_hand
        kps = [(0, 0, 0)] * 17
        kps[8] = (100.0, 300.0, 0.9)
        kps[10] = (100.0, 200.0, 0.9)
        p = {'conf': 0.9, 'box': [0, 0, 50, 400], 'kps': kps}
        hand = pick_front_hand([p])
        assert (hand['x'], hand['y']) == (100.0, 170.0)
        assert hand['wrist'] == (100.0, 200.0)   # запястье сохранено для оверлея


class TestHand21Layout:
    """Дообученная модель кисти (21 точка, MediaPipe-раскладка): центр
    ладони = среднее MCP-точек (5/9/13/17), без кода-правок на роботе."""

    def _person21(self):
        kps = [(0, 0, 0)] * 21
        kps[0] = (50.0, 200.0, 0.95)             # запястье
        mcp = ((60.0, 120.0), (80.0, 118.0), (100.0, 122.0), (110.0, 130.0))
        for i, (x, y) in zip((5, 9, 13, 17), mcp):
            kps[i] = (x, y, 0.9)
        return {'conf': 0.9, 'box': [0, 0, 80, 150], 'kps': kps}

    def test_palm_is_mean_of_mcps(self):
        from picarpro.camera.pose_detector import pick_front_hand
        hand = pick_front_hand([self._person21()])
        assert hand['side'] == 'hand'
        assert abs(hand['x'] - 87.5) < 1e-6 and abs(hand['y'] - 122.5) < 1e-6

    def test_layout_autodetect(self):
        from picarpro.camera.pose_detector import detect_layout
        assert detect_layout(17) == 'coco17'
        assert detect_layout(21) == 'hand21'
        assert detect_layout(7) == 'coco17'      # усечённый выход: без краша

    def test_decode_accepts_21kpt_output(self, monkeypatch):
        """(1, 68, N) от дообученной модели не должен падать в пустоту."""
        import types
        from picarpro.camera.pose_detector import PoseDetector
        from tests.test_pose_hand import _nms_stub
        import picarpro.camera.pose_detector as pd
        monkeypatch.setattr(pd.cv2, 'dnn',
                            types.SimpleNamespace(NMSBoxes=_nms_stub),
                            raising=False)
        n = 4200
        out = np.zeros((1, 68, n), dtype=np.float32)
        row = out[0, :, 0]
        row[4] = 0.9
        row[5 + 0 * 3:5 + 1 * 3] = (160.0, 100.0, 0.9)   # запястье
        out[0, :, 0] = row
        persons = PoseDetector._decode(out, 1.0, 0, 0, 320, 240)
        assert len(persons) == 1
        assert len(persons[0]['kps']) == 21
        hand = pd.pick_front_hand(persons)
        assert abs(hand['x'] - 160.0) < 2.0      # MCP не видны -> запястье


class TestPalmSnap:
    """Привязка расчётной точки ладони к реальному HSV-пятну кожи."""

    def test_snap_moves_target_to_skin_blob(self, monkeypatch):
        pytest.importorskip('cv2')
        import picarpro.camera.pose_detector as pd
        det = pd.PoseDetector()
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        # «кожа» (HSV ~ 8/150/200) в кадре RGB: со смещением от расчётной точки
        frame[90:130, 170:220] = (222, 158, 121)
        hand = {'x': 195.0, 'y': 95.0, 'wrist': (195.0, 140.0), 'side': 'right',
                'conf': 0.9, 'scale': 200.0, 'raised': True, 'person': 0}
        low = np.array([0, 30, 50], dtype=np.uint8)
        high = np.array([25, 255, 255], dtype=np.uint8)
        snapped = det._snap_to_skin(frame, hand, (low, high))
        # центр пятна ((170+220-1)/2, (90+130-1)/2) = (194.5, 109.5) -> int
        assert abs(snapped['x'] - 194.5) <= 1
        assert abs(snapped['y'] - 109.5) <= 1
        assert snapped['palm_geo'] == (195.0, 95.0)

    def test_no_skin_keeps_geometry(self):
        pytest.importorskip('cv2')
        import picarpro.camera.pose_detector as pd
        det = pd.PoseDetector()
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        hand = {'x': 160.0, 'y': 120.0, 'wrist': (160.0, 170.0), 'side': 'right',
                'conf': 0.9, 'scale': 200.0, 'raised': False, 'person': 0}
        low = np.array([0, 30, 50], dtype=np.uint8)
        high = np.array([25, 255, 255], dtype=np.uint8)
        assert det._snap_to_skin(frame, hand, (low, high)) is hand


# ---------------------------------------------------------------------------
# Подсчёт устройств hotspot
# ---------------------------------------------------------------------------
class TestHotspotCount:
    def test_iw_station_dump_counts(self, monkeypatch):
        from picarpro.utils import hotspot
        real_iw = hotspot._iw_station_count

        class R:
            returncode = 0
            stdout = ('Station 00:11:22:33:44:55 (on wlan0)\n\tsignal: -50\n'
                      'Station 66:77:88:99:aa:bb (on wlan0)\n')

        # подмена subprocess.run: iw вернёт 2 станции
        monkeypatch.setattr(hotspot.subprocess, 'run',
                            lambda *a, **k: R())
        n, src = hotspot.count_clients(ifaces=('wlan0',), use_cache=False)
        assert n == 2 and src == 'iw'
        assert real_iw is not None

    def test_dnsmasq_leases(self, tmp_path, monkeypatch):
        from picarpro.utils import hotspot
        import time as _time
        now = int(_time.time())
        leases = tmp_path / 'dnsmasq.leases'
        leases.write_text(
            f'{now + 600} aa:bb:cc:dd:ee:01 10.42.0.10 phone *\n'
            f'{now + 600} aa:bb:cc:dd:ee:02 10.42.0.11 laptop *\n'
            f'{now - 600} aa:bb:cc:dd:ee:03 10.42.0.12 old *\n'   # истёк
            f'never aa:bb:cc:dd:ee:04 10.42.0.13 static *\n')
        monkeypatch.setattr(hotspot, '_iw_station_count', lambda *a, **k: None)
        n, src = hotspot.count_clients(lease_paths=(str(leases),),
                                       use_cache=False)
        assert n == 3 and src == 'dnsmasq'       # истёкший lease не считается

    def test_arp_fallback_skips_incomplete_and_self(self, tmp_path, monkeypatch):
        from picarpro.utils import hotspot
        arp = tmp_path / 'arp'
        arp.write_text(
            'IP address       HW type     Flags       HW address            Mask     Device\n'
            '10.42.0.10       0x1         0x2         aa:bb:cc:dd:ee:01     *        wlan0\n'
            '10.42.0.11       0x1         0x0         aa:bb:cc:dd:ee:02     *        wlan0\n'
            '10.42.0.1        0x1         0x2         11:22:33:44:55:66     *        wlan0\n'
            '192.168.1.20     0x1         0x6         aa:bb:cc:dd:ee:03     *        eth0\n')
        monkeypatch.setattr(hotspot, '_iw_station_count', lambda *a, **k: None)
        n, src = hotspot.count_clients(lease_paths=(), arp_path=str(arp),
                                       local_ips=('10.42.0.1',),
                                       use_cache=False)
        # incomplete (0x0) и сам робот (10.42.0.1) не считаются
        assert n == 2 and src == 'arp'

    def test_nothing_available_returns_none(self, monkeypatch):
        from picarpro.utils import hotspot
        monkeypatch.setattr(hotspot, '_iw_station_count', lambda *a, **k: None)
        n, src = hotspot.count_clients(lease_paths=(),
                                       arp_path='/nonexistent/arp',
                                       use_cache=False)
        assert n is None and src is None

    def test_cache_ttl(self, monkeypatch):
        from picarpro.utils import hotspot
        calls = []

        def fake_iw(iface, run=None):
            calls.append(iface)
            return 1

        monkeypatch.setattr(hotspot, '_iw_station_count', fake_iw)
        hotspot.reset_cache()
        hotspot.count_clients(ifaces=('wlan0',), use_cache=True)
        hotspot.count_clients(ifaces=('wlan0',), use_cache=True)
        assert len(calls) == 1                   # второй вызов из кэша
        hotspot.reset_cache()


# ---------------------------------------------------------------------------
# Проверка обновлений на GitHub
# ---------------------------------------------------------------------------
class TestGitHubUpdate:
    def test_parse_repo(self):
        from picarpro.updater import parse_repo
        assert parse_repo('https://github.com/jsck37/adeept_picarpro_v1') == \
            ('jsck37', 'adeept_picarpro_v1')
        assert parse_repo('https://github.com/owner/repo.git') == \
            ('owner', 'repo')
        assert parse_repo('https://gitlab.com/owner/repo') is None

    def test_version_newer(self):
        from picarpro.updater import version_newer
        assert version_newer('1.6.13', '1.6.12') is True
        assert version_newer('1.6.12', '1.6.13') is False
        assert version_newer('1.6.12', '1.6.12') is False
        assert version_newer('1.10.0', '1.9.9') is True    # не строковое!
        assert version_newer('1.6.12-mod1', '1.6.12') is True

    def test_check_no_internet(self, monkeypatch):
        import picarpro.updater as up
        monkeypatch.setattr(up, 'internet_available', lambda: False)
        r = up.github_check()
        assert r['ok'] and r['internet'] is False and 'update_available' not in r

    def test_check_update_available(self, monkeypatch):
        import picarpro.updater as up
        monkeypatch.setattr(up, 'internet_available', lambda: True)
        monkeypatch.setattr(up, '_remote_version', lambda *a: '9.9.9')
        monkeypatch.setattr(up, '_latest_release', lambda *a: None)
        r = up.github_check()
        assert r['ok'] and r['update_available'] is True
        assert r['remote'] == '9.9.9' and r['current'] == up.current_version()

    def test_check_uptodate(self, monkeypatch):
        import picarpro.updater as up
        monkeypatch.setattr(up, 'internet_available', lambda: True)
        monkeypatch.setattr(up, '_remote_version',
                            lambda *a: up.current_version())
        monkeypatch.setattr(up, '_latest_release', lambda *a: None)
        r = up.github_check()
        assert r['ok'] and r['update_available'] is False
        assert 'последняя' in r.get('message', '')

    def test_check_network_error_is_reported_not_raised(self, monkeypatch):
        import picarpro.updater as up
        monkeypatch.setattr(up, 'internet_available', lambda: True)

        def boom(*a):
            raise up.UpdateError('HTTP 404')
        monkeypatch.setattr(up, '_remote_version', boom)
        r = up.github_check()
        assert r['ok'] is False and '404' in r['error']

    def test_download_skips_oversize(self, monkeypatch, tmp_path):
        import picarpro.updater as up

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        big = b'x' * (up.UPDATE_MAX_BYTES + 1024)

        def fake_urlopen(req, timeout=None):
            return FakeResp(big)

        monkeypatch.setattr(up.urllib.request, 'urlopen', fake_urlopen)
        with pytest.raises(up.UpdateError):
            up._download_to('https://codeload.example/x.tar.gz',
                            str(tmp_path / 'a.tar.gz'))

    def test_download_to_writes_file(self, monkeypatch, tmp_path):
        import picarpro.updater as up
        payload = b'x' * 2048

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        monkeypatch.setattr(up.urllib.request, 'urlopen',
                            lambda req, timeout=None: FakeResp(payload))
        dest = tmp_path / 'a.tar.gz'
        up._download_to('https://codeload.example/x.tar.gz', str(dest))
        assert dest.stat().st_size == len(payload)

    def test_install_route_exists(self, client):
        """Маршрут проверки обновлений отвечает без «водителя» (только чтение)."""
        rv = client.get('/api/update/check')
        assert rv.status_code == 200
        data = rv.get_json()
        assert 'ok' in data                      # интернет в песочнице не важен


class TestPaintOverlay:
    """v1.6.13: стрим собирается из СВЕЖЕГО кадра + paint-аргументов."""

    def test_pose_paint_draws_palm_marker(self):
        pytest.importorskip('cv2')
        from picarpro.camera.camera_opencv import CVThread
        cv = CVThread()
        cv.overlay_wanted = True
        persons = [{'conf': 0.9, 'box': [10, 10, 100, 200],
                    'kps': [(0, 0, 0)] * 17}]
        hand = {'x': 60.0, 'y': 110.0, 'wrist': (60.0, 140.0), 'side': 'right',
                'conf': 0.9, 'scale': 200.0, 'raised': True, 'person': 0}
        cv._set_paint('pose', persons, hand, None)
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        out = cv.paint(frame)
        assert out[110, 60, 2] > 0               # красный маркер ладони
        assert cv.get_overlay()[0] == 'pose'

    def test_paint_without_overlay_returns_frame(self):
        from picarpro.camera.camera_opencv import CVThread
        cv = CVThread()
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        assert cv.paint(frame) is frame
