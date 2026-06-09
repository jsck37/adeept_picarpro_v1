import json, os, re, subprocess, threading, time
from flask import Blueprint, jsonify, request
from Server.logger import logger

BT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bt_config.json")


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
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                time.sleep(0.5)
                self._send('power on')
                self._send('agent on')
                self._send('default-agent')
                time.sleep(0.3)
                logger.info("[BT] Interactive bluetoothctl session started")
            except Exception as e:
                logger.error(f"[BT] Failed to start interactive session: {e}")
                self._proc = None

    def _send(self, command, wait=0.3):
        self._ensure_running()
        if not self._proc or self._proc.poll() is not None:
            return ""
        try:
            self._proc.stdin.write(command + '\n')
            self._proc.stdin.flush()
            if wait > 0:
                time.sleep(wait)
            return ""
        except Exception as e:
            logger.warning(f"[BT] Send error for '{command}': {e}")
            self._restart()
            return ""

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
            return ""

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

            output_lines = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    import select as sel
                    r, _, _ = sel.select([self._proc.stdout], [], [], min(0.5, remaining))
                    if r:
                        line = self._proc.stdout.readline()
                        if line:
                            output_lines.append(line.strip())
                            line_lower = line.lower()
                            if ('successful' in line_lower
                                    or 'failed' in line_lower
                                    or 'not available' in line_lower
                                    or 'error' in line_lower
                                    or 'yes/no' in line_lower
                                    or '[bluetooth]' in line
                                    or '[NEW]' in line
                                    or '[DEL]' in line):
                                time.sleep(0.5)
                                while True:
                                    r2, _, _ = sel.select([self._proc.stdout], [], [], 0.3)
                                    if r2:
                                        l2 = self._proc.stdout.readline()
                                        if l2:
                                            output_lines.append(l2.strip())
                                        else:
                                            break
                                    else:
                                        break
                                break
                except Exception:
                    break

            return '\n'.join(output_lines)
        except Exception as e:
            logger.warning(f"[BT] send_and_read error for '{command}': {e}")
            return ""

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


_bt_session = None

def _get_session():
    global _bt_session
    if _bt_session is None:
        _bt_session = BluetoothctlSession()
    return _bt_session


