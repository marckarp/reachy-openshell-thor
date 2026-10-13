#!/bin/sh
# Reset the Thor's internal USB 2.0 hub (1-4) to clear a stuck port. Run with sudo.
echo 0 > /sys/bus/usb/devices/1-4/authorized
sleep 2
echo 1 > /sys/bus/usb/devices/1-4/authorized
