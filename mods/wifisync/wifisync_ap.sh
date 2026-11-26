#!/bin/sh
# WiFiSync 5 GHz AP control. RAM-only: every write stays in /tmp_bt/wifisync.
set -u
DIR=/tmp_bt/wifisync
CFG=$DIR/wifisync.cfg
IFACE=wlan0
LOG=$DIR/ap.log
STATE=$DIR/ap.state
IP=192.168.122.1
say() { echo "[wifisync] $*" >> "$LOG"; }
val() {
  key=$1
  while IFS='=' read -r k v; do
    [ "$k" = "$key" ] && { echo "$v"; return 0; }
  done < "$CFG"
  return 1
}
loaded() { grep -q "^$1 " /proc/modules 2>/dev/null; }
ins_if() {
  loaded "$1" && return 0
  insmod "$2" $3 2>>"$LOG"
}
kill_pidfile() {
  [ -f "$1" ] || return 0
  pid=$(cat "$1" 2>/dev/null)
  [ -n "$pid" ] && kill "$pid" 2>/dev/null
  sleep 1
  [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null
  rm -f "$1"
}
stop_user() {
  kill_pidfile "$DIR/dhcpd.pid"
  kill_pidfile "$DIR/wpa.pid"
  for p in /proc/[0-9]*; do
    [ -r "$p/comm" ] || continue
    read -r comm < "$p/comm" 2>/dev/null || continue
    case "$comm" in
      wpa_supplicant|dhcpd) kill "${p#/proc/}" 2>/dev/null ;;
    esac
  done
  ifconfig "$IFACE" down 2>/dev/null
}
down() {
  say down
  stop_user
  if [ -f "$DIR/owned" ]; then
    for m in bcmdhd mmcioWrapper mmc_core cfg80211 compat wlanIrq; do
      loaded "$m" && rmmod "$m" 2>>"$LOG" || true
    done
  fi
  rm -f "$DIR/owned"
  echo 'state=OFF' > "$STATE" 2>/dev/null
  return 0
}
up() {
  ssid=$(val ssid) || ssid=A6400-5G-SYNC
  psk=$(val psk) || psk=a6400-wifisync
  chan=$(val channel) || chan=36
  width=$(val width) || width=80
  nvram=$(val nvram) || nvram=high/bcm4339_H_14.txt
  country=$(val country) || country=
  allow_2g=$(val allow_2g) || allow_2g=0
  case "$chan" in 36|40|44|48) ;; *) say "bad_channel $chan"; return 1 ;; esac
  case "$width" in 20|40|80) ;; *) width=80 ;; esac
  if loaded bcmdhd && [ ! -f "$DIR/owned" ]; then
    say "busy Sony WiFi ON"
    return 3
  fi
  : > "$DIR/owned"
  if [ ! -d /sys/class/net/$IFACE ]; then
    ins_if wlanIrq /usr/kmod/wlanIrq.ko "" || return 1
    ins_if compat /usr/kmod/compat.ko "" || return 1
    ins_if cfg80211 /usr/kmod/cfg80211.ko "" || return 1
    ins_if mmc_core /usr/kmod/mmc_core.ko "" || return 1
    ins_if mmcioWrapper /usr/kmod/mmcioWrapper.ko "" || return 1
    ins_if bcmdhd /usr/kmod/bcmdhd.ko "firmware_path=/usr/wlan/bcm/bcm4339.bin nvram_path=/usr/share/network/nvram/$nvram op_mode=2" || return 1
    i=0
    while [ ! -d /sys/class/net/$IFACE ] && [ "$i" -lt 30 ]; do
      usleep 100000 2>/dev/null || sleep 1; i=$((i+1))
    done
    [ -d /sys/class/net/$IFACE ] || { say wlan0_missing; return 1; }
  fi
  stop_user
  ifconfig "$IFACE" "$IP" netmask 255.255.255.0 up
  printf '1048576\n' | tee /proc/sys/net/core/wmem_max >/dev/null 2>&1 || true
  printf '1048576\n' | tee /proc/sys/net/core/rmem_max >/dev/null 2>&1 || true
  printf '4096 131072 1048576\n' | tee /proc/sys/net/ipv4/tcp_wmem >/dev/null 2>&1 || true
  printf '4096 131072 1048576\n' | tee /proc/sys/net/ipv4/tcp_rmem >/dev/null 2>&1 || true
  wl -i "$IFACE" frameburst 1 >/dev/null 2>&1 || true
  [ -n "$country" ] && wl -i "$IFACE" country "$country" 2>>"$LOG"
  band=$(wl -i "$IFACE" bands 2>/dev/null)
  say "bands $band"
  case "$band" in
    *a*) ;;
    *) say 5g_unavailable
       [ "$allow_2g" = "1" ] || return 4 ;;
  esac
  spec="$chan/$width"
  wl -i "$IFACE" chanspec "$spec" 2>>"$LOG" || true
  if ! wl -i "$IFACE" chanspec 2>/dev/null | grep -q "$chan"; then
    for w in 40 20; do
      wl -i "$IFACE" chanspec "$chan/$w" 2>>"$LOG" || true
      wl -i "$IFACE" chanspec 2>/dev/null | grep -q "$chan" && { spec="$chan/$w"; break; }
    done
  fi
  say "chanspec $spec"
  {
    echo "ctrl_interface=$DIR/ctrl"
    echo 'ap_scan=2'
    echo 'max_num_sta=1'
    echo 'beacon_int=100'
    echo 'network={'
    echo "  ssid=\"$ssid\""
    echo '  mode=2'
    echo "  frequency=$((5180 + (chan - 36) * 5))"
    echo '  key_mgmt=WPA-PSK'
    echo '  proto=RSN'
    echo '  pairwise=CCMP'
    echo '  group=CCMP'
    echo "  psk=\"$psk\""
    echo '}'
  } > "$DIR/wpa.conf"
  chmod 600 "$DIR/wpa.conf"
  wpa_supplicant -B -Dnl80211 -i "$IFACE" -c "$DIR/wpa.conf" -P "$DIR/wpa.pid" -f "$DIR/wpa.log"
  i=0
  while [ "$i" -lt 25 ]; do
    wpa_cli -p "$DIR/ctrl" -i "$IFACE" status 2>/dev/null | grep -q 'wpa_state=COMPLETED' && break
    usleep 200000 2>/dev/null || sleep 1; i=$((i+1))
  done
  ifconfig "$IFACE" "$IP" netmask 255.255.255.0 up
  {
    echo 'default-lease-time 600;'
    echo 'max-lease-time 7200;'
    echo 'authoritative;'
    echo 'subnet 192.168.122.0 netmask 255.255.255.0 {'
    echo '  range 192.168.122.10 192.168.122.20;'
    echo '}'
  } > "$DIR/dhcpd.conf"
  : > "$DIR/dhcpd.leases"
  if [ -x /usr/sbin/dhcpd ]; then
    /usr/sbin/dhcpd -cf "$DIR/dhcpd.conf" -lf "$DIR/dhcpd.leases" -pf "$DIR/dhcpd.pid" "$IFACE" 2>>"$LOG" || say dhcpd_failed
  fi
  echo "state=ACTIVE chanspec=$spec ssid=$ssid ip=$IP" > "$STATE"
  say "up ok $spec"
  return 0
}
case "${1:-status}" in
  up) up ;;
  down) down ;;
  standby) wl -i "$IFACE" pm 2 2>>"$LOG"; echo 'state=STANDBY' > "$STATE" ;;
  active) wl -i "$IFACE" pm 0 2>>"$LOG"; echo 'state=ACTIVE' > "$STATE" ;;
  status) cat "$STATE" 2>/dev/null || echo 'state=OFF' ;;
  *) echo 'usage: ap.sh up|down|standby|active|status' >&2; exit 2 ;;
esac
exit $?
