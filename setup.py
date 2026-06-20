#!/usr/bin/env python3
import os, subprocess, sys, shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SERVICE_NAME = 'picarpro'
SERVICE_FILE = f'''[Unit]
Description=PiCar Pro v1 robot server
After=network-online.target bluetooth.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={PROJECT_DIR}
ExecStart=/usr/bin/python3 {PROJECT_DIR}/boot.py
Restart=on-failure
RestartSec=5
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
'''

RST = '\033[0m'
BOLD = '\033[1m'
GRN = '\033[92m'
YLW = '\033[93m'
RED = '\033[91m'
BLU = '\033[94m'


def step(msg):
    print(f'{BLU}[*]{RST} {msg}')

def ok(msg=''):
    print(f'{GRN}[+]{RST} {msg}')

def warn(msg):
    print(f'{YLW}[!]{RST} {msg}')

def err(msg):
    print(f'{RED}[x]{RST} {msg}')

def run(cmd, check=True, capture=False):
    if capture:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        if check and r.returncode != 0:
            err(f'Command failed: {cmd}')
            print(r.stdout)
            print(r.stderr)
            sys.exit(1)
        return r
    else:
        r = subprocess.run(cmd, shell=True, timeout=300)
        if check and r.returncode != 0:
            err(f'Command failed: {cmd}')
            sys.exit(1)
        return r

def is_raspberry_pi():
    try:
        with open('/proc/device-tree/model') as f:
            return 'Raspberry Pi' in f.read()
    except Exception:
        return False

def enable_interfaces():
    step('Enabling I2C, SPI, camera...')
    run('sudo raspi-config nonint do_i2c 0', check=False)
    run('sudo modprobe i2c-dev', check=False)
    run('sudo raspi-config nonint do_spi 0', check=False)
    run('sudo modprobe spidev', check=False)
    run('sudo raspi-config nonint do_camera 0', check=False)
    ok('Interfaces enabled')

def install_packages():
    step('Installing system packages...')
    run('sudo apt-get update -y')
    run('sudo apt-get install -y git python3-pip python3-dev build-essential '
        'libopenjp2-7 libtiff5 libjpeg-dev zlib1g-dev '
        'libraspberrypi-bin raspberrypi-kernel-headers '
        'i2c-tools bluetooth bluez bluez-firmware pi-bluetooth')

def install_python_deps():
    step('Installing Python packages...')
    flag = '--break-system-packages'
    try:
        with open('/etc/debian_version') as f:
            ver = f.read().strip()
            if ver and int(ver.split('.')[0]) < 12:
                flag = ''
    except Exception:
        pass
    run(f'sudo -H pip3 install {flag} --upgrade pip', check=False)
    run(f'sudo -H pip3 install {flag} -r {PROJECT_DIR}/requirements.txt')

def install_service():
    step('Installing systemd service...')
    service_path = f'/etc/systemd/system/{SERVICE_NAME}.service'
    run(f"sudo tee {service_path} > /dev/null << 'EOF'\n{SERVICE_FILE}EOF", check=False)
    run('sudo systemctl daemon-reload')
    run(f'sudo systemctl enable {SERVICE_NAME}')
    ok(f'Service installed: {SERVICE_NAME}.service')

def main():
    print(f'\n{BOLD}  PiCar Pro v1 — installer{RST}\n')
    if not is_raspberry_pi():
        warn('Not running on a Raspberry Pi — proceeding anyway (some steps may fail)')
    enable_interfaces()
    install_packages()
    install_python_deps()
    install_service()
    print()
    ok(f'{BOLD}Done!{RST}')
    print()
    print(f'  Start:   {BOLD}sudo systemctl start {SERVICE_NAME}{RST}')
    print(f'  Status:  {BOLD}sudo systemctl status {SERVICE_NAME}{RST}')
    print(f'  Logs:    {BOLD}sudo journalctl -u {SERVICE_NAME} -f{RST}')
    print(f'  Web UI:  {BOLD}http://<this-pi-ip>:5000{RST}')
    print()
    print(f'{YLW}Reboot recommended to activate all kernel modules.{RST}')

if __name__ == '__main__':
    main()
