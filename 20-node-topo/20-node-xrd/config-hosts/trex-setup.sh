#!/bin/sh

# IP addresses and routes

## ubuntu trex 01
#docker exec -it clab-trex-ubtrex01 sysctl -p
docker exec -it clab-trex-ubtrex01 systemctl start trex
docker exec -it clab-trex-ubtrex01 ip addr add fc00:0:1000:1::/64 dev eth1
docker exec -it clab-trex-ubtrex01 ip -6 route add fc00:0000::/32 via fc00:0:1000:1::1 dev eth1

## ubuntu trex 02
#docker exec -it clab-trex-ubtrex02 sysctl -p
docker exec -it clab-trex-ubtrex02 systemctl start trex
docker exec -it clab-trex-ubtrex02 ip addr add fc00:0:101:2::102/64 dev eth1
docker exec -it clab-trex-ubtrex02 ip -6 route add fc00:0000::/32 via fc00:0:101:2::1 dev eth1
