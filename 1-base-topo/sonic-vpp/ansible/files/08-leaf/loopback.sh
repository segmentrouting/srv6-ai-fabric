#!/bin/bash

sudo config interface ip add Loopback0 10.0.0.8/32
sudo config interface ip add Loopback0 fc00:0:8::1/128
sudo ip link add sr0 type dummy
sudo ip link set sr0 up 
sudo config save -y 