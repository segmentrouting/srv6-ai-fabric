#! /bin/bash

sudo brctl addbr l00-bridge
sudo brctl addbr l01-bridge
sudo brctl addbr l02-bridge
sudo brctl addbr l03-bridge

sudo ip link set dev l00-bridge up
sudo ip link set dev l01-bridge up
sudo ip link set dev l02-bridge up
sudo ip link set dev l03-bridge up

