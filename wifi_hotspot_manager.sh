#!/bin/bash
# =============================================================================
# Wi-Fi / Hotspot manager — порт из Adeept_PiCar-Pro v2 (wifi_hotspot_manager.sh)
# Если известная Wi-Fi сеть недоступна, поднимается резервная точка доступа.
# Параметры задаются в config.py проекта и подставляются setup.py при установке.
# =============================================================================
HOTSPOT_SSID="__HOTSPOT_SSID__"
HOTSPOT_PASSWORD="__HOTSPOT_PASSWORD__"
HOTSPOT_IP="__HOTSPOT_IP__"
HOTSPOT_INTERFACE="wlan0"
# имя системного Wi-Fi подключения (Bookworm/Trixie) определяется автоматически
SYSTEM_WIFI_CONN=$(nmcli connection show | awk '$NF=="wifi" && $1!=HOTSPOT_SSID {print $1}' HOTSPOT_SSID="$HOTSPOT_SSID" | head -n1)

echo "Waiting for network service to start..."
while ! nmcli general status > /dev/null 2>&1; do
    sleep 1
done

check_wifi_connection() {
    nmcli connection show --active | grep -q "$SYSTEM_WIFI_CONN"
    return $?
}

get_preconfigured_ssid() {
    nmcli connection show "$SYSTEM_WIFI_CONN" 2>/dev/null | grep "\.ssid:" | awk '{print $2}'
}

connect_wifi() {
    local ssid=$(get_preconfigured_ssid)
    echo "Attempting to connect to system preconfigured WiFi: $ssid (connection name: $SYSTEM_WIFI_CONN)"

    if [ -z "$SYSTEM_WIFI_CONN" ] || ! nmcli connection show | grep -q "$SYSTEM_WIFI_CONN"; then
        echo "Error: System preconfigured connection does not exist"
        return 1
    fi

    nmcli connection up "$SYSTEM_WIFI_CONN" ifname "$HOTSPOT_INTERFACE"
    sleep 5

    if check_wifi_connection; then
        echo "System preconfigured WiFi connected successfully"
        return 0
    fi
    echo "Failed to connect to system preconfigured WiFi"
    return 1
}

start_hotspot() {
    echo "Starting hotspot: $HOTSPOT_SSID"
    if nmcli connection show | grep -q "$HOTSPOT_SSID"; then
        nmcli connection down "$HOTSPOT_SSID" > /dev/null 2>&1
    else
        nmcli connection add type wifi con-name "$HOTSPOT_SSID" ifname "$HOTSPOT_INTERFACE" ssid "$HOTSPOT_SSID"
        nmcli connection modify "$HOTSPOT_SSID" connection.autoconnect no
        nmcli connection modify "$HOTSPOT_SSID" wifi.mode ap
        nmcli connection modify "$HOTSPOT_SSID" wifi-sec.key-mgmt wpa-psk
        nmcli connection modify "$HOTSPOT_SSID" wifi-sec.psk "$HOTSPOT_PASSWORD"
        nmcli connection modify "$HOTSPOT_SSID" ipv4.addresses "$HOTSPOT_IP/24"
        nmcli connection modify "$HOTSPOT_SSID" ipv4.method shared
        nmcli connection modify "$HOTSPOT_SSID" ipv6.method ignore
    fi
    nmcli connection up "$HOTSPOT_SSID" ifname "$HOTSPOT_INTERFACE"
    echo "Hotspot started successfully"
}

main() {
    nmcli connection down id "$SYSTEM_WIFI_CONN" > /dev/null 2>&1
    nmcli connection down id "$HOTSPOT_SSID" > /dev/null 2>&1

    if connect_wifi; then
        exit 0
    fi
    start_hotspot
}

main
