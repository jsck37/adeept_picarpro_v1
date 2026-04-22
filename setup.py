#!/usr/bin/env python3
# coding=utf-8
# File name   : setup.py
# Based on    : Adeept PiCarPro v1 + v2 backports
# Description : Automated setup with swap, I2C tuning, WiFi hotspot, systemd, and safety checks

import os
import sys
import time
import subprocess
import shutil
import platform

# ─────────────────────────────────────────────────────
# GLOBAL VARIABLES
# ─────────────────────────────────────────────────────
username = os.popen("echo ${SUDO_USER:-$(who -m | awk '{ print $1 }')}").readline().strip()
user_home = os.popen(f'getent passwd {username} | cut -d: -f 6').readline().strip()
curpath = os.path.realpath(__file__)
thisPath = "/" + os.path.dirname(curpath)
LOG_FILE = "/tmp/picarpro_setup.log"

# ─────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────
def run_cmd(cmd, critical=True):
    """Execute shell command with logging and error handling."""
    print(f"  [*] {cmd}")
    result = subprocess.run(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    # Log full output for debugging
    with open(LOG_FILE, "a") as log:
        log.write(f"\n=== CMD: {cmd} ===\n")
        log.write(result.stdout)

    if result.returncode != 0:
        print(f"  [!] Error (code {result.returncode}): {cmd}")
        tail = result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
        if tail.strip():
            print(f"      {tail.strip()}")
        if critical:
            print("  [x] Critical error. Setup stopped to prevent system damage.")
            print(f"  [i] Full log: {LOG_FILE}")
            sys.exit(1)
    return result.returncode, result.stdout


def get_debian_version():
    """Detect Debian/Raspberry Pi OS major version."""
    try:
        with open("/etc/debian_version", "r") as f:
            version_str = f.read().strip()
        major = int(version_str.split(".")[0])
        return major
    except Exception:
        print("  [!] Could not detect Debian version, assuming 11 (Bullseye)")
        return 11


def get_os_codename():
    """Get OS codename (bookworm, bullseye, etc.)."""
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("VERSION_CODENAME="):
                    return line.strip().split("=")[1].strip('"')
    except Exception:
        pass
    return "unknown"


def check_disk_space(required_mb=1500):
    """Check available disk space. Exit if not enough."""
    stat = shutil.disk_usage("/")
    free_mb = stat.free // (1024 * 1024)
    if free_mb < required_mb:
        print(f"  [x] Not enough disk space: {free_mb}MB free, need {required_mb}MB minimum.")
        print("  [i] Free up space or use a larger SD card.")
        sys.exit(1)
    print(f"  [+] Disk space OK: {free_mb}MB free")
    return True


def is_package_installed(package_name):
    """Check if an apt package is already installed."""
    result = subprocess.run(
        f"dpkg -s {package_name} 2>/dev/null | grep -q 'Status: install ok installed'",
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.returncode == 0


def get_boot_config_path():
    """Determine the correct boot config.txt path.
    Bookworm uses /boot/firmware/config.txt, older OS uses /boot/config.txt."""
    if os.path.exists("/boot/firmware/config.txt"):
        return "/boot/firmware/config.txt"
    return "/boot/config.txt"


def append_to_config(keyword, line, config_path=None):
    """Append a line to config.txt if the keyword is not already present."""
    if config_path is None:
        config_path = get_boot_config_path()

    try:
        with open(config_path, "r") as f:
            content = f.read()
        if keyword not in content:
            with open(config_path, "a") as f:
                f.write(f"\n{line}\n")
            print(f"  [+] Added to {config_path}: {line.strip()}")
        else:
            print(f"  [=] Already in config: {line.strip()}")
    except PermissionError:
        print(f"  [!] Permission denied writing {config_path}. Run with sudo.")
    except Exception as e:
        print(f"  [!] Could not edit {config_path}: {e}")


# ─────────────────────────────────────────────────────
# SETUP STAGES
# ─────────────────────────────────────────────────────

def stage_0_preflight():
    """Pre-flight checks: disk space, sudo, architecture."""
    print("\n" + "=" * 55)
    print("  PiCar Pro Optimized Setup for Raspberry Pi 3B+")
    print("  (with v2 backports and performance tuning)")
    print("=" * 55)

    # Check we're running as root (sudo)
    if os.geteuid() != 0:
        print("\n  [x] This script must be run with sudo!")
        print("  Usage: sudo python3 setup.py")
        sys.exit(1)

    # Initialize log file
    with open(LOG_FILE, "w") as log:
        log.write(f"PiCar Pro Setup Log — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Check disk space
    print("\n  [0/7] Pre-flight checks...")
    check_disk_space(1500)

    # Detect OS
    debian_ver = get_debian_version()
    codename = get_os_codename()
    print(f"  [+] OS: Debian {debian_ver} ({codename})")
    print(f"  [+] User: {username}, Home: {user_home}")
    print(f"  [+] Install path: {thisPath}")

    return debian_ver, codename


def stage_1_wifi(debian_ver, codename):
    """Configure WiFi — supports both NetworkManager (Bookworm+) and wpa_supplicant."""
    print("\n  [1/7] WiFi configuration...")

    use_networkmanager = debian_ver >= 12

    if use_networkmanager:
        print("  [i] Detected NetworkManager-based system (Bookworm+)")
        _, conn_result = run_cmd(
            "nmcli -t -f ACTIVE,SSID dev wifi list | grep '^yes:'", critical=False
        )
        if conn_result.strip():
            ssid = conn_result.strip().split(":")[1] if ":" in conn_result else "unknown"
            print(f"  [+] Already connected to WiFi: {ssid}")
            choice = input("  Reconfigure WiFi? (y/N): ").strip().lower()
            if choice != 'y':
                return

        ssid = input("  Enter WiFi Network Name (SSID): ").strip()
        if not ssid:
            print("  [!] SSID empty. Skipping WiFi setup.")
            return
        psk = input("  Enter WiFi Password: ").strip()
        run_cmd(f'nmcli dev wifi connect "{ssid}" password "{psk}"', critical=False)
    else:
        print("  [i] Using wpa_supplicant (Bullseye or earlier)")
        wpa_conf = "/etc/wpa_supplicant/wpa_supplicant.conf"

        if not os.path.exists(wpa_conf):
            run_cmd(f"touch {wpa_conf}")
            with open(wpa_conf, "w") as f:
                f.write("ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=US\n")

        try:
            with open(wpa_conf, "r") as f:
                content = f.read()
            if "ssid=" in content:
                print("  [!] WiFi already configured in wpa_supplicant.conf")
                choice = input("  Skip WiFi setup? (Y/n): ").strip().lower()
                if choice != 'n':
                    return
        except Exception:
            pass

        ssid = input("  Enter WiFi Network Name (SSID): ").strip()
        if not ssid:
            print("  [!] SSID empty. Skipping.")
            return
        psk = input("  Enter WiFi Password: ").strip()

        network_block = f'\nnetwork={{\n    ssid="{ssid}"\n    psk="{psk}"\n    key_mgmt=WPA-PSK\n}}\n'
        try:
            with open(wpa_conf, "a") as f:
                f.write(network_block)
            print(f"  [+] WiFi '{ssid}' added to {wpa_conf}")
        except Exception as e:
            print(f"  [!] Failed: {e}")


def stage_2_swap():
    """Configure 2GB swap file for 1GB RAM Raspberry Pi 3B+."""
    print("\n  [2/7] Configuring swap file...")

    # Check if /var/swap already exists with 2GB
    if os.path.exists("/var/swap"):
        file_size = os.path.getsize("/var/swap")
        expected = 2 * 1024 * 1024 * 1024  # 2GB
        if abs(file_size - expected) < 10 * 1024 * 1024:  # within 10MB tolerance
            print("  [+] 2GB swap file already exists. Skipping.")
            return

    print("  [*] Creating 2GB swap file (important for 1GB RAM)...")

    # Turn off existing swap first
    run_cmd("sudo swapoff /var/swap 2>/dev/null || true", critical=False)

    # Try fallocate first (much faster than dd on ext4)
    _, result = run_cmd("sudo fallocate -l 2G /var/swap 2>/dev/null", critical=False)
    if result is not None and "Operation not supported" in str(result):
        print("  [i] fallocate not supported, using dd (slower)...")
        run_cmd("sudo dd if=/dev/zero of=/var/swap bs=1M count=2048 status=progress")
    else:
        if not os.path.exists("/var/swap"):
            run_cmd("sudo dd if=/dev/zero of=/var/swap bs=1M count=2048 status=progress")

    run_cmd("sudo chmod 600 /var/swap")
    run_cmd("sudo mkswap /var/swap")
    run_cmd("sudo swapon /var/swap")

    # Make swap persistent across reboots
    try:
        with open("/etc/fstab", "r") as f:
            fstab = f.read()
        if "/var/swap" not in fstab:
            with open("/etc/fstab", "a") as f:
                f.write("\n/var/swap none swap sw 0 0\n")
            print("  [+] Swap added to /etc/fstab (persistent across reboots)")
        else:
            print("  [=] Swap already in /etc/fstab")
    except Exception as e:
        print(f"  [!] Warning: could not update /etc/fstab: {e}")

    # Set swappiness for Pi (lower = less aggressive swapping, better for SD card)
    run_cmd("sudo sysctl vm.swappiness=10", critical=False)
    try:
        with open("/etc/sysctl.d/99-picarpro.conf", "w") as f:
            f.write("vm.swappiness=10\n")
        print("  [+] Swappiness set to 10 (reduces SD card wear)")
    except Exception as e:
        print(f"  [!] Warning: could not set swappiness: {e}")


def stage_3_apt_packages():
    """Install system packages via apt — optimized with --no-install-recommends."""
    print("\n  [3/7] Installing system packages...")

    run_cmd("sudo apt-get update -qq")

    # Core packages (always needed)
    core_packages = [
        "i2c-tools",
        "python3-smbus",
        "python3-gpiozero",
        "python3-pigpio",
    ]

    # Camera and vision packages
    camera_packages = [
        "python3-picamera2",
        "python3-opencv",
        "opencv-data",
    ]

    # Audio (for voice recognition v2 feature)
    audio_packages = [
        "python3-pyaudio",
    ]

    # Network (for WiFi hotspot via NetworkManager)
    network_packages = [
        "network-manager",
    ]

    # Build dependencies (needed for some pip packages)
    build_packages = [
        "libfreetype6-dev",
        "libjpeg-dev",
        "build-essential",
    ]

    # Skip already-installed packages
    all_packages = core_packages + camera_packages + audio_packages + network_packages + build_packages
    missing = [p for p in all_packages if not is_package_installed(p)]

    if not missing:
        print("  [+] All system packages already installed. Skipping.")
        return

    print(f"  [*] Installing {len(missing)} packages ({len(all_packages) - len(missing)} already present)...")

    pkg_str = " ".join(missing)
    run_cmd(f"sudo apt-get install -y --no-install-recommends {pkg_str}")

    # Clean up apt cache to save SD card space
    run_cmd("sudo apt-get clean", critical=False)
    run_cmd("sudo apt-get -y autoremove", critical=False)


def stage_4_pip_packages(debian_ver):
    """Install Python packages via pip."""
    print("\n  [4/7] Installing Python packages...")

    pip_flag = "--break-system-packages" if debian_ver >= 12 else ""

    # Update pip safely
    print("  [*] Updating pip...")
    run_cmd("sudo wget -q https://bootstrap.pypa.io/get-pip.py -O /tmp/get-pip.py", critical=False)
    run_cmd(f"sudo python3 /tmp/get-pip.py {pip_flag}", critical=False)
    run_cmd("sudo rm -f /tmp/get-pip.py", critical=False)

    # Group packages by dependency to minimize conflicts
    pip_groups = [
        # I2C and servo/motor control
        (
            "I2C/Motor/Servo",
            f"sudo -H pip3 install {pip_flag} "
            "adafruit-circuitpython-pca9685 "
            "adafruit-circuitpython-motor "
            "adafruit-circuitpython-busdevice "
            "adafruit-circuitpython-ads7830"
        ),
        # Display and LED
        (
            "OLED/LED",
            f"sudo -H pip3 install {pip_flag} "
            "luma.oled "
            "spidev"  # SPI-based WS2812 control (replaces rpi_ws281x)
        ),
        # Web server and networking
        (
            "Web/Network",
            f"sudo -H pip3 install {pip_flag} "
            "flask flask_cors websockets==13.0"
        ),
        # Vision and video
        (
            "Vision/Video",
            f"sudo -H pip3 install {pip_flag} "
            "pyzmq imutils pybase64 pillow numpy"
        ),
        # System utilities
        (
            "Utilities",
            f"sudo -H pip3 install {pip_flag} "
            "psutil"
        ),
        # IMU (optional, only if MPU6050 is present)
        (
            "IMU (optional)",
            f"sudo -H pip3 install {pip_flag} "
            "mpu6050-raspberrypi"
        ),
    ]

    for group_name, cmd in pip_groups:
        print(f"  [*] {group_name}...")
        run_cmd(cmd, critical=False)  # Non-critical: missing optional packages shouldn't stop setup


def stage_5_hardware_config():
    """Configure hardware: I2C, SPI, camera, GPU memory, I2C baud rate."""
    print("\n  [5/7] Configuring hardware...")

    # Enable I2C
    run_cmd("sudo raspi-config nonint do_i2c 0", critical=False)

    # Enable SPI (needed for WS2812 LED strip via spidev)
    run_cmd("sudo raspi-config nonint do_spi 0", critical=False)

    # Enable camera
    run_cmd("sudo raspi-config nonint do_camera 0", critical=False)

    # Determine correct config.txt path (Bookworm vs older OS)
    config_path = get_boot_config_path()
    print(f"  [*] Tuning {config_path}...")

    # I2C enabled
    append_to_config("i2c_arm=on", "dtparam=i2c_arm=on", config_path)

    # I2C Fast Mode (400kHz) — critical for servo performance
    # Default 100kHz is too slow for 8 servos + ultrasonic sensor
    append_to_config("i2c_arm_baudrate", "dtparam=i2c_arm_baudrate=400000", config_path)

    # SPI overlay for WS2812 LED strip (via spidev instead of rpi_ws281x)
    # rpi_ws281x requires DMA and kernel module — problematic on newer kernels
    # spidev + SPI is more reliable and compatible with Pi 5
    append_to_config("spi0-0cs", "dtoverlay=spi0-0cs,cs0_pin=8,cs1_pin=7", config_path)

    # Reduce GPU memory to 128MB (from default 256MB on 1GB Pi)
    # The camera only needs 128MB, freeing RAM for CPU
    append_to_config("gpu_mem=", "gpu_mem=128", config_path)

    # Camera support (libcamera on Bookworm, legacy on Bullseye)
    # start_x=1 is NOT needed on Pi 3B+ with libcamera
    # Only add for legacy camera stack if on Bullseye
    debian_ver = get_debian_version()
    if debian_ver < 12:
        append_to_config("start_x=1", "start_x=1", config_path)

    print("  [+] Hardware configuration complete")
    print("  [i] Key changes: I2C 400kHz, SPI enabled, GPU 128MB, camera enabled")


def stage_6_wifi_hotspot(debian_ver):
    """Set up WiFi hotspot auto-manager (from v2).

    Logic: At boot, the robot tries to connect to known WiFi networks first.
    If none are available (e.g. moved to another room, router down),
    it creates its own Access Point so you can always reach the robot
    from your phone/laptop — completely independent of any external WiFi.
    """
    print("\n  [6/7] Setting up WiFi Hotspot auto-manager...")
    print("  [i] Purpose: if no known WiFi is available, the robot creates")
    print("      its own access point so you can always connect to it.")

    if debian_ver < 12:
        print("  [!] NetworkManager hotspot requires Bookworm+ (Debian 12+)")
        print("  [i] On Bullseye, set up hotspot manually with create_ap or hostapd")
        return

    # Check if NetworkManager is installed
    if not is_package_installed("network-manager"):
        print("  [*] Installing NetworkManager...")
        run_cmd("sudo apt-get install -y --no-install-recommends network-manager")

    # --- Configure hotspot SSID and password ---
    default_ssid = "Adeept_Robot"
    default_pass = "12345678"

    # Try to read existing config
    config_file = "/etc/picarpro/hotspot.conf"
    existing_ssid = default_ssid
    existing_pass = default_pass

    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                for line in f:
                    if line.startswith("HOTSPOT_SSID="):
                        existing_ssid = line.strip().split("=", 1)[1].strip('"')
                    elif line.startswith("HOTSPOT_PASS="):
                        existing_pass = line.strip().split("=", 1)[1].strip('"')
            print(f"  [+] Existing hotspot config found: SSID={existing_ssid}")
        except Exception:
            pass

    # Interactive configuration
    print(f"\n  Current hotspot SSID: {existing_ssid}")
    print(f"  Current hotspot password: {existing_pass}")
    choice = input("  Change hotspot settings? (y/N): ").strip().lower()

    if choice == 'y':
        new_ssid = input(f"  Enter hotspot SSID [{existing_ssid}]: ").strip()
        if new_ssid:
            existing_ssid = new_ssid

        new_pass = input(f"  Enter hotspot password [{existing_pass}]: ").strip()
        if new_pass:
            if len(new_pass) < 8:
                print("  [!] WPA password must be at least 8 characters. Keeping old password.")
            else:
                existing_pass = new_pass

    # Save config file for future updates
    try:
        os.makedirs("/etc/picarpro", exist_ok=True)
        with open(config_file, "w") as f:
            f.write(f'# PiCar Pro WiFi Hotspot Configuration\n')
            f.write(f'HOTSPOT_SSID="{existing_ssid}"\n')
            f.write(f'HOTSPOT_PASS="{existing_pass}"\n')
        print(f"  [+] Hotspot config saved to {config_file}")
    except Exception as e:
        print(f"  [!] Could not save config: {e}")

    # Create the WiFi hotspot manager script
    # Reads SSID/password from config file, not hardcoded
    hotspot_script = """#!/bin/bash
# WiFi Hotspot Manager for Adeept PiCar Pro
# Logic: 1) Try known WiFi networks → 2) If none available, start own AP
# Config: /etc/picarpro/hotspot.conf

HOTSPOT_CONF="/etc/picarpro/hotspot.conf"
HOTSPOT_CONN="picarpro-hotspot"

# Read config (with defaults)
HOTSPOT_SSID="Adeept_Robot"
HOTSPOT_PASS="12345678"
if [ -f "$HOTSPOT_CONF" ]; then
    source "$HOTSPOT_CONF"
fi

# Wait for NetworkManager to be ready
for i in $(seq 1 30); do
    if nmcli general status &>/dev/null; then
        break
    fi
    sleep 1
done

# Step 1: Check if already connected to WiFi
CONNECTED=$(nmcli -t -f ACTIVE,SSID dev wifi list 2>/dev/null | grep '^yes:' | head -1)
if [ -n "$CONNECTED" ]; then
    logger "PiCarPro: Already connected to WiFi"
    exit 0
fi

# Step 2: Try all known (saved) WiFi networks
echo "[PiCarPro] Not connected. Trying saved WiFi networks..."
FIRST_WIFI=$(nmcli -t -f NAME,TYPE con show 2>/dev/null | grep '802-11-wireless' | grep -v "$HOTSPOT_CONN" | head -1 | cut -d: -f1)
if [ -n "$FIRST_WIFI" ]; then
    nmcli con up id "$FIRST_WIFI" &>/dev/null
    sleep 5
fi

# Check again
CONNECTED=$(nmcli -t -f ACTIVE,SSID dev wifi list 2>/dev/null | grep '^yes:' | head -1)

# Step 3: If still not connected → start our own Access Point
# This makes the robot ALWAYS accessible, even without any external WiFi
if [ -z "$CONNECTED" ]; then
    echo "[PiCarPro] No WiFi available. Starting hotspot: $HOTSPOT_SSID"
    # Remove old hotspot connection if SSID changed
    if nmcli con show "$HOTSPOT_CONN" &>/dev/null; then
        OLD_SSID=$(nmcli -t -f 802-11-wireless.ssid con show "$HOTSPOT_CONN" 2>/dev/null | cut -d: -f2)
        if [ "$OLD_SSID" != "$HOTSPOT_SSID" ]; then
            nmcli con delete "$HOTSPOT_CONN" &>/dev/null
        fi
    fi
    # Create hotspot connection if it doesn't exist
    if ! nmcli con show "$HOTSPOT_CONN" &>/dev/null; then
        nmcli con add type wifi ifname wlan0 con-name "$HOTSPOT_CONN" autoconnect no ssid "$HOTSPOT_SSID"
        nmcli con modify "$HOTSPOT_CONN" 802-11-wireless.mode ap 802-11-wireless.band bg
        nmcli con modify "$HOTSPOT_CONN" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASS"
        nmcli con modify "$HOTSPOT_CONN" ipv4.method shared ipv4.addresses 10.42.0.1/24
    fi
    nmcli con up "$HOTSPOT_CONN"
    logger "PiCarPro: WiFi hotspot started (SSID: $HOTSPOT_SSID, IP: 10.42.0.1)"
else
    logger "PiCarPro: Connected to WiFi"
fi
"""

    script_path = "/usr/local/bin/wifi_hotspot_manager.sh"
    try:
        with open(script_path, "w") as f:
            f.write(hotspot_script)
        os.chmod(script_path, 0o755)
        print(f"  [+] Hotspot script saved to {script_path}")
        print(f"  [i] SSID: {existing_ssid}, Password: {existing_pass}")
    except Exception as e:
        print(f"  [!] Error writing hotspot script: {e}")
        return

    # Create systemd service for hotspot (runs before robot service)
    hotspot_service = """[Unit]
Description=PiCar Pro WiFi Hotspot Manager
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/wifi_hotspot_manager.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
    try:
        with open("/etc/systemd/system/picarpro-wifi.service", "w") as f:
            f.write(hotspot_service)
        run_cmd("sudo systemctl daemon-reload")
        run_cmd("sudo systemctl enable picarpro-wifi.service", critical=False)
        print("  [+] WiFi hotspot service enabled (starts before robot)")
    except Exception as e:
        print(f"  [!] Error creating hotspot service: {e}")


def stage_7_systemd_service():
    """Create systemd service for auto-starting the robot.
    Uses RELATIVE paths from script location, not hardcoded /home/pi/..."""
    print("\n  [7/7] Setting up robot auto-start service...")

    # Determine which server to start (auto-detect)
    webserver_path = os.path.join(thisPath, "Server", "WebServer.py")
    guiserver_path = os.path.join(thisPath, "Server", "GUIServer.py")
    newserver_path = os.path.join(thisPath, "Server", "server.py")

    if os.path.exists(newserver_path):
        exec_start = f"/usr/bin/python3 {newserver_path}"
        print("  [i] Using Server/server.py (optimized unified server)")
    elif os.path.exists(webserver_path):
        exec_start = f"/usr/bin/python3 {webserver_path}"
        print("  [i] Using Server/WebServer.py (web browser control)")
    elif os.path.exists(guiserver_path):
        exec_start = f"/usr/bin/python3 {guiserver_path}"
        print("  [i] Using Server/GUIServer.py (desktop GUI control)")
    else:
        print("  [!] No server file found! Service will not be created.")
        return

    service_content = f"""[Unit]
Description=Adeept PiCar Pro Robot Server
After=network-online.target picarpro-wifi.service
Wants=network-online.target picarpro-wifi.service

[Service]
Type=simple
User={username}
Group={username}
WorkingDirectory={thisPath}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3

# Security hardening
NoNewPrivileges=true
ProtectHome=false
ReadWritePaths={thisPath} /tmp /var/run

# Environment
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""

    service_path = "/etc/systemd/system/picarpro.service"
    try:
        with open(service_path, "w") as f:
            f.write(service_content)
        run_cmd("sudo systemctl daemon-reload")
        run_cmd("sudo systemctl enable picarpro.service")
        print("  [+] Robot auto-start service created and enabled!")
        print(f"  [i] Service starts {os.path.basename(exec_start)} on boot")
        print(f"  [i] WorkingDirectory: {thisPath}")
    except Exception as e:
        print(f"  [!] Error creating service: {e}")

    # Clean up old startup method if present
    old_startup = os.path.join(user_home, "startup.sh")
    if os.path.exists(old_startup):
        run_cmd(f"sudo rm -f {old_startup}", critical=False)
        print("  [+] Removed old startup.sh")


# ─────────────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────────────
def main():
    debian_ver, codename = stage_0_preflight()
    stage_1_wifi(debian_ver, codename)
    stage_2_swap()
    stage_3_apt_packages()
    stage_4_pip_packages(debian_ver)
    stage_5_hardware_config()
    stage_6_wifi_hotspot(debian_ver)
    stage_7_systemd_service()

    # ─── DETECT SYSTEM INFO ───
    try:
        with open('/proc/device-tree/model', 'r') as f:
            pi_model = f.read().strip('\x00')
    except Exception:
        pi_model = 'Unknown'

    try:
        with open('/etc/debian_version', 'r') as f:
            debian_display = f.read().strip()
    except Exception:
        debian_display = 'unknown'

    try:
        ip_result = subprocess.run(
            "hostname -I 2>/dev/null | awk '{print $1}'",
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        current_ip = ip_result.stdout.strip()
        if not current_ip:
            current_ip = "(will appear after reboot)"
    except Exception:
        current_ip = "(error detecting)"

    # ─── SUMMARY ───
    print("\n" + "=" * 55)
    print("  SETUP COMPLETE!")
    print("=" * 55)
    print("")
    print("  System Info:")
    print(f"    Model:   {pi_model}")
    print(f"    Debian:  {debian_display}")
    print(f"    Python:  {sys.version_info.major}.{sys.version_info.minor}")
    print(f"    Arch:    {platform.machine()}")
    print("")
    print(f"  IP address:     {current_ip}")
    print(f"  Install path:   {thisPath}")
    print(f"  Setup log:      {LOG_FILE}")
    print("")
    print("  To start the robot server:")
    print("    python3 Server/server.py")
    print("")
    print("  Or reboot to auto-start via systemd:")
    print("    sudo reboot")
    print("")
    print(f"  Web interface: http://{current_ip if '/' not in current_ip else '<IP>'}:5000")
    print(f"  WebSocket:     ws://{current_ip if '/' not in current_ip else '<IP>'}:8888")
    print("")
    print("  Key optimizations applied:")
    print("    - I2C Fast Mode: 400kHz (was 100kHz)")
    print("    - GPU memory: 128MB (was 256MB)")
    print("    - Swap: 2GB (swappiness=10)")
    print("    - SPI: enabled for WS2812 LEDs")
    print("    - WiFi Hotspot: auto-switching (fallback AP)")
    print("    - Systemd: auto-start with relative paths")
    print("")
    print("  Service management:")
    print("    sudo systemctl start picarpro     — start")
    print("    sudo systemctl stop picarpro      — stop")
    print("    sudo systemctl status picarpro    — status")
    print("    journalctl -u picarpro -f         — logs")
    print("=" * 55)

    # Interactive reboot
    while True:
        choice = input("\n  Reboot now? (y/N): ").strip().lower()
        if choice in ['y', 'yes']:
            print("\n  Rebooting in 3 seconds...")
            time.sleep(3)
            os.system("sudo reboot")
            break
        elif choice in ['n', 'no', '']:
            print("\n  Reboot cancelled. Run manually: sudo reboot")
            break
        else:
            print("  [!] Enter 'y' (yes) or 'n' (no).")


if __name__ == "__main__":
    main()
