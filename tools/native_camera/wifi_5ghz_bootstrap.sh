#!/bin/sh
# RAM-only 5 GHz BCM4339 Wi-Fi bootstrap for Sony ILCE-6400 (/tmp_bt only).
# Supports STA (router client) and AP (5 GHz SoftAP/GO at 192.168.122.1) modes
# without modifying /dev/nflasha15 or Backup.bin.
set -eu

MODE="sta"
SSID=""
PSK=""
CHANNEL_FREQ="5180"
NVRAM_PATH="/usr/share/network/nvram/high/bcm4339_H_14.txt"
FW_PATH="/usr/wlan/bcm/bcm4339.bin"
IFACE="wlan0"
AP_IP="192.168.122.1"
AP_NETMASK="255.255.0.0"

while [ $# -gt 0 ]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --ssid) SSID="$2"; shift 2 ;;
        --psk) PSK="$2"; shift 2 ;;
        --freq) CHANNEL_FREQ="$2"; shift 2 ;;
        --nvram) NVRAM_PATH="$2"; shift 2 ;;
        --iface) IFACE="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$SSID" ]; then
    echo "ERROR: --ssid is required" >&2
    exit 2
fi

if [ ! -f "$FW_PATH" ] || [ ! -f "$NVRAM_PATH" ]; then
    echo "ERROR: missing firmware ($FW_PATH) or nvram ($NVRAM_PATH)" >&2
    exit 3
fi

# Bring up BCM4339 SDIO + bcmdhd if not already loaded.
# Verified on ILCE-6400: WL_REG_ON is Port 9 Bit 18 (BKID 0xf905ed=9, 0xf905ee=0x12).
if ! lsmod | grep -q "^bcmdhd "; then
    gpioctrl.elf w GPIO_S_4 --gpio --dir o 1 || true
    gpioctrl.elf w 9 18 --gpio --dir o 1
    gpioctrl.elf iw GPIO_S_16 -n -l || true
    echo 1 > /proc/irq/144/smp_affinity || true
    sleep 1
    for mod in wlanIrq.ko compat.ko cfg80211.ko mmc_core.ko mmcioWrapper.ko; do
        lsmod | grep -q "^${mod%.ko} " || insmod "/usr/kmod/$mod"
    done
    insmod /usr/kmod/bcmdhd.ko firmware_path="$FW_PATH" nvram_path="$NVRAM_PATH" iface_name=wlan dhd_irq=144
    sleep 1
fi

# Tune Broadcom driver for high-throughput 5 GHz streaming.
if command -v wl >/dev/null 2>&1; then
    wl -i "$IFACE" country XR/88 2>/dev/null || true
    wl -i "$IFACE" ampdu 1 2>/dev/null || true
    wl -i "$IFACE" p2p_noa 0 0 0 0 2>/dev/null || true
    wl -i "$IFACE" PM 0 2>/dev/null || true
fi

busybox killall wpa_supplicant 2>/dev/null || true
mkdir -p /tmp_bt/wifi_run

if [ "$MODE" = "ap" ]; then
    cat > /tmp_bt/wifi_run/wpa_ap.conf <<EOF
ctrl_interface=/tmp_bt/wifi_run/ctrl
ap_scan=2
network={
    ssid="$SSID"
    mode=2
    frequency=$CHANNEL_FREQ
    key_mgmt=WPA-PSK
    proto=RSN
    pairwise=CCMP
    group=CCMP
    psk="$PSK"
}
EOF
    ifconfig "$IFACE" "$AP_IP" netmask "$AP_NETMASK" up
    wpa_supplicant -B -Dnl80211 -i "$IFACE" -c /tmp_bt/wifi_run/wpa_ap.conf
    if [ -x /usr/sbin/dhcpd ] && [ -f /usr/lib/netconf/dhcpd.conf ]; then
        busybox killall dhcpd 2>/dev/null || true
        touch /tmp_bt/wifi_run/dhcpd.leases
        /usr/sbin/dhcpd -cf /usr/lib/netconf/dhcpd.conf -lf /tmp_bt/wifi_run/dhcpd.leases -pf /tmp_bt/wifi_run/dhcpd.pid "$IFACE" || true
    fi
elif [ "$MODE" = "sta" ]; then
    busybox killall dhcpd 2>/dev/null || true
    cat > /tmp_bt/wifi_run/udhcpc.sh <<'EOF'
#!/bin/sh
[ "$1" = "bound" ] || [ "$1" = "renew" ] || exit 0
ifconfig "$interface" "$ip" netmask "${subnet:-255.255.255.0}" up
for g in $router; do
    route add default gw "$g" dev "$interface" 2>/dev/null || true
    break
done
EOF
    chmod 755 /tmp_bt/wifi_run/udhcpc.sh
    cat > /tmp_bt/wifi_run/wpa_sta.conf <<EOF
ctrl_interface=/tmp_bt/wifi_run/ctrl
network={
    ssid="$SSID"
    key_mgmt=WPA-PSK
    psk="$PSK"
    freq_list=$CHANNEL_FREQ
}
EOF
    ifconfig "$IFACE" up
    wpa_supplicant -B -Dnl80211 -i "$IFACE" -c /tmp_bt/wifi_run/wpa_sta.conf
    sleep 2
    busybox udhcpc -i "$IFACE" -n -q -t 8 -s /tmp_bt/wifi_run/udhcpc.sh || true
else
    echo "ERROR: unsupported mode $MODE (expected sta or ap)" >&2
    exit 2
fi

if command -v wl >/dev/null 2>&1; then
    wl -i "$IFACE" PM 0 2>/dev/null || true
    wl -i "$IFACE" ampdu 1 2>/dev/null || true
fi

ifconfig "$IFACE"
