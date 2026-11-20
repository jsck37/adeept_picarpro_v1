"""setup.py / tools / hotspot: загрузка YOLO, выбор канала, проверки связности."""
import hashlib
import http.server
import os
import re
import shutil
import stat
import subprocess
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import yolo_common  # noqa: E402


# ---- локальный HTTP-сервер для проверки загрузки ----------------------------
@pytest.fixture
def http_server(tmp_path):
    payload = os.urandom(200_000)
    (tmp_path / 'model.onnx').write_bytes(payload)
    state = {'fail_first': 0}

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

        def do_GET(self):
            if state['fail_first'] > 0:
                state['fail_first'] -= 1
                self.send_error(503)
                return
            super().do_GET()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}/model.onnx', payload, state
    srv.shutdown()


class TestDownload:
    def test_ok_with_sha(self, http_server, tmp_path, monkeypatch):
        url, payload, _ = http_server
        monkeypatch.setattr(yolo_common.time, 'sleep', lambda s: None)
        dest = str(tmp_path / 'out' / 'm.onnx')
        assert yolo_common.download_file(url, dest, sha256=hashlib.sha256(payload).hexdigest())
        assert open(dest, 'rb').read() == payload
        assert not os.path.exists(dest + '.part')

    def test_wrong_sha_leaves_nothing(self, http_server, tmp_path, monkeypatch):
        url, _, _ = http_server
        monkeypatch.setattr(yolo_common.time, 'sleep', lambda s: None)
        dest = str(tmp_path / 'm.onnx')
        assert yolo_common.download_file(url, dest, sha256='0' * 64, retries=2) is False
        assert not os.path.exists(dest) and not os.path.exists(dest + '.part')

    def test_retries_on_server_error(self, http_server, tmp_path, monkeypatch):
        url, payload, state = http_server
        monkeypatch.setattr(yolo_common.time, 'sleep', lambda s: None)
        state['fail_first'] = 2
        dest = str(tmp_path / 'm.onnx')
        assert yolo_common.download_file(url, dest, retries=3)
        assert open(dest, 'rb').read() == payload

    def test_progress_reported(self, http_server, tmp_path):
        url, payload, _ = http_server
        seen = []
        yolo_common.download_file(url, str(tmp_path / 'm.onnx'),
                                  progress=lambda d, t: seen.append((d, t)))
        assert seen and seen[-1][0] == len(payload)

    def test_coco_classes(self, tmp_path):
        assert len(yolo_common.COCO80) == 80
        p = tmp_path / 'sub' / 'classes.txt'
        yolo_common.write_classes(str(p))
        assert p.read_text().splitlines()[0] == 'person'
        assert len(p.read_text().splitlines()) == 80


