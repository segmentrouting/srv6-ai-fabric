#! /bin/bash

sudo brctl addbr br-l00-s02
sudo brctl addbr br-l01-s02
sudo brctl addbr br-l00-s03
sudo brctl addbr br-l01-s03

sudo ip link set dev br-l00-s02 up
sudo ip link set dev br-l01-s02 up
sudo ip link set dev br-l00-s03 up
sudo ip link set dev br-l01-s03 up

