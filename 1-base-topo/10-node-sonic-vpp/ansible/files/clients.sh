#! /bin/bash

docker exec -it clab-sonic-host01 ip addr add fc00:0:f800::2/64 dev eth1
docker exec -it clab-sonic-host01 ip addr add fc00:0:f800:1::2/64 dev eth2

docker exec -it clab-sonic-host02 ip addr add fc00:0:f800:2::2/64 dev eth1
docker exec -it clab-sonic-host02 ip addr add fc00:0:f800:3::2/64 dev eth2

docker exec -it clab-sonic-host03 ip addr add fc00:0:f800:4::2/64 dev eth1
docker exec -it clab-sonic-host03 ip addr add fc00:0:f800:5::2/64 dev eth2

docker exec -it clab-sonic-host04 ip addr add fc00:0:f800:6::2/64 dev eth1
docker exec -it clab-sonic-host04 ip addr add fc00:0:f800:7::2/64 dev eth2


# routes

docker exec -it clab-sonic-host01 ip route add fc00:0::/32 via fc00:0:f800:0::1
docker exec -it clab-sonic-host01 ip route add fc00:0:f800:2::1/64 via fc00:0:f800:1::1
#docker exec -it clab-sonic-host01 ip route add fc00:0:f800:3::1/64 via fc00:0:f800:2::1
docker exec -it clab-sonic-host01 ip route add fc00:0:f800:4::1/64 via fc00:0:f800:3::1
docker exec -it clab-sonic-host01 ip route add fc00:0:f800:5::1/64 via fc00:0:f800:4::1
