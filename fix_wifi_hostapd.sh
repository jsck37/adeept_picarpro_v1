#!/bin/bash
# =============================================================================
# PiCarPro Wi-Fi rewrite (v1.3.5): hostapd + dnsmasq + SSH at system level.
#
# NetworkManager AP больше не используется: wlan0 навсегда изымается из-под
# управления NM и отдаётся hostapd. Точка доступа работает всегда при загрузке,
# SSH включён на уровне системы. Идемпотентен — можно запускать повторно.
#
# Использование:  sudo bash fix_wifi_hostapd.sh
# Настройки:      /etc/picarpro/hotspot.conf
#                   HOTSPOT_SSID, HOTSPOT_PASS, HOTSPOT_CHANNEL, HOTSPOT_IP
#                 после правки:
#                   sudo systemctl restart picarpro-hotspot picarpro-hotspot-dhcp
#
# Переменные для тестирования (в обычной работе не задаются):
#   PICARPRO_CONF, PICARPRO_SYSDDIR, PICARPRO_BINDIR, PICARPRO_NMDIR,
#   PICARPRO_ALLOW_NONROOT=1
# =============================================================================
set -u

CONF="${PICARPRO_CONF:-/etc/picarpro/hotspot.conf}"
SYSDDIR="${PICARPRO_SYSDDIR:-/etc/systemd/system}"
BINDIR="${PICARPRO_BINDIR:-/usr/local/bin}"
NMDIR="${PICARPRO_NMDIR:-/etc/NetworkManager/conf.d}"
RUN_DIR="/run"   # runtime-конфиги hostapd/dnsmasq; очищаются при загрузке

log()  { echo "[PiCarPro-AP] $*"; }
fail() { echo "[PiCarPro-AP] ERROR: $*"; exit 1; }

if [ "$(id -u)" -ne 0 ] && [ "${PICARPRO_ALLOW_NONROOT:-0}" != "1" ]; then
    fail "run as root: sudo bash fix_wifi_hostapd.sh"
fi

# --- 1. Настройки ------------------------------------------------------------
if [ ! -f "$CONF" ]; then
    mkdir -p "$(dirname "$CONF")"
    cat > "$CONF" << 'PEOF'
HOTSPOT_SSID="Adeept_Robot"
HOTSPOT_PASS="12345678"
HOTSPOT_CHANNEL="6"
HOTSPOT_IP="10.42.0.1"
PEOF
    log "создан конфиг по умолчанию: $CONF"
fi
# shellcheck disable=SC1090
. "$CONF"
HOTSPOT_SSID="${HOTSPOT_SSID:-Adeept_Robot}"
HOTSPOT_PASS="${HOTSPOT_PASS:-12345678}"
HOTSPOT_CHANNEL="${HOTSPOT_CHANNEL:-6}"
HOTSPOT_IP="${HOTSPOT_IP:-10.42.0.1}"

