#!/bin/bash

sudo config interface ip add Loopback0 10.0.0.4/32
sudo config interface ip add Loopback0 fc00:0:4::1/128
sudo ip link add sr0 type dummy
sudo ip link set sr0 up 
sudo sysctl -w net.vrf.strict_mode=1
sudo sysctl -w net.ipv6.seg6_flowlabel=1
sudo sysctl -w net.ipv6.fib_multipath_hash_policy=3
sudo sysctl -w net.ipv6.fib_multipath_hash_fields=11

