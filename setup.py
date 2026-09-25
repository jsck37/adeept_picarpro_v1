#!/usr/bin/env python3
"""Установщик прошивки PiCar-Pro (слияние jsck37 + Adeept v1/v2).

* включает I2C/SPI/camera;
* ставит системные и Python-зависимости;
* создаёт systemd-сервис picarpro (автозапуск прошивки);
* создаёт systemd-сервис picarpro-hotspot (порт из v2: Wi-Fi либо
  резервная точка доступа с параметрами из config.py).

Отличия от оригинальных setup.py: без chmod 777, без хрупкой правки
rc.local, без create_ap из GitHub, идемпотентная установка сервисов.
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SERVICE_NAME = 'picarpro'
HOTSPOT_SERVICE_NAME = 'picarpro-hotspot'

# --- параметры хотспота берутся из config.py проекта ------------------------
import re as _re
_cfg = (PROJECT_DIR / 'config.py').read_text()


def _cfg_value(name, default):
    m = _re.search(rf"^{name}\s*=\s*['\"](.+?)['\"]", _cfg, _re.M)
    return m.group(1) if m else default


HOTSPOT_SSID = _cfg_value('HOTSPOT_SSID', 'Adeept_Robot')
HOTSPOT_PASSWORD = _cfg_value('HOTSPOT_PASSWORD', '12345678')
HOTSPOT_IP = _cfg_value('HOTSPOT_IP', '10.42.0.1')

SERVICE_FILE = f'''[Unit]
Description=PiCar Pro robot server
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

HOTSPOT_SERVICE_FILE = f'''[Unit]
Description=PiCar Pro WiFi/Hotspot manager (from Adeept v2)
After=NetworkManager.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={PROJECT_DIR}/wifi_hotspot_manager.sh
RemainAfterExit=yes

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
    """Безопасный запуск: список аргументов без shell, если не задан список строк."""
    if isinstance(cmd, str):
        cmd_list = cmd.split()
    else:
        cmd_list = cmd
    try:
        if capture:
            r = subprocess.run(cmd_list, capture_output=True, text=True, timeout=300)
            if check and r.returncode != 0:
                err(f'Command failed: {" ".join(cmd_list)}')
                print(r.stdout)
                print(r.stderr)
                sys.exit(1)
            return r
        r = subprocess.run(cmd_list, timeout=300)
        if check and r.returncode != 0:
            err(f'Command failed: {" ".join(cmd_list)}')
            sys.exit(1)
        return r
    except FileNotFoundError:
        warn(f'Command not found: {cmd_list[0]} (skipped)')
        return None


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
    run(['sudo', 'apt-get', 'update', '-y'])
    run(['sudo', 'apt-get', 'install', '-y',
         'git', 'python3-pip', 'python3-dev', 'build-essential',
         'libopenjp2-7', 'libtiff5', 'libjpeg-dev', 'zlib1g-dev',
         'libraspberrypi-bin', 'raspberrypi-kernel-headers',
         'i2c-tools', 'bluetooth', 'bluez', 'bluez-firmware',
         'pi-bluetooth', 'network-manager'])


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
    run(['sudo', '-H', 'pip3', 'install', flag, '--upgrade', 'pip'], check=False)
    run(['sudo', '-H', 'pip3', 'install', flag,
         '-r', str(PROJECT_DIR / 'requirements.txt')])


def _write_service(name, content):
    """Идемпотентная установка unit-файла: tmp-файл + sudo cp (без shell)."""
    service_path = f'/etc/systemd/system/{name}.service'
    tmp = Path('/tmp') / f'{name}.service'
    tmp.write_text(content)
    run(['sudo', 'cp', str(tmp), service_path], check=False)
    tmp.unlink(missing_ok=True)


def install_services():
    step('Installing systemd services...')
    _write_service(SERVICE_NAME, SERVICE_FILE)
    _write_service(HOTSPOT_SERVICE_NAME, HOTSPOT_SERVICE_FILE)
    run(['sudo', 'chmod', '644', f'/etc/systemd/system/{SERVICE_NAME}.service',
         f'/etc/systemd/system/{HOTSPOT_SERVICE_NAME}.service'], check=False)
    # скрипт хотспота исполняемым
    run(['sudo', 'chmod', '755', str(PROJECT_DIR / 'wifi_hotspot_manager.sh')],
        check=False)
    run(['sudo', 'systemctl', 'daemon-reload'])
    run(['sudo', 'systemctl', 'enable', SERVICE_NAME])
    run(['sudo', 'systemctl', 'enable', HOTSPOT_SERVICE_NAME], check=False)
    ok(f'Services installed: {SERVICE_NAME}.service, {HOTSPOT_SERVICE_NAME}.service')
    ok(f'Hotspot: SSID={HOTSPOT_SSID} IP={HOTSPOT_IP}')


def main():
    print(f'\n{BOLD}  PiCar Pro — installer (merged edition){RST}\n')
    if not is_raspberry_pi():
        warn('Not running on a Raspberry Pi — proceeding anyway (some steps may fail)')
    enable_interfaces()
    install_packages()
    install_python_deps()
    install_services()
    print()
    ok(f'{BOLD}Done!{RST}')
    print()
    print(f'  Start:   {BOLD}sudo systemctl start {SERVICE_NAME}{RST}')
    print(f'  Status:  {BOLD}sudo systemctl status {SERVICE_NAME}{RST}')
    print(f'  Logs:    {BOLD}sudo journalctl -u {SERVICE_NAME} -f{RST}')
    print(f'  Web UI:  {BOLD}http://<this-pi-ip>:5000{RST} (или порт 80)')
    print(f'  Tests:   {BOLD}python3 -m pytest tests/ -v{RST} (на ПК, без железа)')
    print()
    print(f'{YLW}Reboot recommended to activate all kernel modules.{RST}')


if __name__ == '__main__':
    main()
