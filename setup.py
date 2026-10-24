#!/usr/bin/env python3
"""Установщик прошивки PiCar-Pro (слияние jsck37 + Adeept v1/v2).

* включает I2C/SPI/camera;
* ставит системные и Python-зависимости;
* создаёт systemd-сервис picarpro (автозапуск прошивки);
* создаёт systemd-сервис picarpro-hotspot (порт из v2: Wi-Fi либо
  резервная точка доступа с параметрами из config.py).

Отличия от оригинальных setup.py: без chmod 777, без хрупкой правки
rc.local, без create_ap из GitHub, идемпотентная установка сервисов.
Поддерживает Debian 11-13 (Bullseye / Bookworm / Trixie).
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


def _debian_release():
    """(major, codename) текущего Debian/RPi OS; (0, '') если не удалось."""
    major = 0
    codename = ''
    try:
        with open('/etc/debian_version') as f:
            s = f.read().strip()
        token = s.split('/')[0]              # '13.2' | 'trixie/sid' | '12.5'
        if token[:1].isdigit():
            major = int(token.split('.')[0])
    except Exception:
        pass
    try:
        with open('/etc/os-release') as f:
            for line in f:
                if line.startswith('VERSION_CODENAME='):
                    codename = line.split('=', 1)[1].strip().strip('"')
    except Exception:
        pass
    if not major:
        major = {'bullseye': 11, 'bookworm': 12,
                 'trixie': 13}.get(codename, 0)
    return major, codename


def enable_interfaces():
    step('Enabling I2C, SPI, camera...')
    run('sudo raspi-config nonint do_i2c 0', check=False)
    run('sudo modprobe i2c-dev', check=False)
    run('sudo raspi-config nonint do_spi 0', check=False)
    run('sudo modprobe spidev', check=False)
    if _debian_release()[0] < 13:
        # Trixie: камера включена всегда, do_camera из raspi-config удалён
        run('sudo raspi-config nonint do_camera 0', check=False)
    ok('Interfaces enabled')


def install_packages():
    step('Installing system packages...')
    run(['sudo', 'apt-get', 'update', '-y'])

    major, codename = _debian_release()
    if major >= 13 or codename in ('trixie', 'forky', 'sid'):
        # Debian 13 / RPi OS Trixie: пакеты переименованы
        #   libtiff5 -> libtiff6;
        #   libraspberrypi-bin -> raspi-utils-core (в нём и vcgencmd,
        #     нужен мониторингу напряжения) + raspi-utils-dt;
        #   raspberrypi-kernel-headers удалён — прошивка не собирает
        #     модули ядра и в заголовках не нуждается.
        ok(f'Detected Debian {major or "?"} ({codename or "trixie"}): '
           'using new package names')
        core = ['git', 'python3-pip', 'python3-dev', 'build-essential',
                'libopenjp2-7', 'libtiff6', 'libjpeg-dev', 'zlib1g-dev',
                'raspi-utils-core', 'raspi-utils-dt',
                'i2c-tools', 'bluetooth', 'bluez', 'bluez-firmware',
                'network-manager']
    else:
        core = ['git', 'python3-pip', 'python3-dev', 'build-essential',
                'libopenjp2-7', 'libtiff5', 'libjpeg-dev', 'zlib1g-dev',
                'libraspberrypi-bin', 'raspberrypi-kernel-headers',
                'i2c-tools', 'bluetooth', 'bluez', 'bluez-firmware',
                'pi-bluetooth', 'network-manager']

    r = run(['sudo', 'apt-get', 'install', '-y', *core], check=False)
    if r is None or r.returncode != 0:
        # защита от будущих переименований: недостающий пакет не должен
        # ронять весь установщик — добираем по одному
        warn('Batch install failed — retrying package-by-package...')
        for pkg in core:
            run(['sudo', 'apt-get', 'install', '-y', pkg], check=False)

    # best-effort: отсутствие этого пакета не критично
    run(['sudo', 'apt-get', 'install', '-y', 'pi-bluetooth'], check=False)


def install_python_deps():
    step('Installing Python packages...')
    major, _ = _debian_release()
    flag = ['--break-system-packages'] if major >= 12 else []

    # picamera2 (Debian 12+): рабочий способ — только apt
    # (python3-picamera2 с системными биндингами libcamera). Версия с PyPI
    # ставится, но не импортируется. На Debian 12+ она фильтруется из
    # requirements.txt перед установкой через pip.
    reqs = str(PROJECT_DIR / 'requirements.txt')
    pip_reqs = reqs
    tmp = None
    if major >= 12:
        r = run(['sudo', 'apt-get', 'install', '-y',
                 'python3-picamera2', 'python3-libcamera'], check=False)
        if r and r.returncode == 0:
            ok('picamera2 installed from apt (system libcamera bindings)')
            try:
                lines = [ln for ln in Path(reqs).read_text().splitlines()
                         if ln.strip() and not ln.strip().startswith('#')
                         and ln.strip() != 'picamera2']
                tmp = Path('/tmp/picarpro_requirements.txt')
                tmp.write_text('\n'.join(lines) + '\n')
                pip_reqs = str(tmp)
            except Exception:
                tmp = None
                pip_reqs = reqs

    run(['sudo', '-H', 'pip3', 'install', *flag, '--upgrade', 'pip'], check=False)
    run(['sudo', '-H', 'pip3', 'install', *flag, '-r', pip_reqs])
    if tmp is not None:
        tmp.unlink(missing_ok=True)


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
    print(f'  Swap:    {BOLD}sudo bash tools/setup_swap.sh{RST} '
          '(2 ГБ подкачки — обязательно для YOLO на 1 ГБ ОЗУ)')
    print(f'  Camera:  {BOLD}python3 tools/selftest.py{RST} (аппаратный самотест)')
    print()
    print(f'{YLW}Reboot recommended to activate all kernel modules.{RST}')


if __name__ == '__main__':
    main()
