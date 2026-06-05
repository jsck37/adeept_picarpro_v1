#!/usr/bin/env python3
"""Bluetooth API blueprint — DS4 gamepad scanning, pairing & auto-connect.

All routes are registered under the ``/api/bt`` url prefix.

Uses ``bluetoothctl`` (via subprocess) for scanning and pairing because
it works reliably on Raspberry Pi OS with BlueZ.  For trusting and
connecting we also use ``bluetoothctl`` commands.

A small JSON config file (``bt_config.json``) stores the MAC address of
the last successfully connected gamepad so that auto-connect can work on
boot without user interaction.
"""

import json, os, subprocess, threading, time
from flask import Blueprint, jsonify, request
from Server.logger import logger

BT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bt_config.json")

# ---------------------------------------------------------------------------
# bluetoothctl helpers
# ---------------------------------------------------------------------------

def _btctl_cmd(*args, timeout=10):
    """Run a bluetoothctl command and return its output."""
    cmd = ["bluetoothctl"] + list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        logger.error(f"[BT] bluetoothctl error: {e}")
        return ""


def _scan_devices(scan_time=5):
    """Scan for nearby Bluetooth devices using bluetoothctl.

    Returns a list of dicts: [{"name": "...", "mac": "XX:XX:XX:XX:XX:XX"}, ...]
    """
    # Start scan
    _btctl_cmd("power", "on")
    time.sleep(0.3)
    _btctl_cmd("scan", "on")
    time.sleep(scan_time)

    # Get devices
    output = _btctl_cmd("devices")
    _btctl_cmd("scan", "off")

    devices = []
    seen = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Device "):
            parts = line.split(None, 2)
            if len(parts) >= 3:
                mac = parts[1]
                name = parts[2] if len(parts) > 2 else "Unknown"
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

    Returns (success: bool, message: str).
    """
    _btctl_cmd("power", "on")
    time.sleep(0.2)

    # Remove any old pairing first
    _btctl_cmd("remove", mac)
    time.sleep(0.3)

    # Pair
    logger.info(f"[BT] Pairing with {mac}...")
    pair_out = _btctl_cmd("pair", mac, timeout=15)
    if "Failed" in pair_out and "successful" not in pair_out.lower():
        return False, f"Pairing failed: {pair_out.strip()}"

    time.sleep(0.3)

    # Trust
    _btctl_cmd("trust", mac)
    time.sleep(0.2)

    # Connect
    logger.info(f"[BT] Connecting to {mac}...")
    conn_out = _btctl_cmd("connect", mac, timeout=10)
    if "Failed" in conn_out:
        return False, f"Connection failed: {conn_out.strip()}"

    return True, f"Connected to {mac}"


def _disconnect_device(mac):
    """Disconnect and remove a Bluetooth device."""
    _btctl_cmd("disconnect", mac)
    time.sleep(0.2)
    _btctl_cmd("remove", mac)
    return True


def _get_connected_devices():
    """Get list of currently connected Bluetooth devices."""
    output = _btctl_cmd("info")
    # Also check via "devices Connected" if available
    output2 = _btctl_cmd("devices", "Connected")
    return output2


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
            devices = _scan_devices(scan_time=5)
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

        # Run pairing in background thread to avoid blocking
        result = {"ok": False, "message": ""}
        done = threading.Event()

        def _do_connect():
            success, msg = _pair_and_connect(mac)
            result["ok"] = success
            result["message"] = msg
            if success:
                # Save to config for auto-connect
                cfg = _load_bt_config()
                cfg["last_gamepad_mac"] = mac
                cfg["last_gamepad_name"] = data.get("name", "")
                _save_bt_config(cfg)
                # Trigger DS4 reconnect after a short delay
                if state.ds4:
                    threading.Timer(3.0, lambda: None).start()  # let BT settle
            done.set()

        t = threading.Thread(target=_do_connect, daemon=True)
        t.start()
        done.wait(timeout=20)

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
        done.wait(timeout=20)

        return jsonify(result)

    return bp
