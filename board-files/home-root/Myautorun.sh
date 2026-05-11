#!/bin/sh

ifconfig eth0 192.168.137.150 netmask 255.255.255.0
route add default gw 192.168.137.1
cd /home/root/PSRT_app/backend
nohup /home/root/PSRT_app/.venv/bin/python app.py > /home/root/app.log 2>&1 &
