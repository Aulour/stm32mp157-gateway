#!/bin/sh -
# www.openedv.com
# author: dengzhimao@alientek.com
# date: 2021.7.14
# brief: Initialize Bluetooth for STM32MP157
# version v1.0

result=`ps -e|grep 'rtk_hciattach'|sed -e "/grep/d"`
if [ ! -z "$result" ];then
   killall rtk_hciattach
fi
if [ ! -d "/sys/class/gpio/gpio90/" ]; then
echo 90 > /sys/class/gpio/export
fi
echo out > /sys/class/gpio/gpio90/direction
echo 0 > /sys/class/gpio/gpio90/value
sleep 1
echo 1 > /sys/class/gpio/gpio90/value
rtk_hciattach -n -s 115200 /dev/ttySTM3 rtk_h5 > /tmp/.bluetooth.log 2>&1 &
sleep 4
result=$(cat /tmp/.bluetooth.log | grep "finished")
if test "$result" != ""
then
    echo "bluetooth init finished!"
    systemctl start bluetooth.service
else
    echo "bluetooth init error!"
fi
