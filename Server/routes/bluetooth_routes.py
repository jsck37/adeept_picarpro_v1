import json, os, re, subprocess, threading, time
from Server.logger import logger

BT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'bt_config.json')


class BluetoothctlSession:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._ensure_running()

    def _ensure_running(self):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            try:
                self._proc = subprocess.Popen(
                    ['bluetoothctl'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
                time.sleep(0.5)
                self._send('power on')
                self._send('agent on')
                self._send('default-agent')
                time.sleep(0.3)
                logger.info('[BT] bluetoothctl session started')
            except Exception as e:
                logger.error(f'[BT] failed to start session: {e}')
                self._proc = None

    def _send(self, command, wait=0.3):
        self._ensure_running()
        if not self._proc or self._proc.poll() is not None:
            return
        try:
            self._proc.stdin.write(command + '\n')
            self._proc.stdin.flush()
            if wait > 0:
                time.sleep(wait)
        except Exception:
            self._restart()

    def _restart(self):
        with self._lock:
            if self._proc:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
        self._ensure_running()

    def send_and_read(self, command, timeout=10):
        self._ensure_running()
        if not self._proc or self._proc.poll() is not None:
            return ''
        try:
            import select as sel
            while True:
                r, _, _ = sel.select([self._proc.stdout], [], [], 0.1)
                if not r:
                    break
                try:
                    self._proc.stdout.readline()
                except Exception:
                    break
            self._proc.stdin.write(command + '\n')
            self._proc.stdin.flush()
            out = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    r, _, _ = sel.select([self._proc.stdout], [], [], min(0.5, remaining))
                    if r:
                        line = self._proc.stdout.readline()
                        if line:
                            out.append(line.strip())
                            low = line.lower()
                            if any(k in low for k in ('successful', 'failed',
                                                       'not available', 'error',
                                                       'yes/no', '[bluetooth]',
                                                       '[NEW]', '[DEL]')):
                                time.sleep(0.3)
                                while True:
                                    r2, _, _ = sel.select([self._proc.stdout], [], [], 0.3)
                                    if r2:
                                        l2 = self._proc.stdout.readline()
                                        if l2:
                                            out.append(l2.strip())
                                        else:
                                            break
                                    else:
                                        break
                                break
                except Exception:
                    break
            return '\n'.join(out)
        except Exception:
            return ''

    def shutdown(self):
        if self._proc:
            try:
                self._proc.stdin.write('quit\n')
                self._proc.stdin.flush()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


_session = None
_session_lock = threading.Lock()


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            _session = BluetoothctlSession()
        return _session


def _load_hid_sony():
    try:
        with open('/proc/modules') as f:
            for line in f:
                if line.startswith('hid_sony ') or line.startswith('hid_playstation '):
                    return True
    except Exception:
        pass
    try:
        subprocess.run(['modprobe', 'hid-sony'],
                       capture_output=True, text=True, timeout=5)
        return True
    except Exception:
        return False


def _hcitool_scan(scan_time=8):
    devices = []
    try:
        result = subprocess.run(
            ['hcitool', 'scan', f'--length={max(1, min(scan_time, 16))}'],
            capture_output=True, text=True, timeout=scan_time + 5,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                m = re.match(r'([0-9A-Fa-f:]{17})\s+(.*)', line)
                if m:
                    devices.append({
                        'name': m.group(2).strip(),
                        'mac': m.group(1).upper(),
                    })
    except Exception as e:
        logger.warning(f'[BT] hcitool scan failed: {e}')
    return devices


def _bluetoothctl_scan(scan_time=6):
    _load_hid_sony()
    s = _get_session()
    s._send('power on', wait=0.5)
    try:
        import select as sel
        while True:
            r, _, _ = sel.select([s._proc.stdout], [], [], 0.1)
            if not r:
                break
            try:
                s._proc.stdout.readline()
            except Exception:
                break
    except Exception:
        pass
    s._send('scan on', wait=0)
    time.sleep(scan_time)
    s._send('scan off', wait=1.0)
    output = s.send_and_read('devices', timeout=5)
    devices = []
    seen = set()
    for line in output.splitlines():
        line = line.strip()
        m = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line)
        if m:
            mac = m.group(1).upper()
            if mac not in seen:
                seen.add(mac)
                devices.append({'name': m.group(2).strip(), 'mac': mac})
    return devices


def scan_devices(scan_time=6):
    devices = _bluetoothctl_scan(scan_time=scan_time)
    if devices:
        return devices
    logger.info('[BT] bluetoothctl empty — trying hcitool')
    return _hcitool_scan(scan_time=max(scan_time, 8))


def pair_and_connect(mac, ds4_controller=None, timeout=30):
    mac = mac.upper()
    _load_hid_sony()
    s = _get_session()
    logger.info(f'[BT] connecting to {mac}...')
    out = s.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in out.lower():
        s._send(f'trust {mac}', wait=1)
        _post_connect(mac, ds4_controller)
        return True, f'Connected to {mac}'
    logger.info(f'[BT] direct connect failed, full pairing...')
    s._send(f'remove {mac}', wait=1)
    time.sleep(0.5)
    s._send('scan on', wait=0)
    time.sleep(3.0)
    s._send('scan off', wait=0.5)
    time.sleep(0.3)
    pair_out = s.send_and_read(f'pair {mac}', timeout=20)
    if 'successful' not in pair_out.lower():
        info = s.send_and_read(f'info {mac}', timeout=5)
        if 'Paired: yes' not in info:
            return False, f'Pairing failed for {mac}'
    time.sleep(0.5)
    s._send(f'trust {mac}', wait=1)
    time.sleep(0.3)
    conn_out = s.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in conn_out.lower():
        _post_connect(mac, ds4_controller)
        return True, f'Paired and connected to {mac}'
    time.sleep(1.0)
    conn_out2 = s.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in conn_out2.lower():
        _post_connect(mac, ds4_controller)
        return True, f'Connected to {mac}'
    return False, f'Connection failed for {mac}'


def _post_connect(mac, ds4_controller=None):
    if ds4_controller is not None:
        try:
            ds4_controller.trigger_rescan()
        except Exception:
            pass


def disconnect_device(mac):
    if not mac:
        return
    s = _get_session()
    s._send(f'disconnect {mac}', wait=2)
    s._send(f'remove {mac}', wait=1)


def load_config():
    import json
    try:
        if os.path.isfile(BT_CONFIG_FILE):
            with open(BT_CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(cfg):
    import json
    try:
        with open(BT_CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
