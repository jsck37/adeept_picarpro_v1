#!/usr/bin/env python3
"""Имитатор интерактивного bluetoothctl (BlueZ 5.6x): цветной вывод, приглашения
без перевода строки, события [NEW]/[CHG]/[DEL]. Сценарий — JSON из $FAKE_BT_SCENARIO,
журнал полученных команд — в $FAKE_BT_LOG.

Сценарий: {"adapter": true, "devices": {MAC: {name, icon, paired, trusted,
connected, known, visible, rssi}}, "pair": "ok|auth_fail|not_available",
"connect": "ok|page_timeout|auth_fail|already", "scan_delay": 0.2,
"flood": 0, "autoconnect_after_pair": true}
"""
import json
import os
import sys
import threading
import time

SC = json.load(open(os.environ['FAKE_BT_SCENARIO']))
LOG = os.environ.get('FAKE_BT_LOG')
DEV = SC.setdefault('devices', {})
G, Y, B, R = '\x1b[0;92m', '\x1b[0;93m', '\x1b[0;94m', '\x1b[0;91m'
Z = '\x1b[0m'
lock = threading.Lock()
scanning = False


def out(text):
    with lock:
        sys.stdout.write(text + '\n')
        sys.stdout.flush()


def prompt():
    with lock:
        sys.stdout.write(f'\r{B}[bluetooth]{Z}# ')   # без \n, как настоящий
        sys.stdout.flush()


def new(mac, d):
    out(f'[{G}NEW{Z}] Device {mac} {d["name"]}')
    d['known'] = True


def chg(mac, key, val):
    out(f'[{Y}CHG{Z}] Device {mac} {key}: {val}')


def scan_thread():
    time.sleep(SC.get('scan_delay', 0.2))
    for mac, d in list(DEV.items()):
        if not scanning:
            return
        if d.get('visible') and not d.get('known'):
            new(mac, d)
        if d.get('visible') and d.get('rssi') is not None:
            chg(mac, 'RSSI', d['rssi'])
    for i in range(SC.get('flood', 0)):
        if not scanning:
            return
        out(f'[{G}NEW{Z}] Device 11:22:33:44:{i // 256:02X}:{i % 256:02X} Noise {i}')


def info(mac):
    d = DEV.get(mac)
    if not d or not d.get('known'):
        out(f'Device {mac} not available')
        return
    out(f'Device {mac} (public)')
    out(f'\tName: {d["name"]}\n\tAlias: {d["name"]}\n\tIcon: {d.get("icon", "")}')
    out(f'\tPaired: {"yes" if d.get("paired") else "no"}')
    out(f'\tTrusted: {"yes" if d.get("trusted") else "no"}')
    out(f'\tConnected: {"yes" if d.get("connected") else "no"}')


def handle(line):
    global scanning
    parts = line.split()
    if not parts:
        return
    cmd, arg = parts[0], (parts[1].upper() if len(parts) > 1 else '')
    if LOG:
        with open(LOG, 'a') as f:
            f.write(line + '\n')
    if cmd == 'power':
        out('Changing power on succeeded')
    elif cmd == 'agent':
        out('Agent registered')
    elif cmd == 'default-agent':
        out('Default agent request successful')
    elif cmd == 'pairable':
        out(f'[{Y}CHG{Z}] Controller 00:11:22:33:44:55 Pairable: yes')
    elif cmd == 'show':
        if not SC.get('adapter', True):
            out('No default controller available')
        else:
            out('Controller 00:11:22:33:44:55 (public)\n\tPowered: yes\n\tPairable: yes')
    elif cmd == 'scan':
        if arg == 'ON':
            scanning = True
            out('Discovery started')
            threading.Thread(target=scan_thread, daemon=True).start()
        else:
            scanning = False
            out('Discovery stopped')
    elif cmd == 'devices':
        for mac, d in DEV.items():
            if d.get('known'):
                out(f'Device {mac} {d["name"]}')
    elif cmd == 'info':
        info(arg)
    elif cmd == 'pair':
        d = DEV.get(arg)
        if not d or not d.get('known'):
            out(f'Device {arg} not available')
            return
        out(f'Attempting to pair with {arg}')
        time.sleep(0.2)
        mode = SC.get('pair', 'ok')
        if mode == 'ok':
            d['paired'] = True
            chg(arg, 'Paired', 'yes')
            if SC.get('autoconnect_after_pair', True):
                d['connected'] = True
                chg(arg, 'Connected', 'yes')
            out('Pairing successful')
        else:
            out('Failed to pair: org.bluez.Error.AuthenticationFailed')
    elif cmd == 'trust':
        d = DEV.get(arg)
        if d:
            d['trusted'] = True
            chg(arg, 'Trusted', 'yes')
            out(f'Changing {arg} trust succeeded')
        else:
            out(f'Device {arg} not available')
    elif cmd == 'connect':
        d = DEV.get(arg)
        if not d or not d.get('known'):
            out(f'Device {arg} not available')
            return
        out(f'Attempting to connect to {arg}')
        time.sleep(0.2)
        mode = SC.get('connect', 'ok')
        if mode == 'ok':
            d['connected'] = True
            chg(arg, 'Connected', 'yes')
            out('Connection successful')
        elif mode == 'page_timeout':
            out('Failed to connect: org.bluez.Error.Failed br-connection-page-timeout')
        elif mode == 'auth_fail':
            out('Failed to connect: org.bluez.Error.AuthenticationFailed')
        elif mode == 'already':
            out('Failed to connect: org.bluez.Error.AlreadyConnected')
    elif cmd == 'disconnect':
        d = DEV.get(arg)
        if d:
            d['connected'] = False
            chg(arg, 'Connected', 'no')
        out('Successful disconnected')
    elif cmd == 'remove':
        d = DEV.get(arg)
        if d and d.get('known'):
            out(f'[{R}DEL{Z}] Device {arg} {d["name"]}')
            d.update(known=False, paired=False, trusted=False, connected=False)
            out('Device has been removed')
            if SC.get('after_remove_connect'):
                SC['connect'] = SC['after_remove_connect']
            if SC.get('after_remove_pair'):
                SC['pair'] = SC['after_remove_pair']
        else:
            out(f'Device {arg} not available')
    elif cmd == 'quit':
        sys.exit(0)


for raw in sys.stdin:
    handle(raw.strip())
    prompt()
