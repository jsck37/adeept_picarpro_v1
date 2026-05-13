#!/usr/bin/env python3
"""Automated setup for PiCar Pro (Flask + WebSocket) on Raspberry Pi 3B+."""

import os
import sys
import time
import subprocess
import shutil
import platform

username = os.environ.get('SUDO_USER', '').strip()
if not username:
    username = os.popen('whoami').readline().strip()
if not username:
    username = "pi"
user_home = os.popen(f'getent passwd {username} 2>/dev/null | cut -d: -f 6').readline().strip()
if not user_home:
    user_home = f"/home/{username}"
curpath = os.path.realpath(__file__)
thisPath = os.path.dirname(curpath)
LOG_FILE = "/tmp/picarpro_setup.log"

RST  = "\033[0m"
BOLD = "\033[1m"
DIM  = "\033[2m"
RED  = "\033[91m"
GRN  = "\033[92m"
YLW  = "\033[93m"
BLU  = "\033[94m"
CYN  = "\033[96m"


def run_cmd(cmd, critical=True):
    print(f"  {CYN}[*]{RST} {cmd}")
    result = subprocess.run(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=300
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
            print(f"  {RED}[x]{RST} Critical error. Log: {LOG_FILE}")
            sys.exit(1)
    return result.returncode, result.stdout or ""


def get_debian_version():
    try:
        with open("/etc/debian_version", "r") as f:
            version_str = f.read().strip()
        major_str = version_str.split(".")[0]
        if major_str.isdigit():
            return int(major_str)
        codename_map = {"bullseye": 11, "bookworm": 12, "trixie": 13, "forky": 14}
        for name, ver in codename_map.items():
            if name in version_str.lower():
                return ver
    except Exception:
        pass
    print(f"  {YLW}[!]{RST} Cannot detect Debian version, assuming 12")
    return 12


def get_os_codename():
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("VERSION_CODENAME="):
                    return line.strip().split("=")[1].strip('"')
    except Exception:
        pass
    return "unknown"


def check_disk_space(required_mb=1500):
    stat = shutil.disk_usage("/")
    free_mb = stat.free // (1024 * 1024)
    if free_mb < required_mb:
        print(f"  {RED}[x]{RST} Not enough disk space: {free_mb}MB free, need {required_mb}MB")
        sys.exit(1)
    print(f"  {GRN}[+]{RST} Disk space OK: {free_mb}MB free")


def is_package_installed(package_name):
    result = subprocess.run(
        f"dpkg -s {package_name} 2>/dev/null | grep -q 'Status: install ok installed'",
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.returncode == 0


def get_boot_config_path():
    if os.path.exists("/boot/firmware/config.txt"):
        return "/boot/firmware/config.txt"
    return "/boot/config.txt"


def append_to_config(keyword, line, config_path=None):
    if config_path is None:
        config_path = get_boot_config_path()
    try:
        with open(config_path, "r") as f:
            content = f.read()
        if keyword not in content:
            with open(config_path, "a") as f:
                f.write(f"\n{line}\n")
            print(f"  {GRN}[+]{RST} Added: {line.strip()}")
        else:
            print(f"  {DIM}[=]{RST} Already present: {line.strip()}")
    except Exception as e:
        print(f"  {RED}[!]{RST} Could not edit {config_path}: {e}")


def stage_0_preflight():
    print(f"\n{BOLD}{CYN}{'=' * 55}{RST}")
    print(f"  {BOLD}PiCar Pro Setup{RST}")
    print(f"  {DIM}Flask + WebSocket | Raspberry Pi 3B+{RST}")
    print(f"{BOLD}{CYN}{'=' * 55}{RST}")

    if os.geteuid() != 0:
        print(f"\n  {RED}[x]{RST} Run with {BOLD}sudo{RST}!")
        sys.exit(1)

    with open(LOG_FILE, "w") as log:
        log.write(f"PiCar Pro Setup Log — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    print(f"\n  {BLU}[0/7]{RST} Pre-flight checks...")
    check_disk_space(1500)

    debian_ver = get_debian_version()
    codename = get_os_codename()
    print(f"  {GRN}[+]{RST} OS: Debian {debian_ver} ({codename})")
    print(f"  {GRN}[+]{RST} User: {username}, Path: {thisPath}")

    server_check = os.path.join(thisPath, "Server", "WebServer.py")
    if not os.path.exists(server_check):
        print(f"  {YLW}[!]{RST} Server/WebServer.py not found at {server_check}")

    return debian_ver, codename


def stage_1_wifi(debian_ver, codename):
    print(f"\n  {BLU}[1/7]{RST} WiFi configuration...")

    if debian_ver >= 12:
        _, conn_result = run_cmd(
            "nmcli -t -f ACTIVE,SSID dev wifi list | grep '^yes:'", critical=False
        )
        if conn_result.strip():
            ssid = conn_result.strip().split(":")[1] if ":" in conn_result else "unknown"
            print(f"  {GRN}[+]{RST} Connected to: {ssid}")
            choice = input(f"  {YLW}?{RST} Reconfigure WiFi? (y/N): ").strip().lower()
            if choice != 'y':
                return

        ssid = input(f"  {YLW}?{RST} WiFi SSID: ").strip()
        if not ssid:
            print(f"  {YLW}[!]{RST} Empty SSID, skipping.")
            return
        psk = input(f"  {YLW}?{RST} WiFi Password: ").strip()

        for attempt in range(1, 4):
            rc, output = run_cmd(
                f'nmcli dev wifi connect "{ssid}" password "{psk}"', critical=False
            )
            if rc == 0:
                print(f"  {GRN}[+]{RST} Connected to: {ssid}")
                break
            print(f"  {YLW}[!]{RST} Attempt {attempt}/3 failed")
            if attempt < 3:
                time.sleep(5)
            else:
                print(f"  {RED}[!]{RST} Could not connect after 3 attempts")
    else:
        print(f"  {DIM}[i]{RST} Using wpa_supplicant (Bullseye or earlier)")
        wpa_conf = "/etc/wpa_supplicant/wpa_supplicant.conf"
        if not os.path.exists(wpa_conf):
            with open(wpa_conf, "w") as f:
                f.write("ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
                        "update_config=1\ncountry=US\n")
        ssid = input(f"  {YLW}?{RST} WiFi SSID: ").strip()
        if not ssid:
            return
        psk = input(f"  {YLW}?{RST} WiFi Password: ").strip()
        try:
            with open(wpa_conf, "a") as f:
                f.write(f'\nnetwork={{\n    ssid="{ssid}"\n    psk="{psk}"\n    key_mgmt=WPA-PSK\n}}\n')
            print(f"  {GRN}[+]{RST} WiFi '{ssid}' added")
        except Exception as e:
            print(f"  {RED}[!]{RST} Failed: {e}")


def stage_2_swap():
    print(f"\n  {BLU}[2/7]{RST} Configuring swap file...")

    _, sw_result = run_cmd("free -m | grep Swap", critical=False)
    current_swap = 0
    try:
        parts = sw_result.strip().split()
        if len(parts) >= 2:
            current_swap = int(parts[1])
    except (ValueError, IndexError):
        pass

    if current_swap >= 1900:
        print(f"  {GRN}[+]{RST} Swap already configured ({current_swap}MB)")
        return

    print(f"  {DIM}[i]{RST} Creating 2GB swap file...")

    # Stop all existing swap services first
    run_cmd("systemctl stop dphys-swapfile 2>/dev/null || true", critical=False)
    run_cmd("systemctl disable dphys-swapfile 2>/dev/null || true", critical=False)
    run_cmd("swapoff /var/swap 2>/dev/null || true", critical=False)
    run_cmd("swapoff /dev/zram0 2>/dev/null || true", critical=False)
    run_cmd("systemctl stop systemd-zram-setup@zram0 2>/dev/null || true", critical=False)
    run_cmd("systemctl stop rpi-resize-swap-file 2>/dev/null || true", critical=False)
    run_cmd("systemctl stop rpi-setup-loop@var-swap 2>/dev/null || true", critical=False)

    if os.path.exists("/var/swap"):
        try:
            os.remove("/var/swap")
        except Exception:
            run_cmd("rm -f /var/swap", critical=False)

    fallocate_ok = False
    rc, _ = run_cmd("fallocate -l 2G /var/swap 2>/dev/null", critical=False)
    if rc == 0 and os.path.exists("/var/swap"):
        try:
            file_size = os.path.getsize("/var/swap")
            if file_size >= 2 * 1024 * 1024 * 1024 - 1024 * 1024:
                fallocate_ok = True
            else:
                os.remove("/var/swap")
        except Exception:
            fallocate_ok = False

    if not fallocate_ok:
        print(f"  {DIM}[i]{RST} Using dd...")
        run_cmd("dd if=/dev/zero of=/var/swap bs=1M count=2048 status=progress")

    run_cmd("chmod 600 /var/swap")
    run_cmd("mkswap /var/swap")
    run_cmd("swapon /var/swap")

    try:
        with open("/etc/fstab", "r") as f:
            fstab = f.read()
        if "/var/swap" not in fstab:
            with open("/etc/fstab", "a") as f:
                f.write("\n/var/swap none swap sw 0 0\n")
            print(f"  {GRN}[+]{RST} Swap added to /etc/fstab")
    except Exception as e:
        print(f"  {YLW}[!]{RST} Could not update /etc/fstab: {e}")

    run_cmd("sysctl vm.swappiness=10", critical=False)
    try:
        os.makedirs("/etc/sysctl.d", exist_ok=True)
        with open("/etc/sysctl.d/99-picarpro.conf", "w") as f:
            f.write("vm.swappiness=10\n")
        print(f"  {GRN}[+]{RST} Swappiness=10")
    except Exception as e:
        print(f"  {YLW}[!]{RST} Could not set swappiness: {e}")


def stage_3_apt_packages(debian_ver, codename):
    print(f"\n  {BLU}[3/7]{RST} Installing system packages...")

    update_ok = False
    for attempt in range(1, 4):
        rc, output = run_cmd("apt update -y", critical=False)
        if rc == 0:
            update_ok = True
            break
        print(f"  {YLW}[!]{RST} apt update attempt {attempt}/3 failed")
        if attempt < 3:
            time.sleep(10)

    if not update_ok:
        print(f"  {RED}[!]{RST} apt update failed after 3 attempts, continuing anyway")
    else:
        run_cmd("apt upgrade -y", critical=False)

    all_packages = [
        "i2c-tools", "python3-smbus", "python3-gpiozero",
        "fonts-noto-color-emoji",
    ]

    if debian_ver >= 13:
        camera_packages = ["python3-opencv", "opencv-data"]
    else:
        camera_packages = ["python3-picamera2", "python3-opencv", "opencv-data"]

    all_packages.extend(camera_packages)
    all_packages.extend([
        "libfreetype6-dev", "libjpeg-dev", "build-essential",
        "network-manager",
    ])

    missing = [p for p in all_packages if not is_package_installed(p)]
    if not missing:
        print(f"  {GRN}[+]{RST} All packages already installed")
        return

    print(f"  {DIM}[*]{RST} Installing {len(missing)} packages...")
    run_cmd(f"apt-get install -y --no-install-recommends {' '.join(missing)}", critical=False)
    run_cmd("apt-get clean", critical=False)
    run_cmd("apt-get -y autoremove", critical=False)


def stage_4_pip_packages(debian_ver):
    print(f"\n  {BLU}[4/7]{RST} Installing Python packages...")

    pip_flag = "--break-system-packages" if debian_ver >= 12 else ""

    print(f"  {DIM}[*]{RST} Updating pip...")
    run_cmd(f"sudo -H pip3 install {pip_flag} --ignore-installed pip", critical=False)

    pip_groups = [
        ("I2C/Motor/Servo",
         f"sudo -H pip3 install {pip_flag} "
         "adafruit-circuitpython-pca9685 "
         "adafruit-circuitpython-motor "
         "adafruit-circuitpython-busdevice"),
        ("OLED/LED",
         f"sudo -H pip3 install {pip_flag} luma.oled rpi_ws281x"),
        ("Web Server",
         f"sudo -H pip3 install {pip_flag} flask flask_cors websockets"),
        ("Vision/Video",
         f"sudo -H pip3 install {pip_flag} numpy psutil imutils pybase64 pillow pyzmq"),
        ("IMU Sensor",
         f"sudo -H pip3 install {pip_flag} mpu6050-raspberrypi"),
        ("DS4 Controller",
         f"sudo -H pip3 install {pip_flag} evdev"),
    ]

    for group_name, cmd in pip_groups:
        print(f"  {DIM}[*]{RST} {group_name}...")
        run_cmd(cmd, critical=False)


def stage_5_hardware_config():
    print(f"\n  {BLU}[5/7]{RST} Configuring hardware...")

    run_cmd("raspi-config nonint do_i2c 0", critical=False)
    run_cmd("raspi-config nonint do_spi 0", critical=False)
    run_cmd("raspi-config nonint do_camera 0", critical=False)

    # Mask all failing rpi/zram/swap services to prevent boot errors
    services_to_mask = [
        "rpi-setup-resize.service",
        "rpi-resize-swap-file.service",
        "rpi-setup-loop@var-swap.service",
        "systemd-zram-setup@zram0.service",
        "dphys-swapfile.service",
    ]
    swap_units_to_mask = [
        "dev-zram0.swap",
    ]

    # Stop first, then disable+mask (order matters)
    for svc in services_to_mask:
        run_cmd(f"systemctl stop {svc} 2>/dev/null || true", critical=False)
    for svc in swap_units_to_mask:
        run_cmd(f"systemctl stop {svc} 2>/dev/null || true", critical=False)

    for svc in services_to_mask:
        run_cmd(f"systemctl disable {svc} 2>/dev/null || true", critical=False)
        run_cmd(f"systemctl mask {svc} 2>/dev/null || true", critical=False)
    for svc in swap_units_to_mask:
        run_cmd(f"systemctl mask {svc} 2>/dev/null || true", critical=False)

    run_cmd("swapoff /dev/zram0 2>/dev/null || true", critical=False)
    run_cmd("systemctl daemon-reload", critical=False)

    total = len(services_to_mask) + len(swap_units_to_mask)
    print(f"  {GRN}[+]{RST} Masked {total} failing boot services")

    config_path = get_boot_config_path()
    print(f"  {DIM}[*]{RST} Tuning {config_path}...")

    append_to_config("i2c_arm=on", "dtparam=i2c_arm=on", config_path)
    append_to_config("i2c_arm_baudrate", "dtparam=i2c_arm_baudrate=400000", config_path)
    append_to_config("gpu_mem=", "gpu_mem=128", config_path)

    if get_debian_version() < 12:
        append_to_config("start_x=1", "start_x=1", config_path)

    print(f"  {GRN}[+]{RST} Hardware configuration complete")


def stage_6_wifi_hotspot(debian_ver):
    print(f"\n  {BLU}[6/7]{RST} Setting up WiFi Hotspot...")

    if debian_ver < 12:
        print(f"  {YLW}[!]{RST} Hotspot requires Bookworm+ (Debian 12+)")
        return

    if not is_package_installed("network-manager"):
        run_cmd("apt-get install -y --no-install-recommends network-manager", critical=False)

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
        except Exception:
            pass

    print(f"\n  Hotspot SSID: {BOLD}{existing_ssid}{RST}")
    print(f"  Hotspot pass: {BOLD}{existing_pass}{RST}")
    choice = input(f"  {YLW}?{RST} Change? (y/N): ").strip().lower()

    if choice == 'y':
        new_ssid = input(f"  {YLW}?{RST} SSID [{existing_ssid}]: ").strip()
        if new_ssid:
            existing_ssid = new_ssid
        new_pass = input(f"  {YLW}?{RST} Password [{existing_pass}]: ").strip()
        if new_pass:
            if len(new_pass) < 8:
                print(f"  {RED}[!]{RST} WPA password needs 8+ chars")
            else:
                existing_pass = new_pass

    try:
        os.makedirs("/etc/picarpro", exist_ok=True)
        with open(config_file, "w") as f:
            f.write(f'HOTSPOT_SSID="{existing_ssid}"\n')
            f.write(f'HOTSPOT_PASS="{existing_pass}"\n')
        print(f"  {GRN}[+]{RST} Config saved")
    except Exception as e:
        print(f"  {RED}[!]{RST} Could not save config: {e}")

    hotspot_script = f"""#!/bin/bash
HOTSPOT_CONF="/etc/picarpro/hotspot.conf"
HOTSPOT_CONN="picarpro-hotspot"
HOTSPOT_SSID="{existing_ssid}"
HOTSPOT_PASS="{existing_pass}"
[ -f "$HOTSPOT_CONF" ] && source "$HOTSPOT_CONF"

for i in $(seq 1 30); do nmcli general status &>/dev/null && break; sleep 1; done

CONNECTED=$(nmcli -t -f ACTIVE,SSID dev wifi list 2>/dev/null | grep '^yes:' | head -1)
[ -n "$CONNECTED" ] && exit 0

FIRST_WIFI=$(nmcli -t -f NAME,TYPE con show 2>/dev/null | grep '802-11-wireless' | grep -v "$HOTSPOT_CONN" | head -1 | cut -d: -f1)
if [ -n "$FIRST_WIFI" ]; then
    nmcli con up id "$FIRST_WIFI" &>/dev/null
    sleep 5
fi

CONNECTED=$(nmcli -t -f ACTIVE,SSID dev wifi list 2>/dev/null | grep '^yes:' | head -1)
if [ -z "$CONNECTED" ]; then
    echo "[PiCarPro] Starting hotspot: $HOTSPOT_SSID"
    if nmcli con show "$HOTSPOT_CONN" &>/dev/null; then
        nmcli con delete "$HOTSPOT_CONN" &>/dev/null
    fi
    nmcli con add type wifi ifname wlan0 con-name "$HOTSPOT_CONN" autoconnect no ssid "$HOTSPOT_SSID"
    nmcli con modify "$HOTSPOT_CONN" 802-11-wireless.mode ap 802-11-wireless.band bg
    nmcli con modify "$HOTSPOT_CONN" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASS"
    nmcli con modify "$HOTSPOT_CONN" ipv4.method shared ipv4.addresses 10.42.0.1/24
    nmcli con up "$HOTSPOT_CONN"
fi
"""

    script_path = "/usr/local/bin/wifi_hotspot_manager.sh"
    try:
        with open(script_path, "w") as f:
            f.write(hotspot_script)
        os.chmod(script_path, 0o755)
        print(f"  {GRN}[+]{RST} Hotspot script saved")
    except Exception as e:
        print(f"  {RED}[!]{RST} Error: {e}")
        return

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
        print(f"  {GRN}[+]{RST} Hotspot service enabled")
    except Exception as e:
        print(f"  {RED}[!]{RST} Error: {e}")


def stage_7_systemd_service():
    print(f"\n  {BLU}[7/7]{RST} Setting up auto-start service...")

    server_path = os.path.join(thisPath, "Server", "WebServer.py")
    if not os.path.exists(server_path):
        print(f"  {RED}[!]{RST} Server file not found: {server_path}")
        return

    if os.path.exists("/etc/systemd/system/picarpro.service"):
        run_cmd("systemctl stop picarpro 2>/dev/null", critical=False)
        run_cmd("systemctl disable picarpro 2>/dev/null", critical=False)
        os.remove("/etc/systemd/system/picarpro.service")
        run_cmd("systemctl daemon-reload", critical=False)

    service_content = f"""[Unit]
Description=PiCar Pro Robot Server (Flask + WebSocket)
After=network-online.target picarpro-wifi.service
Wants=network-online.target picarpro-wifi.service

[Service]
Type=simple
User=root
Group=root
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
        print(f"  {GRN}[+]{RST} Auto-start service enabled!")
    except Exception as e:
        print(f"  {RED}[!]{RST} Error: {e}")


def main():
    debian_ver, codename = stage_0_preflight()
    stage_1_wifi(debian_ver, codename)
    stage_2_swap()
    stage_3_apt_packages(debian_ver, codename)
    stage_4_pip_packages(debian_ver)
    stage_5_hardware_config()
    stage_6_wifi_hotspot(debian_ver)
    stage_7_systemd_service()

    try:
        with open('/proc/device-tree/model', 'r') as f:
            pi_model = f.read().strip('\x00')
    except Exception:
        pi_model = 'Unknown'

    try:
        ip_result = subprocess.run(
            "hostname -I 2>/dev/null | awk '{print $1}'",
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        current_ip = ip_result.stdout.strip() or "(after reboot)"
    except Exception:
        current_ip = "(error)"

    # Gather system info
    cpu_info = "Unknown"
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Model'):
                    cpu_info = line.split(':')[1].strip()
                    break
                elif line.startswith('Hardware'):
                    cpu_info = line.split(':')[1].strip()
    except Exception:
        pass

    mem_total = 0
    mem_avail = 0
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                parts = line.split()
                if parts[0] == 'MemTotal:':
                    mem_total = int(parts[1]) // 1024
                elif parts[0] == 'MemAvailable:':
                    mem_avail = int(parts[1]) // 1024
    except Exception:
        pass
    mem_used = mem_total - mem_avail

    disk_info = shutil.disk_usage("/")
    disk_total_gb = disk_info.total // (1024**3)
    disk_used_gb = disk_info.used // (1024**3)
    disk_free_gb = disk_info.free // (1024**3)

    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    i2c_devices = []
    try:
        i2c_result = subprocess.run(
            "i2cdetect -y 1 2>/dev/null",
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        for line in (i2c_result.stdout or "").split('\n'):
            for part in line.split():
                if part.startswith('--') or part.startswith('0'):
                    continue
                try:
                    int(part, 16)
                    i2c_devices.append(part)
                except ValueError:
                    pass
    except Exception:
        pass

    kernel_ver = platform.release()
    arch = platform.machine()

    print(f"\n{BOLD}{GRN}{'=' * 55}{RST}")
    print(f"  {BOLD}{GRN}SETUP COMPLETE!{RST}")
    print(f"{BOLD}{GRN}{'=' * 55}{RST}")
    print(f"")
    print(f"  {BOLD}System Information:{RST}")
    print(f"  Model:    {pi_model}")
    print(f"  CPU:      {cpu_info}")
    print(f"  Kernel:   {kernel_ver}")
    print(f"  Arch:     {arch}")
    print(f"  OS:       Debian {debian_ver} ({codename})")
    print(f"  Python:   {py_version}")
    print(f"")
    print(f"  {BOLD}Resources:{RST}")
    print(f"  RAM:      {mem_used}MB / {mem_total}MB ({round(100*mem_used/mem_total if mem_total else 0,1)}%)")
    print(f"  Disk:     {disk_used_gb}GB / {disk_total_gb}GB ({disk_free_gb}GB free)")
    print(f"")
    print(f"  {BOLD}I2C Devices:{RST}")
    if i2c_devices:
        print(f"  Found:    {', '.join('0x'+d for d in i2c_devices)}")
    else:
        print(f"  No I2C devices detected")
    print(f"")
    print(f"  {BOLD}Network:{RST}")
    print(f"  IP:       {current_ip}")
    print(f"  Hotspot:  {existing_ssid if 'existing_ssid' in dir() else 'Adeept_Robot'}")
    print(f"")
    print(f"  {BOLD}Server:{RST}")
    print(f"  Path:     {thisPath}")
    print(f"  Start:    python3 Server/WebServer.py")
    print(f"  Web:      http://{current_ip}:5000")
    print(f"  WS:       ws://{current_ip}:8888")
    print(f"  Service:  picarpro.service (auto-start)")
    print(f"  Log:      {LOG_FILE}")
    print(f"")
    print(f"  {BOLD}Next Steps:{RST}")
    print(f"  1. Reboot:  {YLW}sudo reboot{RST}")
    print(f"  2. Open:    {YLW}http://{current_ip}:5000{RST}")
    print(f"  3. Check:   {YLW}systemctl status picarpro{RST}")
    print(f"{BOLD}{GRN}{'=' * 55}{RST}")

    while True:
        choice = input(f"\n  {YLW}?{RST} Reboot now? (y/N): ").strip().lower()
        if choice in ['y', 'yes']:
            print(f"\n  {GRN}Rebooting in 3s...{RST}")
            time.sleep(3)
            os.system("reboot")
            break
        elif choice in ['n', 'no', '']:
            break


if __name__ == "__main__":
    main()