class TestSetupYoloStage:
    @pytest.fixture
    def setup_mod(self, monkeypatch):
        import importlib
        sys.path.insert(0, ROOT)
        spec = importlib.util.spec_from_file_location('picar_setup', os.path.join(ROOT, 'setup.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_existing_model_gets_classes(self, setup_mod, tmp_path, monkeypatch):
        import config
        model = tmp_path / 'models' / 'yolov8n.onnx'
        model.parent.mkdir()
        model.write_bytes(b'x')
        classes = tmp_path / 'models' / 'classes.txt'
        monkeypatch.setattr(config, 'YOLO_MODEL_PATH', str(model))
        monkeypatch.setattr(config, 'YOLO_CLASSES_FILE', str(classes))
        monkeypatch.delenv('SUDO_USER', raising=False)
        monkeypatch.delenv('PICARPRO_SKIP_YOLO', raising=False)
        setup_mod.stage_8_yolo_model()
        assert classes.exists() and len(classes.read_text().splitlines()) == 80

    def test_downloads_from_configured_url(self, setup_mod, http_server, tmp_path, monkeypatch):
        import config
        url, payload, _ = http_server
        model = tmp_path / 'm' / 'y.onnx'
        monkeypatch.setattr(config, 'YOLO_MODEL_PATH', str(model))
        monkeypatch.setattr(config, 'YOLO_CLASSES_FILE', str(tmp_path / 'm' / 'classes.txt'))
        monkeypatch.setattr(config, 'YOLO_MODEL_URL', url)
        monkeypatch.setattr(config, 'YOLO_MODEL_SHA256', hashlib.sha256(payload).hexdigest())
        monkeypatch.delenv('SUDO_USER', raising=False)
        monkeypatch.delenv('PICARPRO_SKIP_YOLO', raising=False)
        setup_mod.stage_8_yolo_model()
        assert model.read_bytes() == payload

    def test_missing_model_is_not_fatal_and_explains(self, setup_mod, tmp_path, monkeypatch, capsys):
        import config
        monkeypatch.setattr(config, 'YOLO_MODEL_PATH', str(tmp_path / 'none.onnx'))
        monkeypatch.setattr(config, 'YOLO_MODEL_URL', '')
        monkeypatch.delenv('PICARPRO_SKIP_YOLO', raising=False)
        setup_mod.stage_8_yolo_model()
        assert 'fetch_yolo.py' in capsys.readouterr().out

    def test_skip_env(self, setup_mod, monkeypatch, capsys):
        monkeypatch.setenv('PICARPRO_SKIP_YOLO', '1')
        setup_mod.stage_8_yolo_model()
        assert 'skipped' in capsys.readouterr().out


# ---- hotspot: синтаксис сгенерированного скрипта и выбор канала ----------------
def _generated_setup_script(tmp_path):
    """Вырезать из fix_wifi_hostapd.sh содержимое picarpro-hotspot-setup.sh."""
    text = open(os.path.join(ROOT, 'fix_wifi_hostapd.sh'), encoding='utf-8').read()
    m = re.search(r"picarpro-hotspot-setup\.sh\" << 'PEOF'\n(.*?)\nPEOF\n", text, re.S)
    assert m, 'не найден встроенный скрипт'
    p = tmp_path / 'hotspot-setup.sh'
    p.write_text(m.group(1) + '\n')
    return p, m.group(1)


@pytest.mark.skipif(shutil.which('bash') is None, reason='нужен bash')
class TestHotspotScript:
    def test_syntax(self, tmp_path):
        assert subprocess.run(['bash', '-n', os.path.join(ROOT, 'fix_wifi_hostapd.sh')]).returncode == 0
        p, _ = _generated_setup_script(tmp_path)
        r = subprocess.run(['bash', '-n', str(p)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def _pick(self, tmp_path, scan_text):
        _, body = _generated_setup_script(tmp_path)
        fn = re.search(r'# --- pick_channel begin\n(.*?)# --- pick_channel end', body, re.S).group(1)
        bindir = tmp_path / 'bin'
        bindir.mkdir(exist_ok=True)
        iw = bindir / 'iw'
        iw.write_text('#!/bin/sh\ncat <<"EOF"\n' + scan_text + '\nEOF\n')
        iw.chmod(iw.stat().st_mode | stat.S_IEXEC)
        script = f'DEV=wlan0\n{fn}\npick_channel\n'
        env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}')
        r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=env)
        return r.stdout.strip(), r.returncode

    @staticmethod
    def _bss(freq, dbm):
        return f'BSS aa:bb:cc:dd:ee:ff(on wlan0)\n\tfreq: {freq}\n\tsignal: {dbm} dBm\n\tSSID: x'

    def test_avoids_busy_channel(self, tmp_path):
        # шесть сильных сетей на канале 6 (2437) и одна слабая на 1 -> лучше 11
        scan = '\n'.join([self._bss(2437, -40)] * 6 + [self._bss(2412, -85)])
        assert self._pick(tmp_path, scan) == ('11', 0)

    def test_overlap_counts(self, tmp_path):
        # сети на 1 и 11 — канал 6 свободнее всех
        scan = '\n'.join([self._bss(2412, -45), self._bss(2462, -45)])
        assert self._pick(tmp_path, scan)[0] == '6'

    def test_empty_scan_fails_so_default_used(self, tmp_path):
        out, rc = self._pick(tmp_path, '')
        assert rc != 0

    def test_generated_configs(self, tmp_path):
        """Полный прогон скрипта с заглушками: проверяем содержимое конфигов."""
        _, body = _generated_setup_script(tmp_path)
        bindir = tmp_path / 'bin'
        bindir.mkdir(exist_ok=True)
        for name, content in {
            'iw': ('#!/bin/sh\ncase "$*" in\n*scan*) printf "BSS x(on wlan0)\\n\\tfreq: 2437\\n\\tsignal: -40.0 dBm\\n";;\n'
                   '*"reg get"*) echo "country DE: DFS-ETSI";;\n*phy*) echo "* 2412.0 MHz [1]";;\nesac\n'),
            'ip': '#!/bin/sh\nexit 0\n', 'rfkill': '#!/bin/sh\nexit 0\n', 'nmcli': '#!/bin/sh\nexit 0\n',
            'sleep': '#!/bin/sh\nexit 0\n',
        }.items():
            f = bindir / name
            f.write_text(content)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        (tmp_path / 'sys' / 'wlan0').mkdir(parents=True, exist_ok=True)
        body = body.replace('/sys/class/net/$DEV', str(tmp_path / 'sys') + '/$DEV')
        conf = tmp_path / 'hotspot.conf'
        conf.write_text('HOTSPOT_SSID="Bot"\nHOTSPOT_PASS="0123456789"\nHOTSPOT_CHANNEL="auto"\n'
                        'HOTSPOT_IP="10.42.0.1"\nHOTSPOT_BAND="a"\n')
        run_dir = tmp_path / 'run'
        run_dir.mkdir()
        script = tmp_path / 's.sh'
        script.write_text(body + '\n')
        env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}',
                   PICARPRO_CONF=str(conf), PICARPRO_RUN_DIR=str(run_dir))
        r = subprocess.run(['bash', str(script)], capture_output=True, text=True, env=env, timeout=30)
        assert r.returncode == 0, r.stdout + r.stderr
        ap = (run_dir / 'picarpro-hostapd.conf').read_text()
        assert 'hw_mode=g' in ap                       # 5 ГГц нет в iw phy -> откат на 2.4
        assert 'channel=1' in ap                       # на 6 сильная чужая сеть -> auto ушёл на 1
        assert 'country_code=DE' in ap and 'ieee80211d=1' in ap
        assert 'dtim_period=1' in ap and 'max_num_sta=5' in ap
        assert 'ht_capab=[SHORT-GI-20]' in ap
        dm = (run_dir / 'picarpro-dnsmasq.conf').read_text()
        assert 'address=/picar.lan/10.42.0.1' in dm
        assert 'address=/#/10.42.0.1' in dm     # wildcard: все домены -> Pi


