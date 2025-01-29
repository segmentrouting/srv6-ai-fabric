#!/bin/sh

# IP addresses and routes

## host00
#docker exec -it clab-trex-ubtrex01 sysctl -p
ip addr add 10.10.0.2/24 dev eth1
ip addr add fc00:0:f800::2/64 dev eth1
ip -6 route add fc00:0:fe00::/48 via fc00:0:f800::1 dev eth1
ip route add 10.10.16.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth1
ip -6 route add fc00:0:f800:8000::/64 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth1

ip addr add 10.10.4.2/24 dev eth2
ip addr add fc00:0:f800:4::2/64 dev eth2
ip -6 route add fc00:0:fe01::/48 via fc00:0:f800:4::1 dev eth2
ip route add 10.10.20.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth2
ip -6 route add fc00:0:f800:8004::/64 encap seg6 mode encap segs fc00:0:fe01:fe01:fe05:fe05:: dev eth2
docker exec -it clab-trex-ubtrex01 systemctl start trex


## host01
ip addr add 10.10.4.2/24 dev eth1
ip -6 route add fc00:0:fe00::/48 via fc00:0:f800::1 dev eth1
ip route add 10.10.20.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth1

docker exec -it clab-trex-ubtrex01 systemctl start trex


## host02
ip addr add 10.10.16.2/24 dev eth1
ip addr add fc00:0:f800:8000::2/64 dev eth1
ip -6 route add fc00:0:fe00::/48 via fc00:0:f800:8000::1 dev eth1
ip route add 10.10.0.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe00:fe00:: dev eth1
ip -6 route add fc00:0:f800::/64 encap seg6 mode encap segs fc00:0:fe00:fe00:fe00:fe00:: dev eth1

ip addr add 10.10.20.2/24 dev eth2
ip addr add fc00:0:f800:8004::2/64 dev eth2
ip -6 route add fc00:0:fe01::/48 via fc00:0:f800:8004::1 dev eth2
ip route add 10.10.4.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe00:fe00:: dev eth2
ip -6 route add fc00:0:f800:4::/64 encap seg6 mode encap segs fc00:0:fe01:fe01:fe01:fe01:: dev eth2

docker exec -it clab-trex-ubtrex01 systemctl start trex







ip addr del fc00:0:f800::2/64 dev eth1
ip -6 route del fc00:0:fe00::/48 via fc00:0:f800::1 dev eth1
ip route del 10.10.16.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth1
ip -6 route del fc00:0:f800:8000::/64 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth1

ip addr del 10.10.4.2/24 dev eth2
ip addr del fc00:0:f800:4::2/64 dev eth2
ip -6 route del fc00:0:fe01::/48 via fc00:0:f800:4::1 dev eth2
ip route del 10.10.20.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth2
ip -6 route del fc00:0:f800:8004::/64 encap seg6 mode encap segs fc00:0:fe01:fe01:fe05:fe05:: dev eth2
