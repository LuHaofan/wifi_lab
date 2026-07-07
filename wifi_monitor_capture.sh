#!/bin/bash
#
# wifi_monitor_capture.sh
#
# One-shot script to put a WiFi NIC into monitor mode, launch Wireshark
# on it, and restore the interface to managed mode afterward.
#
# Usage:
#   sudo ./wifi_monitor_capture.sh <interface> [channel]
#
# Examples:
#   sudo ./wifi_monitor_capture.sh wlan0
#   sudo ./wifi_monitor_capture.sh wlan0 6
#
# Requires: aircrack-ng (airmon-ng), iw, wireshark
#
# Only use this on networks/spectrum you own or are authorized to monitor.

set -euo pipefail

IFACE="${1:-}"
CHANNEL="${2:-}"

if [[ -z "$IFACE" ]]; then
    echo "Usage: sudo $0 <interface> [channel]"
    echo
    echo "Available wireless interfaces:"
    iw dev | awk '$1=="Interface"{print "  - "$2}'
    exit 1
fi

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (use sudo)."
    exit 1
fi

for cmd in airmon-ng iw wireshark; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: '$cmd' not found. Install it first (e.g. sudo apt install aircrack-ng wireshark)."
        exit 1
    fi
done

if ! iw dev "$IFACE" info &>/dev/null; then
    echo "Error: interface '$IFACE' not found."
    echo "Available wireless interfaces:"
    iw dev | awk '$1=="Interface"{print "  - "$2}'
    exit 1
fi

MON_IFACE=""

cleanup() {
    echo
    echo "[*] Cleaning up..."
    if [[ -n "$MON_IFACE" ]]; then
        echo "[*] Stopping monitor mode on $MON_IFACE"
        airmon-ng stop "$MON_IFACE" >/dev/null 2>&1 || true
    fi
    echo "[*] Restarting NetworkManager (if present)"
    systemctl restart NetworkManager >/dev/null 2>&1 || true
    echo "[*] Done."
}
trap cleanup EXIT INT TERM

echo "[*] Killing processes that may interfere (NetworkManager, wpa_supplicant, etc.)"
airmon-ng check kill

echo "[*] Enabling monitor mode on $IFACE"
airmon-ng start "$IFACE" $CHANNEL

# Figure out the resulting monitor interface name.
# airmon-ng usually renames wlan0 -> wlan0mon, but on some setups it
# keeps the same name and just changes the mode. Detect which happened.
if iw dev "${IFACE}mon" info &>/dev/null; then
    MON_IFACE="${IFACE}mon"
elif iw dev "$IFACE" info 2>/dev/null | grep -q "type monitor"; then
    MON_IFACE="$IFACE"
else
    echo "Error: could not determine monitor interface name."
    exit 1
fi

echo "[*] Monitor interface active: $MON_IFACE"

if [[ -n "$CHANNEL" ]]; then
    echo "[*] Setting channel $CHANNEL"
    iw dev "$MON_IFACE" set channel "$CHANNEL" || true
fi

echo "[*] Launching Wireshark on $MON_IFACE (close Wireshark to end capture and restore interface)"
wireshark -i "$MON_IFACE" -k

# cleanup() runs automatically via trap on exit
