#!/bin/bash
# =============================================================================
# PiCarPro — настройка файла подкачки под YOLO + OpenCV на RPi 3B+ (1 ГБ ОЗУ)
#
# Что делает:
#   1) dphys-swapfile: swap 2048 МБ на SD-карте (по умолчанию 100 МБ —
#      катастрофически мало для cv2.dnn + picamera2 + Flask + websockets);
#   2) sysctl: агрессивная выгрузка страниц (vm.swappiness=80) и берережливый
#      кэш inode/dentry (vm.vfs_cache_pressure=50);
#   3) опционально zram (сжатый swap в RAM, zstd): быстрее SD и не убивает
#      карту, при флаге --zram;
#   4) печатает отчёт free -h / swapon --show.
#
# Запуск: sudo bash tools/setup_swap.sh [--zram] [--size 2048]
# После настройки перезагрузка не обязательна, но рекомендована.
# =============================================================================
set -euo pipefail

SIZE=2048
USE_ZRAM=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --zram)  USE_ZRAM=1; shift ;;
        --size)  SIZE="$2"; shift 2 ;;
        *) echo "Неизвестный флаг: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Запустите с sudo: sudo bash $0"
    exit 1
fi

echo "==> [1/4] dphys-swapfile: CONF_SWAPSIZE=${SIZE} МБ"
SWAP_CONF=/etc/dphys-swapfile
if [[ -f $SWAP_CONF ]]; then
    sed -i "s/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=${SIZE}/" "$SWAP_CONF" \
        || echo "CONF_SWAPSIZE=${SIZE}" >> "$SWAP_CONF"
    sed -i "s/^#*CONF_SWAPFACTOR=.*/CONF_SWAPFACTOR=2/" "$SWAP_CONF" || true
else
    echo "CONF_SWAPSIZE=${SIZE}" > "$SWAP_CONF"
    echo "CONF_SWAPFACTOR=2" >> "$SWAP_CONF"
fi
systemctl restart dphys-swapfile 2>/dev/null || service dphys-swapfile restart

echo "==> [2/4] sysctl: swappiness=80, vfs_cache_pressure=50"
cat > /etc/sysctl.d/99-picarpro-swap.conf <<EOF
# PiCarPro: swap-настройки под YOLO/OpenCV на 1 ГБ ОЗУ
vm.swappiness=80
vm.vfs_cache_pressure=50
vm.overcommit_memory=1
EOF
sysctl --system >/dev/null

echo "==> [3/4] zram (сжатый swap в RAM, zstd)"
if [[ $USE_ZRAM -eq 1 ]]; then
    apt-get install -y zram-tools
    cat > /etc/default/zramswap <<'EOF'
ALGO=zstd
PERCENT=50
PRIORITY=100
EOF
    systemctl restart zramswap
    echo "    zram включён (PERCENT=50 => ~512 МБ сжатого swap в RAM)"
else
    echo "    пропущено (добавьте флаг --zram для включения)"
fi

echo "==> [4/4] Текущее состояние:"
free -h
echo
swapon --show
echo
echo "Готово. Замечания:"
echo " * Swap на SD-карте изнашивает карту при активном использовании;"
echo "   рекомендуем включить --zram и не держать YOLO-режим включённым постоянно."
echo " * gpu_mem можно снизить: /boot/config.txt -> gpu_mem=128 (камере хватает)."
echo " * Проверка под нагрузкой: python3 boot.py, затем в web-UI включите режим"
echo "   'YOLO' и следите за 'ram' и 'cpu_temp' в статусе."
