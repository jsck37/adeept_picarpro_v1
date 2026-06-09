#!/usr/bin/env python3
"""Bluetooth API blueprint — DS4 gamepad scanning, pairing & auto-connect.

All routes are registered under the ``/api/bt`` url prefix.

Uses an INTERACTIVE bluetoothctl session (persistent agent) for reliable
pairing and connection.  This is critical because without a running agent,
bluetoothctl connect may fail silently — this is why the gamepad only
worked when the desktop Bluetooth UI (which runs its own agent) was open.

The interactive session keeps a bluetoothctl process running with
'agent on' + 'default-agent' active, so all pairing handshakes work.

After a successful BT connection, the ``hid-sony`` kernel module is
loaded (if not already present) so that the DS4 gamepad creates a
proper /dev/input/eventX device that evdev can read.

A small JSON config file (``bt_config.json``) stores the MAC address of
the last successfully connected gamepad so that auto-connect can work on
boot without user interaction.
"""

import json, os, re, subprocess, threading, time
from flask import Blueprint, jsonify, request
from Server.logger import logger

BT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bt_config.json")

# ---------------------------------------------------------------------------
# Interactive bluetoothctl session — keeps agent alive for pairing
# ---------------------------------------------------------------------------

class BluetoothctlSession:
    """Persistent interactive bluetoothctl session.

    WHY: Without a running Bluetooth agent, `bluetoothctl connect` fails
    silently because no agent is available to handle the pairing handshake.
    The desktop Bluetooth UI (blueman, GNOME Bluetooth, etc.) runs its own
    agent — that's why connecting only worked when the UI was open.

    This class keeps a bluetoothctl process running with 'agent on' and
    'default-agent' so that pairing always works, even headless.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._ensure_running()

    def _ensure_running(self):
        """Start the interactive session if not running."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return  # Already running
            try:
                self._proc = subprocess.Popen(
                    ['bluetoothctl'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,  # Line buffered
                )
                time.sleep(0.5)
                # Register agent for pairing
                self._send('power on')
                self._send('agent on')
                self._send('default-agent')
                time.sleep(0.3)
                logger.info("[BT] Interactive bluetoothctl session started")
            except Exception as e:
                logger.error(f"[BT] Failed to start interactive session: {e}")
                self._proc = None

    def _send(self, command, wait=0.3):
        """Send a command to the interactive bluetoothctl session."""
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
        """Kill and restart the session."""
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
        """Send a command and read the output until a prompt or timeout.

        Returns the output text.
        """
        self._ensure_running()
        if not self._proc or self._proc.poll() is not None:
            return ""

        try:
            # Drain any pending output first
            import select as sel
            while True:
                r, _, _ = sel.select([self._proc.stdout], [], [], 0.1)
                if not r:
                    break
                try:
                    self._proc.stdout.readline()
                except Exception:
                    break

            # Send command
            self._proc.stdin.write(command + '\n')
            self._proc.stdin.flush()

            # Read output until timeout
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
                            # Check for terminal indicators
                            line_lower = line.lower()
                            if ('successful' in line_lower
                                    or 'failed' in line_lower
                                    or 'not available' in line_lower
                                    or 'error' in line_lower
                                    or 'yes/no' in line_lower
                                    or '[bluetooth]' in line
                                    or '[NEW]' in line
                                    or '[DEL]' in line):
                                # Give a small window for more output
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


# Global session
_bt_session = None

def _get_session():
    global _bt_session
    if _bt_session is None:
        _bt_session = BluetoothctlSession()
    return _bt_session


# ---------------------------------------------------------------------------
# Kernel module helpers
# ---------------------------------------------------------------------------

def _load_hid_sony():
    """Try to load the hid-sony kernel module (DS4 Bluetooth support).

    Without hid-sony, the DS4 connects at the Bluetooth level but does
    NOT create a /dev/input/eventX device, so evdev cannot read it.
    This is the #1 reason why a DS4 "connects" but keys don't respond.
    """
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

    # Try hid-playstation as fallback (newer kernels)
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
    """Wait for a new DS4-compatible evdev device to appear.

    After bluetoothctl connects the DS4, there is a delay (1-5 seconds)
    before the kernel creates the /dev/input/eventX device.  This
    function polls until a matching device appears or the timeout expires.

    Returns the device path or None.
    """
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


# ---------------------------------------------------------------------------
# bluetoothctl helpers
# ---------------------------------------------------------------------------

def _btctl_simple(*args, timeout=10):
    """Run a single bluetoothctl command (for non-critical operations like scan)."""
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
    """Scan for nearby Bluetooth devices.

    Returns a list of dicts: [{"name": "...", "mac": "XX:XX:XX:XX:XX:XX"}, ...]
    """
    # Ensure hid-sony is loaded before scanning
    _load_hid_sony()

    session = _get_session()
    session._send('power on', wait=0.3)

    # Start scan
    session._send('scan on', wait=0)

    # Wait for devices to appear
    time.sleep(scan_time)

    # Stop scan
    session._send('scan off', wait=0.5)

    # Read device list using the interactive session
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
    """Check if a device name looks like a gamepad/controller."""
    name_lower = name.lower()
    keywords = [
        "wireless controller", "dualshock", "ds4", "ds5", "dualsense",
        "xbox", "gamepad", "8bitdo", "pro controller", "joy-con",
        "sony interactive", "playstation", "nintendo",
    ]
    return any(kw in name_lower for kw in keywords)


def _pair_and_connect(mac, ds4_controller=None):
    """Pair, trust and connect to a Bluetooth device by MAC address.

    Uses the INTERACTIVE bluetoothctl session which keeps a pairing agent
    alive — this is essential for headless operation without a desktop
    Bluetooth UI.

    Strategy:
      1. Load hid-sony kernel module
      2. Try direct connect (device may already be paired/trusted)
      3. If that fails, do remove -> pair -> trust -> connect
      4. Wait for evdev device to appear
      5. Trigger DS4 controller rescan

    Returns (success: bool, message: str).
    """
    mac = mac.upper()

    # Step 0: Ensure hid-sony is loaded
    _load_hid_sony()

    session = _get_session()

    # Step 1: Try direct connect (device may already be paired)
    logger.info(f"[BT] Attempting direct connect to {mac}...")
    out = session.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in out.lower():
        session._send(f'trust {mac}', wait=1)
        logger.info(f"[BT] Direct connect succeeded for {mac}")
        _post_connect(mac, ds4_controller)
        return True, f"Connected to {mac}"

    # Step 2: Full pair sequence
    logger.info(f"[BT] Direct connect failed, full pairing for {mac}...")

    # Remove any existing pairing
    session._send(f'remove {mac}', wait=1)
    time.sleep(0.5)

    # Start scan so device is discoverable
    session._send('scan on', wait=0)
    time.sleep(3.0)
    session._send('scan off', wait=0.5)
    time.sleep(0.3)

    # Pair — the interactive session has agent on, so pairing works
    logger.info(f"[BT] Pairing with {mac}...")
    pair_out = session.send_and_read(f'pair {mac}', timeout=20)

    # Check if pairing succeeded
    pair_ok = 'successful' in pair_out.lower()
    if not pair_ok:
        # Some versions report differently
        info_out = session.send_and_read(f'info {mac}', timeout=5)
        if 'Paired: yes' in info_out:
            pair_ok = True

    if not pair_ok:
        # Try again with scan
        session._send('scan on', wait=0)
        time.sleep(2.0)
        session._send('scan off', wait=0.5)
        pair_out2 = session.send_and_read(f'pair {mac}', timeout=20)
        if 'successful' not in pair_out2.lower():
            info_out2 = session.send_and_read(f'info {mac}', timeout=5)
            if 'Paired: yes' not in info_out2:
                return False, f"Pairing failed for {mac}"

    time.sleep(0.5)

    # Trust
    session._send(f'trust {mac}', wait=1)
    time.sleep(0.3)

    # Connect
    logger.info(f"[BT] Connecting to {mac}...")
    conn_out = session.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in conn_out.lower():
        logger.info(f"[BT] Pair + connect succeeded for {mac}")
        _post_connect(mac, ds4_controller)
        return True, f"Paired and connected to {mac}"

    # One more try
    time.sleep(1.0)
    conn_out2 = session.send_and_read(f'connect {mac}', timeout=15)
    if 'successful' in conn_out2.lower():
        logger.info(f"[BT] Second connect attempt succeeded for {mac}")
        _post_connect(mac, ds4_controller)
        return True, f"Paired and connected to {mac}"

    return False, f"Pairing succeeded but connection failed for {mac}"


def _post_connect(mac, ds4_controller=None):
    """After a successful BT connection, wait for evdev device and
    trigger the DS4 controller to rescan for input devices.
    """
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
    """Disconnect and remove a Bluetooth device."""
    session = _get_session()
    session._send(f'disconnect {mac}', wait=2)
    session._send(f'remove {mac}', wait=1)
    return True


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Auto-connect on startup
# ---------------------------------------------------------------------------

def auto_connect_on_boot(ds4_controller=None):
    """Attempt to auto-connect to the last known gamepad on startup."""
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


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

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
