#!/bin/sh
# www.openedv.com
# author: liangwencong@alientek.com
# date: 2021.12.3
# brief: Initialize M4 for STM32MP157
# version v1.0

rproc_class_dir="/sys/class/remoteproc/remoteproc0"
fmw_dir="/lib/firmware"

cd /sys/class/remoteproc/remoteproc0

if [ $1 == "start" ]
then
	/bin/echo -n $2 > $rproc_class_dir/firmware
	/bin/echo -n start > $rproc_class_dir/state
fi

if [ $1 == "stop" ]
then
	/bin/echo -n stop > $rproc_class_dir/state
fi
