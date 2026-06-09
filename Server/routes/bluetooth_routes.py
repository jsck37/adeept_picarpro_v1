#!/usr/bin/env python3
"""Bluetooth API blueprint — DS4 gamepad scanning, pairing & auto-connect.

All routes are registered under the ``/api/bt`` url prefix.

Uses ``bluetoothctl`` via an interactive session (instead of spawning a new
subprocess for every command) for fast and reliable operation.

A small JSON config file (``bt_config.json``) stores the MAC address of
the last successfully connected gamepad so that auto-connect can work on
boot without user interaction.
"""

import json, os, re, subprocess, threading, time
from flask import Blueprint, jsonify, request
from Server.logger import logger

BT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bt_config.json")

# ---------------------------------------------------------------------------
# Interactive bluetoothctl session (fast — no subprocess spawn per command)
# ---------------------------------------------------------------------------

class BluetoothctlSession:
    """Persistent bluetoothctl session for low-latency commands.

    Instead of spawning a new ``bluetoothctl`` subprocess for every single
    command (which takes 1-2 seconds each time due to D-Bus setup), we keep
    an interactive session open and write commands to its stdin.  This makes
    scanning and pairing much faster.
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._ensure_running()

    def _ensure_running(self):
        """Start bluetoothctl if not already running."""
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
            # Give it a moment to initialize
            time.sleep(0.3)
            # Power on adapter
            self._write_cmd('power on')
            time.sleep(0.2)
            self._write_cmd('agent on')
            self._write_cmd('default-agent')
            time.sleep(0.1)
            logger.info("[BT] bluetoothctl session started")
        except Exception as e:
            logger.error(f"[BT] Failed to start bluetoothctl: {e}")
            self._proc = None

    def _write_cmd(self, cmd):
        """Write a command to bluetoothctl stdin."""
        if not self._proc or self._proc.poll() is not None:
            self._ensure_running()
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(cmd + '\n')
                self._proc.stdin.flush()
            except Exception:
                self._proc = None

    def _read_output(self, timeout=5.0, until=None):
        """Read output from bluetoothctl until timeout or a pattern is matched.

        Returns list of output lines.
        """
        lines = []
        if not self._proc or self._proc.poll() is not None:
            return lines
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    lines.append(line)
                    if until and until.search(line):
                        break
        except Exception:
            pass
        return lines

    def send_and_read(self, cmd, timeout=5.0, until=None):
        """Send a command and read the response."""
        with self._lock:
            self._ensure_running()
            # Consume any stale output first
            try:
                while self._proc and self._proc.stdout:
                    import select as sel
                    if sel.select([self._proc.stdout], [], [], 0.05)[0]:
                        self._proc.stdout.readline()
                    else:
                        break
            except Exception:
                pass
            self._write_cmd(cmd)
            return self._read_output(timeout=timeout, until=until)

    def shutdown(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


# Global session (shared across all requests)
_bt_session = None
_bt_session_lock = threading.Lock()


def _get_bt_session():
    global _bt_session
    with _bt_session_lock:
        if _bt_session is None:
            _bt_session = BluetoothctlSession()
        return _bt_session


# ---------------------------------------------------------------------------
# bluetoothctl helpers
# ---------------------------------------------------------------------------

def _scan_devices(scan_time=3):
    """Scan for nearby Bluetooth devices using interactive bluetoothctl.

    Returns a list of dicts: [{"name": "...", "mac": "XX:XX:XX:XX:XX:XX"}, ...]
    """
    bt = _get_bt_session()

    # Start scan
    bt.send_and_read('scan on', timeout=1.0)
    time.sleep(scan_time)

    # Read device list
    lines = bt.send_and_read('devices', timeout=3.0,
                             until=re.compile(r'Device\s', re.IGNORECASE))

    # Stop scan (don't wait for output)
    bt._write_cmd('scan off')
    time.sleep(0.2)

    devices = []
    seen = set()
    for line in lines:
        # Parse "Device AA:BB:CC:DD:EE:FF Device Name"
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


def _pair_and_connect(mac):
    """Pair, trust and connect to a Bluetooth device by MAC address.

    Uses the interactive bluetoothctl session for faster operation.
    Does NOT remove existing pairing first — this avoids the common
    problem where ``remove`` + ``pair`` fails because the controller
    needs time to reset.

    Returns (success: bool, message: str).
    """
    bt = _get_bt_session()
    mac = mac.upper()

    # First, try to connect directly (device may already be paired/trusted)
    logger.info(f"[BT] Attempting direct connect to {mac}...")
    lines = bt.send_and_read(f'connect {mac}', timeout=10.0,
                             until=re.compile(r'(Connection successful|Failed to connect)', re.IGNORECASE))
    for line in lines:
        if 'successful' in line.lower():
            # Already paired — just needed connect
            bt.send_and_read(f'trust {mac}', timeout=3.0)
            return True, f"Connected to {mac}"

    # Direct connect failed — try full pair sequence
    logger.info(f"[BT] Direct connect failed, pairing {mac}...")

    # Remove old pairing (only if direct connect failed)
    bt.send_and_read(f'remove {mac}', timeout=5.0)
    time.sleep(0.5)

    # Scan briefly to rediscover
    bt._write_cmd('scan on')
    time.sleep(2.0)
    bt._write_cmd('scan off')
    time.sleep(0.3)

    # Pair
    lines = bt.send_and_read(f'pair {mac}', timeout=15.0,
                             until=re.compile(r'(Pairing successful|Failed to pair)', re.IGNORECASE))
    paired = any('successful' in l.lower() for l in lines)
    if not paired:
        return False, f"Pairing failed for {mac}"

    time.sleep(0.3)

    # Trust
    bt.send_and_read(f'trust {mac}', timeout=3.0)
    time.sleep(0.2)

    # Connect
    lines = bt.send_and_read(f'connect {mac}', timeout=10.0,
                             until=re.compile(r'(Connection successful|Failed to connect)', re.IGNORECASE))
    for line in lines:
        if 'successful' in line.lower():
            return True, f"Paired and connected to {mac}"

    return False, f"Pairing succeeded but connection failed for {mac}"


def _disconnect_device(mac):
    """Disconnect and remove a Bluetooth device."""
    bt = _get_bt_session()
    bt.send_and_read(f'disconnect {mac}', timeout=5.0)
    time.sleep(0.3)
    bt.send_and_read(f'remove {mac}', timeout=5.0)
    return True


def _get_connected_devices():
    """Get list of currently connected Bluetooth devices."""
    bt = _get_bt_session()
    lines = bt.send_and_read('devices Connected', timeout=5.0)
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

def _load_bt_config():
    """Load saved Bluetooth config."""
    try:
        if os.path.isfile(BT_CONFIG_FILE):
            with open(BT_CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_bt_config(config):
    """Save Bluetooth config."""
    try:
        with open(BT_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"[BT] Config save error: {e}")


# ---------------------------------------------------------------------------
# Auto-connect on startup
# ---------------------------------------------------------------------------

def auto_connect_on_boot(ds4_controller=None):
    """Attempt to auto-connect to the last known gamepad on startup.

    This is called after the DS4 controller is started, so that if a
    previously paired gamepad is powered on, it will be connected
    automatically via bluetoothctl (which triggers the evdev device to
    appear, and then the DS4 watchdog picks it up).
    """
    cfg = _load_bt_config()
    mac = cfg.get("last_gamepad_mac")
    if not mac:
        logger.info("[BT] No saved gamepad MAC — skipping auto-connect")
        return

    def _do_auto_connect():
        # Wait a bit for system to stabilize
        time.sleep(3.0)
        logger.info(f"[BT] Auto-connecting to saved gamepad {mac}...")
        success, msg = _pair_and_connect(mac)
        if success:
            logger.info(f"[BT] Auto-connect success: {msg}")
        else:
            logger.warning(f"[BT] Auto-connect failed: {msg}")
            # The DS4 watchdog will keep looking for evdev devices anyway

    t = threading.Thread(target=_do_auto_connect, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

def create_bluetooth_blueprint(state):
    """Return a Blueprint that provides Bluetooth management endpoints.

    Parameters
    ----------
    state : SharedState
        The shared robot state object.
    """
    bp = Blueprint("bt", __name__, url_prefix="/api/bt")

    @bp.route("/scan", methods=["GET"])
    def bt_scan():
        """Scan for nearby Bluetooth devices.

        Returns a list of found devices sorted by whether they look like
        gamepads (gamepads first).
        """
        try:
            devices = _scan_devices(scan_time=3)
            # Mark gamepad-like devices
            for d in devices:
                d["is_gamepad"] = _is_gamepad(d["name"])
            # Sort: gamepads first, then by name
            devices.sort(key=lambda d: (0 if d["is_gamepad"] else 1, d["name"]))
            logger.info(f"[BT] Scan found {len(devices)} devices")
            return jsonify({"ok": True, "devices": devices})
        except Exception as e:
            logger.error(f"[BT] Scan error: {e}")
            return jsonify({"ok": False, "error": str(e), "devices": []})

    @bp.route("/connect", methods=["POST"])
    def bt_connect():
        """Connect to a Bluetooth device by MAC address.

        Expects JSON: {"mac": "XX:XX:XX:XX:XX:XX"}
        """
        data = request.get_json(silent=True) or {}
        mac = data.get("mac", "").strip()
        if not mac:
            return jsonify({"ok": False, "error": "MAC address required"})

        result = {"ok": False, "message": ""}
        done = threading.Event()

        def _do_connect():
            success, msg = _pair_and_connect(mac)
            result["ok"] = success
            result["message"] = msg
            if success:
                # Save to config for auto-connect
                cfg = _load_bt_config()
                cfg["last_gamepad_mac"] = mac.upper()
                cfg["last_gamepad_name"] = data.get("name", "")
                _save_bt_config(cfg)
            done.set()

        t = threading.Thread(target=_do_connect, daemon=True)
        t.start()
        done.wait(timeout=25)

        return jsonify(result)

    @bp.route("/disconnect", methods=["POST"])
    def bt_disconnect():
        """Disconnect from the currently connected Bluetooth gamepad."""
        data = request.get_json(silent=True) or {}
        mac = data.get("mac", "").strip()
        cfg = _load_bt_config()
        if not mac and cfg.get("last_gamepad_mac"):
            mac = cfg["last_gamepad_mac"]
        if mac:
            _disconnect_device(mac)
        # Clear config
        cfg.pop("last_gamepad_mac", None)
        cfg.pop("last_gamepad_name", None)
        _save_bt_config(cfg)
        return jsonify({"ok": True})

    @bp.route("/status", methods=["GET"])
    def bt_status():
        """Return the current Bluetooth connection status and saved config."""
        cfg = _load_bt_config()
        ds4_connected = state.ds4.connected if state.ds4 else False
        return jsonify({
            "ok": True,
            "connected": ds4_connected,
            "saved_mac": cfg.get("last_gamepad_mac"),
            "saved_name": cfg.get("last_gamepad_name"),
        })

    @bp.route("/auto_connect", methods=["POST"])
    def bt_auto_connect():
        """Attempt to auto-connect to the last known gamepad."""
        cfg = _load_bt_config()
        mac = cfg.get("last_gamepad_mac")
        if not mac:
            return jsonify({"ok": False, "error": "No saved gamepad MAC"})

        result = {"ok": False, "message": ""}
        done = threading.Event()

        def _do_auto():
            success, msg = _pair_and_connect(mac)
            result["ok"] = success
            result["message"] = msg
            done.set()

        t = threading.Thread(target=_do_auto, daemon=True)
        t.start()
        done.wait(timeout=25)

        return jsonify(result)

    return bp
