#!/bin/sh

sudo ip link set l00-bridge down
sudo ip link set l01-bridge down
sudo ip link set l02-bridge down
sudo ip link set l03-bridge down
sudo brctl delbr l00-bridge
sudo brctl delbr l01-bridge
sudo brctl delbr l02-bridge
sudo brctl delbr l03-bridge