def _load_hid_sony():
    try:
        with open('/proc/modules', 'r') as f:
            for line in f:
                if line.startswith('hid_sony ') or line.startswith('hid_playstation '):
                    return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ['modprobe', 'hid-sony'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            logger.info("[BT] Loaded hid-sony kernel module")
            time.sleep(0.5)
            return True
        else:
            logger.warning(f"[BT] modprobe hid-sony failed: {result.stderr.strip()}")
    except FileNotFoundError:
        logger.warning("[BT] modprobe not found — cannot load hid-sony")
    except Exception as e:
        logger.warning(f"[BT] modprobe hid-sony error: {e}")

    try:
        result = subprocess.run(
            ['modprobe', 'hid-playstation'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            logger.info("[BT] Loaded hid-playstation kernel module")
            time.sleep(0.5)
            return True
    except Exception:
        pass

    return False


def _wait_for_evdev_device(timeout=10):
    try:
        import evdev
    except ImportError:
        logger.warning("[BT] evdev not installed — cannot wait for input device")
        return None

    ds4_keywords = ['wireless controller', 'dualshock', 'sony interactive', 'playstation']
    deadline = time.monotonic() + timeout

    logger.info(f"[BT] Waiting up to {timeout}s for evdev device...")
    while time.monotonic() < deadline:
        try:
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                    name_lower = dev.name.lower()
                    if any(kw in name_lower for kw in ds4_keywords):
                        caps = dev.capabilities()
                        keys = caps.get(evdev.ecodes.EV_KEY, [])
                        if evdev.ecodes.BTN_SOUTH in keys:
                            logger.info(f"[BT] Found evdev device: {dev.name} @ {dev.path}")
                            return dev.path
                except OSError:
                    continue
        except Exception:
            pass
        time.sleep(0.5)

    logger.warning(f"[BT] No evdev device appeared within {timeout}s")
    return None


def _btctl_simple(*args, timeout=10):
    cmd = ['bluetoothctl'] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def _scan_devices(scan_time=3):
    _load_hid_sony()

    session = _get_session()
    session._send('power on', wait=0.3)

    session._send('scan on', wait=0)
    time.sleep(scan_time)
    session._send('scan off', wait=0.5)

    output = session.send_and_read('devices', timeout=5)

    devices = []
    seen = set()
    for line in output.splitlines():
        line = line.strip()
        m = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line)
        if m:
            mac = m.group(1).upper()
            name = m.group(2).strip()
            if mac not in seen:
                seen.add(mac)
                devices.append({"name": name, "mac": mac})
    return devices


def _is_gamepad(name):
    name_lower = name.lower()
    keywords = [
        "wireless controller", "dualshock", "ds4", "ds5", "dualsense",
        "xbox", "gamepad", "8bitdo", "pro controller", "joy-con",
        "sony interactive", "playstation", "nintendo",
    ]
    return any(kw in name_lower for kw in keywords)


def _pair_and_connect(mac, ds4_controller=None):
    mac = mac.upper()

    _load_hid_sony()

    session = _get_session()

    logger.info(f"[BT] Attempting direct connect to {mac}...")
    out = session.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in out.lower():
        session._send(f'trust {mac}', wait=1)
        logger.info(f"[BT] Direct connect succeeded for {mac}")
        _post_connect(mac, ds4_controller)
        return True, f"Connected to {mac}"

    logger.info(f"[BT] Direct connect failed, full pairing for {mac}...")

    session._send(f'remove {mac}', wait=1)
    time.sleep(0.5)

    session._send('scan on', wait=0)
    time.sleep(3.0)
    session._send('scan off', wait=0.5)
    time.sleep(0.3)

    logger.info(f"[BT] Pairing with {mac}...")
    pair_out = session.send_and_read(f'pair {mac}', timeout=20)

    pair_ok = 'successful' in pair_out.lower()
    if not pair_ok:
        info_out = session.send_and_read(f'info {mac}', timeout=5)
        if 'Paired: yes' in info_out:
            pair_ok = True

    if not pair_ok:
        session._send('scan on', wait=0)
        time.sleep(2.0)
        session._send('scan off', wait=0.5)
        pair_out2 = session.send_and_read(f'pair {mac}', timeout=20)
        if 'successful' not in pair_out2.lower():
            info_out2 = session.send_and_read(f'info {mac}', timeout=5)
            if 'Paired: yes' not in info_out2:
                return False, f"Pairing failed for {mac}"

    time.sleep(0.5)

    session._send(f'trust {mac}', wait=1)
    time.sleep(0.3)

    logger.info(f"[BT] Connecting to {mac}...")
    conn_out = session.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in conn_out.lower():
        logger.info(f"[BT] Pair + connect succeeded for {mac}")
        _post_connect(mac, ds4_controller)
        return True, f"Paired and connected to {mac}"

    time.sleep(1.0)
    conn_out2 = session.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in conn_out2.lower():
        logger.info(f"[BT] Second connect attempt succeeded for {mac}")
        _post_connect(mac, ds4_controller)
        return True, f"Paired and connected to {mac}"

    return False, f"Pairing succeeded but connection failed for {mac}"


def _post_connect(mac, ds4_controller=None):
    evdev_path = _wait_for_evdev_device(timeout=10)

    if evdev_path:
        logger.info(f"[BT] DS4 input device ready: {evdev_path}")
    else:
        logger.warning("[BT] DS4 input device NOT found — keys may not respond")
        logger.warning("[BT] Check: lsmod | grep hid_sony ; ls /dev/input/")

    if ds4_controller is not None:
        try:
            ds4_controller.trigger_rescan()
            logger.info("[BT] Triggered DS4 controller rescan")
        except Exception as e:
            logger.warning(f"[BT] DS4 rescan trigger failed: {e}")


def _disconnect_device(mac):
    session = _get_session()
    session._send(f'disconnect {mac}', wait=2)
    session._send(f'remove {mac}', wait=1)
    return True


def _load_bt_config():
    try:
        if os.path.isfile(BT_CONFIG_FILE):
            with open(BT_CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_bt_config(config):
    try:
        with open(BT_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"[BT] Config save error: {e}")


def auto_connect_on_boot(ds4_controller=None):
    cfg = _load_bt_config()
    mac = cfg.get("last_gamepad_mac")
    if not mac:
        logger.info("[BT] No saved gamepad MAC — skipping auto-connect")
        return

    def _do_auto_connect():
        time.sleep(3.0)
        logger.info(f"[BT] Auto-connecting to saved gamepad {mac}...")
        success, msg = _pair_and_connect(mac, ds4_controller)
        if success:
            logger.info(f"[BT] Auto-connect success: {msg}")
        else:
            logger.warning(f"[BT] Auto-connect failed: {msg}")

    t = threading.Thread(target=_do_auto_connect, daemon=True)
    t.start()


def create_bluetooth_blueprint(state):
    bp = Blueprint("bt", __name__, url_prefix="/api/bt")

    @bp.route("/scan", methods=["GET"])
    def bt_scan():
        try:
            devices = _scan_devices(scan_time=3)
            for d in devices:
                d["is_gamepad"] = _is_gamepad(d["name"])
            devices.sort(key=lambda d: (0 if d["is_gamepad"] else 1, d["name"]))
            logger.info(f"[BT] Scan found {len(devices)} devices")
            return jsonify({"ok": True, "devices": devices})
        except Exception as e:
            logger.error(f"[BT] Scan error: {e}")
            return jsonify({"ok": False, "error": str(e), "devices": []})

    @bp.route("/connect", methods=["POST"])
    def bt_connect():
        data = request.get_json(silent=True) or {}
        mac = data.get("mac", "").strip()
        if not mac:
            return jsonify({"ok": False, "error": "MAC address required"})

        result = {"ok": False, "message": ""}
        done = threading.Event()

        def _do_connect():
            ds4 = getattr(state, 'ds4', None)
            success, msg = _pair_and_connect(mac, ds4_controller=ds4)
            result["ok"] = success
            result["message"] = msg
            if success:
                cfg = _load_bt_config()
                cfg["last_gamepad_mac"] = mac.upper()
                cfg["last_gamepad_name"] = data.get("name", "")
                _save_bt_config(cfg)
            done.set()

        t = threading.Thread(target=_do_connect, daemon=True)
        t.start()
        done.wait(timeout=45)

        return jsonify(result)

    @bp.route("/disconnect", methods=["POST"])
    def bt_disconnect():
        data = request.get_json(silent=True) or {}
        mac = data.get("mac", "").strip()
        cfg = _load_bt_config()
        if not mac and cfg.get("last_gamepad_mac"):
            mac = cfg["last_gamepad_mac"]
        if mac:
            _disconnect_device(mac)
        cfg.pop("last_gamepad_mac", None)
        cfg.pop("last_gamepad_name", None)
        _save_bt_config(cfg)
        return jsonify({"ok": True})

    @bp.route("/status", methods=["GET"])
    def bt_status():
        cfg = _load_bt_config()
        ds4_connected = state.ds4.connected if state.ds4 else False
        hid_sony_loaded = False
        try:
            with open('/proc/modules', 'r') as f:
                for line in f:
                    if line.startswith('hid_sony ') or line.startswith('hid_playstation '):
                        hid_sony_loaded = True
                        break
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "connected": ds4_connected,
            "saved_mac": cfg.get("last_gamepad_mac"),
            "saved_name": cfg.get("last_gamepad_name"),
            "hid_sony_loaded": hid_sony_loaded,
        })

    @bp.route("/auto_connect", methods=["POST"])
    def bt_auto_connect():
        cfg = _load_bt_config()
        mac = cfg.get("last_gamepad_mac")
        if not mac:
            return jsonify({"ok": False, "error": "No saved gamepad MAC"})

        result = {"ok": False, "message": ""}
        done = threading.Event()

        def _do_auto():
            ds4 = getattr(state, 'ds4', None)
            success, msg = _pair_and_connect(mac, ds4_controller=ds4)
            result["ok"] = success
            result["message"] = msg
            done.set()

        t = threading.Thread(target=_do_auto, daemon=True)
        t.start()
        done.wait(timeout=45)

        return jsonify(result)

    @bp.route("/load_hid_sony", methods=["POST"])
    def bt_load_hid_sony():
        ok = _load_hid_sony()
        return jsonify({"ok": ok, "message": "hid-sony loaded" if ok else "Failed to load hid-sony"})

    return bp