PASSLEN=${#HOTSPOT_PASS}
[ "$PASSLEN" -ge 8 ] && [ "$PASSLEN" -le 63 ] \
    || fail "HOTSPOT_PASS должен быть 8..63 символов — правьте $CONF"

# --- 2. Пакеты ---------------------------------------------------------------
need=()
dpkg -s hostapd        >/dev/null 2>&1 || need+=(hostapd)
dpkg -s dnsmasq-base   >/dev/null 2>&1 || need+=(dnsmasq-base)
dpkg -s openssh-server >/dev/null 2>&1 || need+=(openssh-server)
if [ "${#need[@]}" -gt 0 ]; then
    log "установка пакетов: ${need[*]}"
    apt-get install -y "${need[@]}" >/dev/null 2>&1 \
        || { apt-get update >/dev/null 2>&1 && apt-get install -y "${need[@]}" >/dev/null 2>&1; } \
        || fail "apt install ${need[*]} — проверьте сеть и репозитории"
fi

# --- 3. Снос предыдущей NM-реализации ---------------------------------------
systemctl disable --now picarpro-wifi.service >/dev/null 2>&1
rm -f "$SYSDDIR/picarpro-wifi.service" "$BINDIR/wifi_hotspot_manager.sh"
nmcli con delete picarpro-hotspot >/dev/null 2>&1
systemctl disable --now hostapd.service  >/dev/null 2>&1   # дистрибутивный юнит
systemctl disable --now dnsmasq.service  >/dev/null 2>&1   # системный dnsmasq занимает :53

# --- 4. wlan0 навсегда вне NetworkManager (постоянно + на текущий сеанс) -----
mkdir -p "$NMDIR"
cat > "$NMDIR/picarpro-unmanage-wlan0.conf" << 'PEOF'
[keyfile]
unmanaged-devices=interface-name:wlan0
PEOF
if command -v nmcli >/dev/null 2>&1; then
    nmcli device disconnect wlan0 >/dev/null 2>&1
    nmcli device set wlan0 managed no >/dev/null 2>&1
fi

# --- 5. Скрипт подготовки wlan0 (выполняется как ExecStartPre hostapd) ------
mkdir -p "$BINDIR"
cat > "$BINDIR/picarpro-hotspot-setup.sh" << 'PEOF'
#!/bin/bash
# PiCarPro AP setup: изъять wlan0 из NM, назначить IP, сгенерировать конфиги
# hostapd/dnsmasq. Запускается перед каждым стартом hostapd (ExecStartPre) —
# при сбое hostapd-юнит перезапускается systemd и всё повторяется.
set -u
CONF="${PICARPRO_CONF:-/etc/picarpro/hotspot.conf}"
RUN_DIR="${PICARPRO_RUN_DIR:-/run}"
[ -r "$CONF" ] || { echo "[PiCarPro-AP] ERROR: $CONF не читается"; exit 1; }
# shellcheck disable=SC1090
. "$CONF"
DEV="${HOTSPOT_INTERFACE:-wlan0}"
HOTSPOT_IP="${HOTSPOT_IP:-10.42.0.1}"
HOTSPOT_CHANNEL="${HOTSPOT_CHANNEL:-6}"

log() { echo "[PiCarPro-AP] $*"; }

# 1. Ждём Wi-Fi-интерфейс (brcmfmac грузит прошивку уже после старта systemd)
for i in $(seq 1 60); do
    [ -d "/sys/class/net/$DEV" ] && break
    sleep 1
done
if [ ! -d "/sys/class/net/$DEV" ]; then
    log "ERROR: $DEV не появился за 60 с (rfkill / прошивка / железо)"
    log "Проверка: rfkill list; ip link; dmesg | grep brcmfmac"
    exit 1
fi

# 2. Радио разблокировано
rfkill unblock wifi 2>/dev/null

# 3. Изъятие интерфейса у NetworkManager (постоянное правило читается NM при
#    загрузке; это подстраховка на текущий сеанс)
if command -v nmcli >/dev/null 2>&1; then
    nmcli device disconnect "$DEV" >/dev/null 2>&1
    nmcli device set "$DEV" managed no >/dev/null 2>&1
fi

# 4. Валидация пароля (требование hostapd/WPA2)
PASSLEN=${#HOTSPOT_PASS}
[ -n "${HOTSPOT_PASS:-}" ] && [ "$PASSLEN" -ge 8 ] && [ "$PASSLEN" -le 63 ] \
    || { log "ERROR: HOTSPOT_PASS должен быть 8..63 символов (сейчас $PASSLEN)"; exit 1; }
[ -n "${HOTSPOT_SSID:-}" ] || { log "ERROR: HOTSPOT_SSID пуст"; exit 1; }

# 5. Сброс линка и статический IP стороны AP
ip link set "$DEV" down
sleep 1
ip link set "$DEV" up
ip addr flush dev "$DEV"
ip addr add "$HOTSPOT_IP/24" dev "$DEV"

# 6. Конфиг hostapd (runtime)
PREFIX=$(echo "$HOTSPOT_IP" | cut -d. -f1-3)
cat > "$RUN_DIR/picarpro-hostapd.conf" << EOF
interface=$DEV
driver=nl80211
ssid=$HOTSPOT_SSID
channel=$HOTSPOT_CHANNEL
hw_mode=g
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=$HOTSPOT_PASS
EOF

# 7. Конфиг dnsmasq: DHCP/DNS для телефонов, подключающихся к AP
cat > "$RUN_DIR/picarpro-dnsmasq.conf" << EOF
interface=$DEV
bind-interfaces
except-interface=lo
dhcp-range=$PREFIX.50,$PREFIX.150,255.255.255.0,12h
dhcp-option=option:router,$HOTSPOT_IP
dhcp-option=option:dns-server,$HOTSPOT_IP
no-resolv
no-hosts
dhcp-authoritative
log-dhcp
EOF

log "$DEV готов: SSID '$HOTSPOT_SSID' канал $HOTSPOT_CHANNEL IP $HOTSPOT_IP"
exit 0
PEOF
chmod 755 "$BINDIR/picarpro-hotspot-setup.sh"

# --- 6. systemd-юниты ---------------------------------------------------------
cat > "$SYSDDIR/picarpro-hotspot.service" << PEOF
[Unit]
Description=PiCarPro AP: hostapd access point (setup via ExecStartPre)
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
ExecStartPre=$BINDIR/picarpro-hotspot-setup.sh
ExecStart=/usr/sbin/hostapd $RUN_DIR/picarpro-hostapd.conf
TimeoutStartSec=120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
PEOF

cat > "$SYSDDIR/picarpro-hotspot-dhcp.service" << PEOF
[Unit]
Description=PiCarPro AP: dnsmasq DHCP/DNS for hotspot clients
Requires=picarpro-hotspot.service
After=picarpro-hotspot.service

[Service]
Type=simple
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=$RUN_DIR/picarpro-dnsmasq.conf
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
PEOF

systemctl daemon-reload
systemctl enable picarpro-hotspot.service picarpro-hotspot-dhcp.service >/dev/null 2>&1

# --- 7. SSH на уровне системы -------------------------------------------------
systemctl enable ssh.service  >/dev/null 2>&1 \
    || systemctl enable sshd.service >/dev/null 2>&1 \
    || log "ВНИМАНИЕ: не удалось включить ssh.service"
systemctl start ssh.service >/dev/null 2>&1 || true

# --- 8. Запуск прямо сейчас (без перезагрузки) ---------------------------------
systemctl start picarpro-hotspot.service >/dev/null 2>&1 || true
systemctl start picarpro-hotspot-dhcp.service >/dev/null 2>&1 || true

# --- 9. Итог -------------------------------------------------------------------
echo
log "Wi-Fi переписан на hostapd + dnsmasq, SSH включён"
log "  SSID     : $HOTSPOT_SSID   канал: $HOTSPOT_CHANNEL"
log "  IP Pi    : $HOTSPOT_IP"
log "  Веб UI   : http://$HOTSPOT_IP:5000"
log "  SSH      : ssh <пользователь>@$HOTSPOT_IP"
log "  Настройки: $CONF"
log "  После правки конфига:"
log "    sudo systemctl restart picarpro-hotspot picarpro-hotspot-dhcp"
log "  Логи:"
log "    journalctl -u picarpro-hotspot -e    (hostapd)"
log "    journalctl -u picarpro-hotspot-dhcp -e  (DHCP)"
exit 0
