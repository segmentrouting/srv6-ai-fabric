#!/bin/sh

# IP addresses and routes

## host00
docker exec -it clab-sonic-host00 ip addr add 200.0.100.2/24 dev eth1
docker exec -it clab-sonic-host00 ip addr add 2001:db8:1000:0::2/64 dev eth1
docker exec -it clab-sonic-host00 ip -6 route add fc00:0::/32 via 2001:db8:1000:0::1 dev eth1
docker exec -it clab-sonic-host00 ip -6 route add 2001:db8:1024::/64 encap seg6 mode encap segs fc00:0:1000:1203:: dev eth1

## host08
docker exec -it clab-sonic-host08 ip addr add 200.8.100.2/24 dev eth1
docker exec -it clab-sonic-host08 ip addr add 2001:db8:1008:0::2/64 dev eth1
docker exec -it clab-sonic-host08 ip -6 route add fc00:0::/32 via 2001:db8:1008:0::1 dev eth1
docker exec -it clab-sonic-host08 ip -6 route add 2001:db8:1024::/64 encap seg6 mode encap segs fc00:0:1000:1203:: dev eth1

## host02