class TestProbes:
    """Проверки связности ОС получают редирект на портал — ОС сама открывает UI."""

    @staticmethod
    def _get(port, path, host='10.42.0.1', method='GET'):
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            conn.request(method, path, headers={'Host': host})
            resp = conn.getresponse()
            return resp.status, resp.getheader('Location'), resp.read()
        finally:
            conn.close()

    @staticmethod
    def _free_port():
        import socket
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_probe_paths_recognised(self):
        from picarpro.network import is_probe
        assert is_probe('/generate_204') and is_probe('/generate_204?x=1')
        assert is_probe('/hotspot-detect.html') and is_probe('/ncsi.txt')
        assert is_probe('/redirect') and is_probe('/success.txt')
        assert not is_probe('/') and not is_probe('/app.js')

    def test_portal_location_prefers_ap_ip(self, monkeypatch):
        from picarpro.network import portal_location
        monkeypatch.setattr('picarpro.network.get_ap_ip', lambda: '10.42.0.1')
        assert portal_location('/', 'connectivitycheck.gstatic.com', 5000) \
            == 'http://10.42.0.1:5000/'
        # интерфейсов нет — падаем на Host запроса (без порта)
        monkeypatch.setattr('picarpro.network.get_ap_ip', lambda: None)
        assert portal_location('/', '10.42.0.1:80', 5000) == 'http://10.42.0.1:5000/'

    def test_server_redirects_probe_to_portal(self, monkeypatch):
        from picarpro.network import start_redirect_server
        monkeypatch.setattr('picarpro.network.get_ap_ip', lambda: '10.42.0.1')
        port = self._free_port()
        assert start_redirect_server(port=port, target_port=5000)
        status, location, _ = self._get(port, '/generate_204?x=1')
        assert (status, location) == (302, 'http://10.42.0.1:5000/')

    def test_server_keeps_path_and_falls_back_to_host(self, monkeypatch):
        from picarpro.network import start_redirect_server
        monkeypatch.setattr('picarpro.network.get_ap_ip', lambda: None)
        port = self._free_port()
        assert start_redirect_server(port=port, target_port=5000)
        status, location, _ = self._get(port, '/main.html', host='10.42.0.1:80')
        assert (status, location) == (302, 'http://10.42.0.1:5000/main.html')

    def test_head_returns_headers_without_body(self, monkeypatch):
        from picarpro.network import start_redirect_server
        monkeypatch.setattr('picarpro.network.get_ap_ip', lambda: '10.42.0.1')
        port = self._free_port()
        assert start_redirect_server(port=port, target_port=5000)
        status, location, body = self._get(port, '/hotspot-detect.html',
                                           method='HEAD')
        assert (status, location, body) == (302, 'http://10.42.0.1:5000/', b'')
