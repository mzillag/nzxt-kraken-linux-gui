#!/bin/sh
# Installs the udev rule that grants the current user access to the Kraken.
# Run with: sudo sh packaging/install-udev.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
install -m 0644 "$HERE/71-kraken-control.rules" /etc/udev/rules.d/71-kraken-control.rules
# An empty liquidctl rule file from an earlier attempt is harmless but useless; replace it.
if [ -f /etc/udev/rules.d/71-liquidctl.rules ] && [ ! -s /etc/udev/rules.d/71-liquidctl.rules ]; then
    rm -f /etc/udev/rules.d/71-liquidctl.rules
fi
udevadm control --reload-rules
# Re-trigger the Kraken so the already-created device nodes get the new permissions
for dev in $(ls /sys/bus/usb/devices/); do
    if [ "$(cat /sys/bus/usb/devices/$dev/idVendor 2>/dev/null)" = "1e71" ]; then
        udevadm trigger --action=add "/sys/bus/usb/devices/$dev"
        for child in /sys/bus/usb/devices/$dev/*:1.*; do
            udevadm trigger --action=add --subsystem-match=hidraw "$child" 2>/dev/null || true
        done
    fi
done
udevadm trigger --subsystem-match=hidraw --action=add
udevadm settle
echo "udev rule installed. Device nodes:"
ls -l /dev/hidraw* 2>/dev/null | grep -v "crw-------" || true
for dev in /sys/bus/usb/devices/*; do
    if [ "$(cat $dev/idVendor 2>/dev/null)" = "1e71" ]; then
        printf "/dev/bus/usb/%03d/%03d: " "$(cat $dev/busnum)" "$(cat $dev/devnum)"
        ls -l "/dev/bus/usb/$(printf %03d $(cat $dev/busnum))/$(printf %03d $(cat $dev/devnum))"
    fi
done
