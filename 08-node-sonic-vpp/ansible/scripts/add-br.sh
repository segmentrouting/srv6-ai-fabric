#! /bin/bash

sudo brctl addbr l00-bridge
sudo brctl addbr l01-bridge
sudo brctl addbr l02-bridge
sudo brctl addbr l03-bridge

sudo ip link set dev l00-bridge up
sudo ip link set dev l01-bridge up
sudo ip link set dev l02-bridge up
sudo ip link set dev l03-bridge up

sudo ip addr add 2001:db8:1000:2::2/64 dev l00-bridge
sudo ip addr add 2001:db8:1008:2::2/64 dev l01-bridge
sudo ip addr add 2001:db8:1016:2::2/64 dev l02-bridge
sudo ip addr add 2001:db8:1024:2::2/64 dev l03-bridge

