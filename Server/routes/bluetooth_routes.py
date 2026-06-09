#!/usr/bin/env python3
"""Bluetooth API blueprint — DS4 gamepad scanning, pairing & auto-connect.

All routes are registered under the ``/api/bt`` url prefix.

Uses individual ``bluetoothctl`` subprocess calls with strict timeouts
for each operation.  This is more reliable than an interactive session
because each command starts fresh and cannot get stuck in a bad state.

A small JSON config file (``bt_config.json``) stores the MAC address of
the last successfully connected gamepad so that auto-connect can work on
boot without user interaction.
"""

import json, os, re, subprocess, threading, time
from flask import Blueprint, jsonify, request
from Server.logger import logger

BT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bt_config.json")

# ---------------------------------------------------------------------------
# bluetoothctl helpers — one subprocess per command (reliable)
# ---------------------------------------------------------------------------

def _btctl(*args, timeout=10):
    """Run a single bluetoothctl command with a strict timeout.

    Returns the combined stdout+stderr as a string.
    """
    cmd = ['bluetoothctl'] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        output = result.stdout + result.stderr
        return output
    except subprocess.TimeoutExpired:
        logger.warning(f"[BT] Command timed out: {cmd}")
        return ""
    except Exception as e:
        logger.error(f"[BT] Command error: {e}")
        return ""


def _scan_devices(scan_time=3):
    """Scan for nearby Bluetooth devices.

    Starts a background scan, waits, then reads the device list.
    Returns a list of dicts: [{"name": "...", "mac": "XX:XX:XX:XX:XX:XX"}, ...]
    """
    # Power on and start scan in background
    _btctl('power', 'on', timeout=5)
    time.sleep(0.2)

    # Start scan (fire and forget — runs in bluetoothctl's own process)
    scan_proc = subprocess.Popen(
        ['bluetoothctl', 'scan', 'on'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for devices to appear
    time.sleep(scan_time)

    # Stop scan
    try:
        scan_proc.terminate()
        scan_proc.wait(timeout=2)
    except Exception:
        try:
            scan_proc.kill()
        except Exception:
            pass

    # Read device list
    output = _btctl('devices', timeout=5)

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


def _pair_and_connect(mac):
    """Pair, trust and connect to a Bluetooth device by MAC address.

    Strategy:
      1. Try direct connect first (device may already be paired/trusted)
      2. If that fails, do remove → pair → trust → connect
      3. Each step has its own timeout to prevent hanging

    Returns (success: bool, message: str).
    """
    mac = mac.upper()
    _btctl('power', 'on', timeout=5)
    _btctl('agent', 'on', timeout=5)
    _btctl('default-agent', timeout=5)
    time.sleep(0.3)

    # ── Step 1: Try direct connect (device may already be paired) ──
    logger.info(f"[BT] Attempting direct connect to {mac}...")
    out = _btctl('connect', mac, timeout=10)
    if 'successful' in out.lower():
        # Already paired — ensure trusted
        _btctl('trust', mac, timeout=5)
        logger.info(f"[BT] Direct connect succeeded for {mac}")
        return True, f"Connected to {mac}"

    # ── Step 2: Full pair sequence ──
    logger.info(f"[BT] Direct connect failed, full pairing for {mac}...")

    # Remove any existing pairing
    _btctl('remove', mac, timeout=5)
    time.sleep(0.5)

    # Start scan so device is discoverable
    scan_proc = subprocess.Popen(
        ['bluetoothctl', 'scan', 'on'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2.0)
    try:
        scan_proc.terminate()
        scan_proc.wait(timeout=2)
    except Exception:
        try:
            scan_proc.kill()
        except Exception:
            pass
    time.sleep(0.3)

    # Pair
    logger.info(f"[BT] Pairing with {mac}...")
    pair_out = _btctl('pair', mac, timeout=15)
    if 'successful' not in pair_out.lower() and 'Failed' in pair_out:
        # Some bluetoothctl versions report pairing differently
        # Check if device appears in paired list
        info_out = _btctl('info', mac, timeout=5)
        if 'Paired: yes' not in info_out:
            return False, f"Pairing failed for {mac}"

    time.sleep(0.5)

    # Trust
    _btctl('trust', mac, timeout=5)
    time.sleep(0.3)

    # Connect
    logger.info(f"[BT] Connecting to {mac}...")
    conn_out = _btctl('connect', mac, timeout=10)
    if 'successful' in conn_out.lower():
        logger.info(f"[BT] Pair + connect succeeded for {mac}")
        return True, f"Paired and connected to {mac}"

    return False, f"Pairing succeeded but connection failed for {mac}"


def _disconnect_device(mac):
    """Disconnect and remove a Bluetooth device."""
    _btctl('disconnect', mac, timeout=5)
    time.sleep(0.3)
    _btctl('remove', mac, timeout=5)
    return True


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

    This runs in a background thread after a short delay.  If the
    bluetoothctl connect succeeds, the DS4 watchdog will detect the
    new evdev device and pick it up automatically.
    """
    cfg = _load_bt_config()
    mac = cfg.get("last_gamepad_mac")
    if not mac:
        logger.info("[BT] No saved gamepad MAC — skipping auto-connect")
        return

    def _do_auto_connect():
        time.sleep(3.0)
        logger.info(f"[BT] Auto-connecting to saved gamepad {mac}...")
        success, msg = _pair_and_connect(mac)
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
                cfg = _load_bt_config()
                cfg["last_gamepad_mac"] = mac.upper()
                cfg["last_gamepad_name"] = data.get("name", "")
                _save_bt_config(cfg)
            done.set()

        t = threading.Thread(target=_do_connect, daemon=True)
        t.start()
        done.wait(timeout=30)

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
        done.wait(timeout=30)

        return jsonify(result)

    return bp
