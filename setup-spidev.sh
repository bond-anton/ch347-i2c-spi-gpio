#!/bin/bash
# Load the CH347 driver and expose /dev/spidev0.0.
# Run after boot, or install as a systemd unit for automatic setup.
set -euo pipefail

modprobe ch347-buses

# Wait for the controller to register.
for _ in $(seq 1 20); do
	[ -d /sys/class/spi_master/spi0 ] && break
	sleep 0.1
done

if [ ! -d /sys/class/spi_master/spi0 ]; then
	echo "spi0 did not appear" >&2
	exit 1
fi

# Create the device if it is not already there.
if [ ! -d /sys/bus/spi/devices/spi0.0 ]; then
	echo "spidev 0" > /sys/class/spi_master/spi0/new_device
fi

# spidev's probe rejects the device without driver_override.
echo spidev > /sys/bus/spi/devices/spi0.0/driver_override
if [ ! -e /sys/bus/spi/devices/spi0.0/driver ]; then
	echo spi0.0 > /sys/bus/spi/drivers/spidev/bind
fi

ls -l /dev/spidev0.0
