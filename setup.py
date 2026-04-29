#!/usr/bin/env python3
# coding=utf-8
# File name   : setup.py
# Description : Automated setup for PiCar Pro (Flask + WebSocket)
# Based on    : jsck37/adeept_picarpro_v1 style + performance tuning for Pi 3B+
# Fixes       : apt update+upgrade, pip --ignore-installed, path bugs, swap logic, hotspot

import os
import sys
import time
import subprocess
import shutil
import platform
import re

# ─────────────────────────────────────────────────────
# GLOBAL VARIABLES
# ─────────────────────────────────────────────────────
username = os.popen("echo ${SUDO_USER:-$(who -m | awk '{ print $1 }')}").readline().strip()
if not username:
    username = "pi"
user_home = os.popen(f'getent passwd {username} 2>/dev/null | cut -d: -f 6').readline().strip()
if not user_home:
    user_home = f"/home/{username}"
curpath = os.path.realpath(__file__)
# FIX: was "/" + dirname → produced double-slash "//home/..."
thisPath = os.path.dirname(curpath)
LOG_FILE = "/tmp/picarpro_setup.log"

# ─────────────────────────────────────────────────────
# ANSI COLORS
# ─────────────────────────────────────────────────────
RST  = "\033[0m"
BOLD = "\033[1m"
DIM  = "\033[2m"
RED  = "\033[91m"
GRN  = "\033[92m"
YLW  = "\033[93m"
BLU  = "\033[94m"
CYN  = "\033[96m"
WHT  = "\033[97m"

# ─────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────
def run_cmd(cmd, critical=True):
    """Execute shell command with logging and error handling."""
    print(f"  {CYN}[*]{RST} {cmd}")
    result = subprocess.run(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=300  # 5 min timeout to prevent hangs
    )
    with open(LOG_FILE, "a") as log:
        log.write(f"\n=== CMD: {cmd} ===\n")
        log.write(result.stdout or "")

    if result.returncode != 0:
        print(f"  {RED}[!]{RST} Error (code {result.returncode}): {cmd}")
        tail = result.stdout[-500:] if result.stdout and len(result.stdout) > 500 else (result.stdout or "")
        if tail.strip():
            print(f"      {tail.strip()}")
        if critical:
            print(f"  {RED}[x]{RST} Critical error. Setup stopped to prevent system damage.")
            print(f"  {BLU}[i]{RST} Full log: {LOG_FILE}")
            sys.exit(1)
    return result.returncode, result.stdout or ""


def run_cmd_quiet(cmd):
    """Execute shell command, return (returncode, stdout). No printing."""
    result = subprocess.run(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=60
    )
    return result.returncode, result.stdout or ""


def get_debian_version():
    """Detect Debian/Raspberry Pi OS major version."""
    try:
        with open("/etc/debian_version", "r") as f:
            version_str = f.read().strip()
        # Handle formats like "13.0" or "bookworm/sid"
        major_str = version_str.split(".")[0]
        if major_str.isdigit():
            return int(major_str)
        # Try codename mapping
        codename_map = {"bullseye": 11, "bookworm": 12, "trixie": 13, "forky": 14}
        for name, ver in codename_map.items():
            if name in version_str.lower():
                return ver
    except Exception:
        pass
    print(f"  {YLW}[!]{RST} Could not detect Debian version, assuming 12 (Bookworm)")
    return 12


def get_os_codename():
    """Get OS codename (bookworm, bullseye, trixie, etc.)."""
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
        print(f"  {RED}[x]{RST} Not enough disk space: {free_mb}MB free, need {required_mb}MB minimum.")
        sys.exit(1)
    print(f"  {GRN}[+]{RST} Disk space OK: {free_mb}MB free")
    return True


