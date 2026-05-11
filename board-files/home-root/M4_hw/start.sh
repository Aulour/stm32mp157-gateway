#!/bin/sh
echo RPMsg_TEST_CM4.elf >/sys/class/remoteproc/remoteproc0/firmware
echo start >/sys/class/remoteproc/remoteproc0/state