def is_package_installed(package_name):
    """Check if an apt package is already installed."""
    result = subprocess.run(
        f"dpkg -s {package_name} 2>/dev/null | grep -q 'Status: install ok installed'",
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.returncode == 0


def get_boot_config_path():
    """Determine the correct boot config.txt path."""
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
            print(f"  {GRN}[+]{RST} Added to {config_path}: {line.strip()}")
        else:
            print(f"  {DIM}[=]{RST} Already in config: {line.strip()}")
    except PermissionError:
        print(f"  {RED}[!]{RST} Permission denied writing {config_path}. Run with sudo.")
    except Exception as e:
        print(f"  {RED}[!]{RST} Could not edit {config_path}: {e}")



# ─────────────────────────────────────────────────────
# SETUP STAGES
# ─────────────────────────────────────────────────────

def stage_0_preflight():
    """Pre-flight checks: disk space, sudo, architecture."""
    print(f"\n{BOLD}{CYN}{'=' * 55}{RST}")
    print(f"  {BOLD}PiCar Pro Optimized Setup{RST}")
    print(f"  {DIM}Flask + WebSocket | Raspberry Pi 3B+{RST}")
    print(f"{BOLD}{CYN}{'=' * 55}{RST}")

    if os.geteuid() != 0:
        print(f"\n  {RED}[x]{RST} This script must be run with {BOLD}sudo{RST}!")
        print(f"  Usage: {BOLD}sudo python3 setup.py{RST}")
        sys.exit(1)

    with open(LOG_FILE, "w") as log:
        log.write(f"PiCar Pro Setup Log — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    print(f"\n  {BLU}[0/7]{RST} Pre-flight checks...")
    check_disk_space(1500)

    debian_ver = get_debian_version()
    codename = get_os_codename()
    print(f"  {GRN}[+]{RST} OS: Debian {debian_ver} ({codename})")
    print(f"  {GRN}[+]{RST} User: {username}, Home: {user_home}")
    print(f"  {GRN}[+]{RST} Install path: {thisPath}")

    # Validate install path
    server_check = os.path.join(thisPath, "Server", "WebServer.py")
    if not os.path.exists(server_check):
        print(f"  {YLW}[!]{RST} Server/WebServer.py not found at {server_check}")
        print(f"  {YLW}[!]{RST} Make sure setup.py is inside the picarpro/ directory")

    return debian_ver, codename


def stage_1_wifi(debian_ver, codename):
    """Configure WiFi."""
    print(f"\n  {BLU}[1/7]{RST} WiFi configuration...")

    use_networkmanager = debian_ver >= 12

    if use_networkmanager:
        print(f"  {DIM}[i]{RST} Detected NetworkManager-based system (Bookworm+)")

        # Check current connection
        _, conn_result = run_cmd(
            "nmcli -t -f ACTIVE,SSID dev wifi list | grep '^yes:'", critical=False
        )
        if conn_result.strip():
            ssid = conn_result.strip().split(":")[1] if ":" in conn_result else "unknown"
            print(f"  {GRN}[+]{RST} Already connected to WiFi: {ssid}")
            choice = input(f"  {YLW}?{RST} Reconfigure WiFi? (y/N): ").strip().lower()
            if choice != 'y':
                return

        ssid = input(f"  {YLW}?{RST} Enter WiFi Network Name (SSID): ").strip()
        if not ssid:
            print(f"  {YLW}[!]{RST} SSID empty. Skipping WiFi setup.")
            return
        psk = input(f"  {YLW}?{RST} Enter WiFi Password: ").strip()

        # FIX: Retry WiFi connection up to 3 times
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            rc, output = run_cmd(
                f'nmcli dev wifi connect "{ssid}" password "{psk}"', critical=False
            )
            if rc == 0:
                print(f"  {GRN}[+]{RST} Connected to WiFi: {ssid}")
                break
            else:
                print(f"  {YLW}[!]{RST} Connection attempt {attempt}/{max_retries} failed")
                if attempt < max_retries:
                    print(f"  {DIM}[i]{RST} Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    print(f"  {RED}[!]{RST} Could not connect to WiFi after {max_retries} attempts")
                    print(f"  {DIM}[i]{RST} You can configure WiFi later:")
                    print(f"      nmcli dev wifi connect \"SSID\" password \"PASSWORD\"")
    else:
        print(f"  {DIM}[i]{RST} Using wpa_supplicant (Bullseye or earlier)")
        wpa_conf = "/etc/wpa_supplicant/wpa_supplicant.conf"

        if not os.path.exists(wpa_conf):
            try:
                with open(wpa_conf, "w") as f:
                    f.write("ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
                            "update_config=1\ncountry=US\n")
            except PermissionError:
                run_cmd(f"touch {wpa_conf}", critical=False)
                with open(wpa_conf, "w") as f:
                    f.write("ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
                            "update_config=1\ncountry=US\n")

        ssid = input(f"  {YLW}?{RST} Enter WiFi Network Name (SSID): ").strip()
        if not ssid:
            print(f"  {YLW}[!]{RST} SSID empty. Skipping.")
            return
        psk = input(f"  {YLW}?{RST} Enter WiFi Password: ").strip()

        network_block = f'\nnetwork={{\n    ssid="{ssid}"\n    psk="{psk}"\n    key_mgmt=WPA-PSK\n}}\n'
        try:
            with open(wpa_conf, "a") as f:
                f.write(network_block)
            print(f"  {GRN}[+]{RST} WiFi '{ssid}' added to {wpa_conf}")
        except Exception as e:
            print(f"  {RED}[!]{RST} Failed: {e}")


def stage_2_swap():
    """Configure 2GB swap file for 1GB RAM Raspberry Pi 3B+."""
    print(f"\n  {BLU}[2/7]{RST} Configuring swap file...")

    # Check current swap
    _, sw_result = run_cmd("free -m | awk '/Swap/{print $2}'", critical=False)
    current_swap = int(sw_result.strip()) if sw_result.strip().isdigit() else 0

    if current_swap >= 1900:
        print(f"  {GRN}[+]{RST} Swap already configured ({current_swap}MB). Skipping.")
        return

    print(f"  {DIM}[i]{RST} Creating 2GB swap file (critical for 1GB RAM)...")

    # First, disable any existing swap at /var/swap
    run_cmd("swapoff /var/swap 2>/dev/null || true", critical=False)

    # Remove old file if exists
    if os.path.exists("/var/swap"):
        try:
            os.remove("/var/swap")
        except Exception:
            run_cmd("rm -f /var/swap", critical=False)

    # FIX: Try fallocate first, verify file size, fall back to dd
    fallocate_ok = False
    rc, _ = run_cmd("fallocate -l 2G /var/swap 2>/dev/null", critical=False)
    if rc == 0 and os.path.exists("/var/swap"):
        # Verify file size
        try:
            file_size = os.path.getsize("/var/swap")
            if file_size >= 2 * 1024 * 1024 * 1024 - 1024 * 1024:  # within 1MB of 2GB
                fallocate_ok = True
                print(f"  {GRN}[+]{RST} Swap file created via fallocate ({file_size // (1024*1024)}MB)")
            else:
                print(f"  {YLW}[!]{RST} fallocate created undersized file ({file_size // (1024*1024)}MB), using dd instead")
                os.remove("/var/swap")
        except Exception:
            fallocate_ok = False

    if not fallocate_ok:
        print(f"  {DIM}[i]{RST} Using dd (slower but reliable)...")
        run_cmd("dd if=/dev/zero of=/var/swap bs=1M count=2048 status=progress")

    run_cmd("chmod 600 /var/swap")
    run_cmd("mkswap /var/swap")
    run_cmd("swapon /var/swap")

    # Make swap persistent in /etc/fstab
    try:
        with open("/etc/fstab", "r") as f:
            fstab = f.read()
        if "/var/swap" not in fstab:
            with open("/etc/fstab", "a") as f:
                f.write("\n/var/swap none swap sw 0 0\n")
            print(f"  {GRN}[+]{RST} Swap added to /etc/fstab (persistent)")
        else:
            print(f"  {DIM}[=]{RST} Swap already in /etc/fstab")
    except Exception as e:
        print(f"  {YLW}[!]{RST} Could not update /etc/fstab: {e}")

    # Set swappiness — low value reduces SD card wear
    run_cmd("sysctl vm.swappiness=10", critical=False)
    try:
        os.makedirs("/etc/sysctl.d", exist_ok=True)
        with open("/etc/sysctl.d/99-picarpro.conf", "w") as f:
            f.write("vm.swappiness=10\n")
        print(f"  {GRN}[+]{RST} Swappiness set to 10 (reduces SD card wear)")
    except Exception as e:
        print(f"  {YLW}[!]{RST} Could not set swappiness: {e}")


def stage_3_apt_packages(debian_ver, codename):
    """Install system packages via apt."""
    print(f"\n  {BLU}[3/7]{RST} Installing system packages...")

    # FIX: Use 'apt update -y && apt upgrade -y' instead of broken apt-get
    # The old fix_apt_sources() was broken — pipe return code from 'head'
    # masked the real apt error, so it never actually fixed anything.
    # Trixie repos work fine (InRelease), the real issue was network.
    update_ok = False
    for attempt in range(1, 4):
        rc, output = run_cmd("apt update -y", critical=False)
        if rc == 0:
            update_ok = True
            break
        print(f"  {YLW}[!]{RST} apt update attempt {attempt}/3 failed")
        if attempt < 3:
            print(f"  {DIM}[i]{RST} Retrying in 10 seconds...")
            time.sleep(10)

    if not update_ok:
        print(f"  {RED}[!]{RST} apt update failed after 3 attempts")
        print(f"  {YLW}[!]{RST} Continuing anyway — some packages may fail to install")
        print(f"  {DIM}[i]{RST} Check your internet connection")
    else:
        # Upgrade installed packages (security + bug fixes)
        print(f"  {DIM}[*]{RST} Upgrading installed packages...")
        run_cmd("apt upgrade -y", critical=False)

    # FIX: Removed python3-pyaudio (no audio input hardware, requires PortAudio)
    # FIX: Removed python3-pigpio (not used, gpiozero handles everything)
    # FIX: Packages adjusted for Trixie compatibility
    all_packages = [
        "i2c-tools", "python3-smbus", "python3-gpiozero",
    ]

    # Camera packages — different names on different Debian versions
    if debian_ver >= 13:
        # Trixie: picamera2 may be in pip instead
        camera_packages = ["python3-opencv", "opencv-data"]
    elif debian_ver >= 12:
        camera_packages = ["python3-picamera2", "python3-opencv", "opencv-data"]
    else:
        # Bullseye and earlier
        camera_packages = ["python3-picamera2", "python3-opencv", "opencv-data"]

    all_packages.extend(camera_packages)

    # Build tools (needed for pip compilation)
    all_packages.extend([
        "libfreetype6-dev", "libjpeg-dev", "build-essential",
        "network-manager",
    ])

    # Filter out already installed packages
    missing = [p for p in all_packages if not is_package_installed(p)]

    if not missing:
        print(f"  {GRN}[+]{RST} All system packages already installed. Skipping.")
        return

    print(f"  {DIM}[*]{RST} Installing {len(missing)} packages "
          f"({len(all_packages) - len(missing)} already present)...")
    pkg_str = " ".join(missing)
    run_cmd(f"apt-get install -y --no-install-recommends {pkg_str}", critical=False)
    run_cmd("apt-get clean", critical=False)
    run_cmd("apt-get -y autoremove", critical=False)


def stage_4_pip_packages(debian_ver):
    """Install Python packages via pip."""
    print(f"\n  {BLU}[4/7]{RST} Installing Python packages...")

    pip_flag = "--break-system-packages" if debian_ver >= 12 else ""

    # Update pip safely
    # FIX: Use --ignore-installed to avoid "Cannot uninstall pip" error
    # on systems where pip was installed by debian (no RECORD file)
    print(f"  {DIM}[*]{RST} Updating pip...")
    run_cmd(f"sudo -H pip3 install {pip_flag} --ignore-installed pip", critical=False)

    # FIX: Removed adafruit-circuitpython-ads7830 (no battery ADC hardware)
    # FIX: MPU6050 now installed by default (user has the hardware)
    pip_groups = [
        ("I2C/Motor/Servo",
         f"sudo -H pip3 install {pip_flag} "
         "adafruit-circuitpython-pca9685 "
         "adafruit-circuitpython-motor "
         "adafruit-circuitpython-busdevice"),
        ("OLED/LED",
         f"sudo -H pip3 install {pip_flag} luma.oled spidev"),
        ("Web Server",
         f"sudo -H pip3 install {pip_flag} flask flask_cors websockets"),
        ("Vision/Video",
         f"sudo -H pip3 install {pip_flag} numpy psutil imutils pybase64 pillow pyzmq"),
        ("IMU Sensor",
         f"sudo -H pip3 install {pip_flag} mpu6050-raspberrypi"),
    ]

    for group_name, cmd in pip_groups:
        print(f"  {DIM}[*]{RST} {group_name}...")
        run_cmd(cmd, critical=False)


def stage_5_hardware_config():
    """Configure hardware: I2C, SPI, camera, GPU memory."""
    print(f"\n  {BLU}[5/7]{RST} Configuring hardware...")

    # Enable I2C, SPI, camera via raspi-config
    # FIX: These may fail on newer OS, so all are non-critical
    run_cmd("raspi-config nonint do_i2c 0", critical=False)
    run_cmd("raspi-config nonint do_spi 0", critical=False)
    run_cmd("raspi-config nonint do_camera 0", critical=False)

    config_path = get_boot_config_path()
    print(f"  {DIM}[*]{RST} Tuning {config_path}...")

    append_to_config("i2c_arm=on", "dtparam=i2c_arm=on", config_path)
    append_to_config("i2c_arm_baudrate", "dtparam=i2c_arm_baudrate=400000", config_path)
    append_to_config("spi0-0cs", "dtoverlay=spi0-0cs,cs0_pin=8,cs1_pin=7", config_path)
    append_to_config("gpu_mem=", "gpu_mem=128", config_path)

    debian_ver = get_debian_version()
    if debian_ver < 12:
        append_to_config("start_x=1", "start_x=1", config_path)

    print(f"  {GRN}[+]{RST} Hardware configuration complete")


def stage_6_wifi_hotspot(debian_ver):
    """Set up WiFi hotspot auto-manager."""
    print(f"\n  {BLU}[6/7]{RST} Setting up WiFi Hotspot auto-manager...")

    if debian_ver < 12:
        print(f"  {YLW}[!]{RST} NetworkManager hotspot requires Bookworm+ (Debian 12+)")
        print(f"  {DIM}[i]{RST} On Bullseye, set up hotspot manually with create_ap or hostapd")
        return

    if not is_package_installed("network-manager"):
        print(f"  {DIM}[*]{RST} Installing NetworkManager...")
        run_cmd("apt-get install -y --no-install-recommends network-manager", critical=False)

    # Configure hotspot
    default_ssid = "Adeept_Robot"
    default_pass = "12345678"
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
            print(f"  {GRN}[+]{RST} Existing hotspot config: SSID={existing_ssid}")
        except Exception:
            pass

    print(f"\n  Current hotspot SSID: {BOLD}{existing_ssid}{RST}")
    print(f"  Current hotspot password: {BOLD}{existing_pass}{RST}")
    choice = input(f"  {YLW}?{RST} Change hotspot settings? (y/N): ").strip().lower()

    if choice == 'y':
        new_ssid = input(f"  {YLW}?{RST} Enter hotspot SSID [{existing_ssid}]: ").strip()
        if new_ssid:
            existing_ssid = new_ssid
        new_pass = input(f"  {YLW}?{RST} Enter hotspot password [{existing_pass}]: ").strip()
        if new_pass:
            if len(new_pass) < 8:
                print(f"  {RED}[!]{RST} WPA password must be at least 8 characters.")
            else:
                existing_pass = new_pass

    # Save config
    try:
        os.makedirs("/etc/picarpro", exist_ok=True)
        with open(config_file, "w") as f:
            f.write(f'# PiCar Pro WiFi Hotspot Configuration\n')
            f.write(f'HOTSPOT_SSID="{existing_ssid}"\n')
            f.write(f'HOTSPOT_PASS="{existing_pass}"\n')
        print(f"  {GRN}[+]{RST} Hotspot config saved to {config_file}")
    except Exception as e:
        print(f"  {RED}[!]{RST} Could not save config: {e}")

    # Create hotspot manager script
    # FIX: Corrected the nmcli connection check logic
    hotspot_script = f"""#!/bin/bash
# WiFi Hotspot Manager for PiCar Pro
HOTSPOT_CONF="/etc/picarpro/hotspot.conf"
HOTSPOT_CONN="picarpro-hotspot"
HOTSPOT_SSID="{existing_ssid}"
HOTSPOT_PASS="{existing_pass}"
[ -f "$HOTSPOT_CONF" ] && source "$HOTSPOT_CONF"

# Wait for NetworkManager
for i in $(seq 1 30); do nmcli general status &>/dev/null && break; sleep 1; done

# Check if already connected to WiFi
CONNECTED=$(nmcli -t -f ACTIVE,SSID dev wifi list 2>/dev/null | grep '^yes:' | head -1)
[ -n "$CONNECTED" ] && exit 0

# Try saved networks first
FIRST_WIFI=$(nmcli -t -f NAME,TYPE con show 2>/dev/null | grep '802-11-wireless' | grep -v "$HOTSPOT_CONN" | head -1 | cut -d: -f1)
if [ -n "$FIRST_WIFI" ]; then
    nmcli con up id "$FIRST_WIFI" &>/dev/null
    sleep 5
fi

# Check again, start hotspot if still not connected
CONNECTED=$(nmcli -t -f ACTIVE,SSID dev wifi list 2>/dev/null | grep '^yes:' | head -1)
if [ -z "$CONNECTED" ]; then
    echo "[PiCarPro] Starting hotspot: $HOTSPOT_SSID"
    # FIX: Use nmcli exit code to check if connection exists, not string comparison
    if nmcli con show "$HOTSPOT_CONN" &>/dev/null; then
        nmcli con delete "$HOTSPOT_CONN" &>/dev/null
    fi
    nmcli con add type wifi ifname wlan0 con-name "$HOTSPOT_CONN" autoconnect no ssid "$HOTSPOT_SSID"
    nmcli con modify "$HOTSPOT_CONN" 802-11-wireless.mode ap 802-11-wireless.band bg
    nmcli con modify "$HOTSPOT_CONN" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASS"
    nmcli con modify "$HOTSPOT_CONN" ipv4.method shared ipv4.addresses 10.42.0.1/24
    nmcli con up "$HOTSPOT_CONN"
    logger "PiCarPro: WiFi hotspot started (SSID: $HOTSPOT_SSID, IP: 10.42.0.1)"
fi
"""

    script_path = "/usr/local/bin/wifi_hotspot_manager.sh"
    try:
        with open(script_path, "w") as f:
            f.write(hotspot_script)
        os.chmod(script_path, 0o755)
        print(f"  {GRN}[+]{RST} Hotspot script saved to {script_path}")
    except Exception as e:
        print(f"  {RED}[!]{RST} Error writing hotspot script: {e}")
        return

    # Create systemd service
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
        run_cmd("systemctl daemon-reload")
        run_cmd("systemctl enable picarpro-wifi.service", critical=False)
        print(f"  {GRN}[+]{RST} WiFi hotspot service enabled")
    except Exception as e:
        print(f"  {RED}[!]{RST} Error creating hotspot service: {e}")


def stage_7_systemd_service():
    """Create systemd service for auto-starting the robot."""
    print(f"\n  {BLU}[7/7]{RST} Setting up robot auto-start service...")

    server_path = os.path.join(thisPath, "Server", "WebServer.py")

    if not os.path.exists(server_path):
        print(f"  {RED}[!]{RST} Server file not found: {server_path}")
        print(f"  {YLW}[!]{RST} Skipping service creation. Fix the path and re-run setup.")
        return

    # Remove old service if exists
    if os.path.exists("/etc/systemd/system/picarpro.service"):
        run_cmd("systemctl stop picarpro 2>/dev/null", critical=False)
        run_cmd("systemctl disable picarpro 2>/dev/null", critical=False)
        os.remove("/etc/systemd/system/picarpro.service")
        run_cmd("systemctl daemon-reload", critical=False)

    # FIX: Use absolute python3 path and add environment for display
    service_content = f"""[Unit]
Description=PiCar Pro Robot Server (Flask + WebSocket)
After=network-online.target picarpro-wifi.service
Wants=network-online.target picarpro-wifi.service

[Service]
Type=simple
User={username}
Group={username}
WorkingDirectory={thisPath}
ExecStart=/usr/bin/python3 {server_path}
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""

    try:
        with open("/etc/systemd/system/picarpro.service", "w") as f:
            f.write(service_content)
        run_cmd("systemctl daemon-reload")
        run_cmd("systemctl enable picarpro.service")
        print(f"  {GRN}[+]{RST} Robot auto-start service created and enabled!")
    except Exception as e:
        print(f"  {RED}[!]{RST} Error creating service: {e}")


# ─────────────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────────────
def main():
    debian_ver, codename = stage_0_preflight()
    stage_1_wifi(debian_ver, codename)
    stage_2_swap()
    stage_3_apt_packages(debian_ver, codename)
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
    print(f"\n{BOLD}{GRN}{'=' * 55}{RST}")
    print(f"  {BOLD}{GRN}SETUP COMPLETE!{RST}")
    print(f"{BOLD}{GRN}{'=' * 55}{RST}")
    print(f"")
    print(f"  {BOLD}System Info:{RST}")
    print(f"    Model:   {pi_model}")
    print(f"    Debian:  {debian_display}")
    print(f"    Python:  {sys.version_info.major}.{sys.version_info.minor}")
    print(f"    Arch:    {platform.machine()}")
    print(f"")
    print(f"  {BOLD}Network:{RST}")
    print(f"    {CYN}IP address:{RST}     {BOLD}{current_ip}{RST}")
    print(f"    Install path:   {thisPath}")
    print(f"    Setup log:      {LOG_FILE}")
    print(f"")
    print(f"  {BOLD}To start the robot server:{RST}")
    print(f"    {GRN}python3 Server/WebServer.py{RST}")
    print(f"")
    print(f"  Or reboot to auto-start via systemd:")
    print(f"    {GRN}sudo reboot{RST}")
    print(f"")
    ip_safe = current_ip if '/' not in current_ip else '<IP>'
    print(f"  {BOLD}{CYN}Web interface:{RST}  {BOLD}http://{ip_safe}:5000{RST}")
    print(f"  {BOLD}{CYN}Modules page:{RST}   {BOLD}http://{ip_safe}:5000/#modules{RST}")
    print(f"")
    print(f"  {BOLD}Key optimizations:{RST}")
    print(f"    {GRN}+{RST} I2C Fast Mode: 400kHz")
    print(f"    {GRN}+{RST} GPU memory: 128MB (was 256MB)")
    print(f"    {GRN}+{RST} Swap: 2GB (swappiness=10)")
    print(f"    {GRN}+{RST} Flask (5000) + WebSocket (8888) real-time bidirectional")
    print(f"    {GRN}+{RST} BGR888 camera (fixed red/blue color swap)")
    print(f"    {GRN}+{RST} WiFi Hotspot: auto-switching (fallback AP)")
    print(f"")
    print(f"  {BOLD}Service management:{RST}")
    print(f"    sudo systemctl start picarpro     — start")
    print(f"    sudo systemctl stop picarpro      — stop")
    print(f"    sudo systemctl status picarpro    — status")
    print(f"    journalctl -u picarpro -f         — logs")
    print(f"{BOLD}{GRN}{'=' * 55}{RST}")

    while True:
        choice = input(f"\n  {YLW}?{RST} Reboot now? (y/N): ").strip().lower()
        if choice in ['y', 'yes']:
            print(f"\n  {GRN}Rebooting in 3 seconds...{RST}")
            time.sleep(3)
            os.system("reboot")
            break
        elif choice in ['n', 'no', '']:
            print(f"\n  {DIM}Reboot cancelled. Run manually: sudo reboot{RST}")
            break
        else:
            print(f"  {YLW}[!]{RST} Enter 'y' (yes) or 'n' (no).")


if __name__ == "__main__":
    main()
