#!/bin/sh

# IP addresses and routes

## host00
docker exec -it clab-clos-host00 ip addr add 10.10.0.2/24 dev eth1
docker exec -it clab-clos-host00 ip addr add fc00:0:f800::2/64 dev eth1
docker exec -it clab-clos-host00 ip -6 route add fc00:0:fe00::/48 via fc00:0:f800::1 dev eth1
docker exec -it clab-clos-host00 ip -6 route add fc00:0:f800:8000::/64 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe00:: dev eth1

docker exec -it clab-clos-host00 ip addr add 10.10.4.2/24 dev eth2
docker exec -it clab-clos-host00 ip addr add fc00:0:f800:4::2/64 dev eth2
docker exec -it clab-clos-host00 ip -6 route add fc00:0:fe01::/48 via fc00:0:f800:4::1 dev eth2
docker exec -it clab-clos-host00 ip -6 route add fc00:0:f800:8004::/64 encap seg6 mode encap segs fc00:0:fe01:fe01:fe05:fe01:: dev eth2

docker exec -it clab-clos-host00 ip addr add 10.10.8.2/24 dev eth3
docker exec -it clab-clos-host00 ip addr add fc00:0:f800:8::2/64 dev eth3
docker exec -it clab-clos-host00 ip -6 route add fc00:0:fe02::/48 via fc00:0:f800:8::1 dev eth3
docker exec -it clab-clos-host00 ip -6 route add fc00:0:f800:8008::/64 encap seg6 mode encap segs fc00:0:fe02:fe02:fe06:fe02:: dev eth3

docker exec -it clab-clos-host00 ip addr add 10.10.12.2/24 dev eth4
docker exec -it clab-clos-host00 ip addr add fc00:0:f800:c::2/64 dev eth4
docker exec -it clab-clos-host00 ip -6 route add fc00:0:fe03::/48 via fc00:0:f800:c::1 dev eth4
docker exec -it clab-clos-host00 ip -6 route add fc00:0:f800:800c::/64 encap seg6 mode encap segs fc00:0:fe03:fe03:fe07:fe03:: dev eth4


## host01
docker exec -it clab-clos-host01 ip addr add 10.10.1.2/24 dev eth1
docker exec -it clab-clos-host01 ip addr add fc00:0:f800:1::2/64 dev eth1
docker exec -it clab-clos-host01 ip -6 route add fc00:0:fe01::/48 via fc00:0:f800:1::1 dev eth1
docker exec -it clab-clos-host01 ip -6 route add fc00:0:f800:8001::/64 encap seg6 mode encap segs fc00:0:fe01:fe00:fe05:fe00:: dev eth1

docker exec -it clab-clos-host01 ip addr add 10.10.5.2/24 dev eth2
docker exec -it clab-clos-host01 ip addr add fc00:0:f800:5::2/64 dev eth2
docker exec -it clab-clos-host01 ip -6 route add fc00:0:fe02::/48 via fc00:0:f800:5::1 dev eth2
docker exec -it clab-clos-host01 ip -6 route add fc00:0:f800:8005::/64 encap seg6 mode encap segs fc00:0:fe02:fe01:fe06:fe01:: dev eth2

docker exec -it clab-clos-host01 ip addr add 10.10.9.2/24 dev eth3
docker exec -it clab-clos-host01 ip addr add fc00:0:f800:9::2/64 dev eth3
docker exec -it clab-clos-host01 ip -6 route add fc00:0:fe03::/48 via fc00:0:f800:9::1 dev eth3
docker exec -it clab-clos-host01 ip -6 route add fc00:0:f800:8009::/64 encap seg6 mode encap segs fc00:0:fe03:fe02:fe07:fe02:: dev eth3

docker exec -it clab-clos-host01 ip addr add 10.10.13.2/24 dev eth4
docker exec -it clab-clos-host01 ip addr add fc00:0:f800:d::2/64 dev eth4
docker exec -it clab-clos-host01 ip -6 route add fc00:0:fe00::/48 via fc00:0:f800:d::1 dev eth4
docker exec -it clab-clos-host01 ip -6 route add fc00:0:f800:800d::/64 encap seg6 mode encap segs fc00:0:fe00:fe03:fe04:fe03:: dev eth4


## host02
docker exec -it clab-clos-host02 ip addr add 10.10.16.2/24 dev eth1
docker exec -it clab-clos-host02 ip addr add fc00:0:f800:8000::2/64 dev eth1
docker exec -it clab-clos-host02 ip -6 route add fc00:0:fe00::/48 via fc00:0:f800:8000::1 dev eth1
docker exec -it clab-clos-host02 ip -6 route add fc00:0:f800::/64 encap seg6 mode encap segs fc00:0:fe00:fe00:fe00:fe00:: dev eth1

docker exec -it clab-clos-host02 ip addr add 10.10.20.2/24 dev eth2
docker exec -it clab-clos-host02 ip addr add fc00:0:f800:8004::2/64 dev eth2
docker exec -it clab-clos-host02 ip -6 route add fc00:0:fe01::/48 via fc00:0:f800:8004::1 dev eth2
docker exec -it clab-clos-host02 ip -6 route add fc00:0:f800:4::/64 encap seg6 mode encap segs fc00:0:fe01:fe01:fe01:fe01:: dev eth2

docker exec -it clab-clos-host02 ip addr add 10.10.24.2/24 dev eth3
docker exec -it clab-clos-host02 ip addr add fc00:0:f800:8008::2/64 dev eth3
docker exec -it clab-clos-host02 ip -6 route add fc00:0:fe02::/48 via fc00:0:f800:8008::1 dev eth3
docker exec -it clab-clos-host02 ip -6 route add fc00:0:f800:8008::/64 encap seg6 mode encap segs fc00:0:fe02:fe02:fe02:fe02:: dev eth3

docker exec -it clab-clos-host02 ip addr add 10.10.28.2/24 dev eth4
docker exec -it clab-clos-host02 ip addr add fc00:0:f800:e::2/64 dev eth4
docker exec -it clab-clos-host02 ip -6 route add fc00:0:fe03::/48 via fc00:0:f800:800c::1 dev eth4
docker exec -it clab-clos-host02 ip -6 route add fc00:0:f800:c::/64 encap seg6 mode encap segs fc00:0:fe03:fe03:fe03:fe03:: dev eth4

## host03
docker exec -it clab-clos-host03 ip addr add 10.10.17.2/24 dev eth1
docker exec -it clab-clos-host03 ip addr add fc00:0:f800:8001::2/64 dev eth1
docker exec -it clab-clos-host03 ip -6 route add fc00:0:fe01::/48 via fc00:0:f800:8001::1 dev eth1
docker exec -it clab-clos-host03 ip -6 route add fc00:0:f800:1::/64 encap seg6 mode encap segs fc00:0:fe01:fe00:fe01:fe00:: dev eth1

docker exec -it clab-clos-host03 ip addr add 10.10.21.2/24 dev eth2
docker exec -it clab-clos-host03 ip addr add fc00:0:f800:8005::2/64 dev eth2
docker exec -it clab-clos-host03 ip -6 route add fc00:0:fe02::/48 via fc00:0:f800:8005::1 dev eth2
docker exec -it clab-clos-host03 ip -6 route add fc00:0:f800:5::/64 encap seg6 mode encap segs fc00:0:fe02:fe01:fe02:fe01:: dev eth2

docker exec -it clab-clos-host03 ip addr add 10.10.25.2/24 dev eth3
docker exec -it clab-clos-host03 ip addr add fc00:0:f800:8009::2/64 dev eth3
docker exec -it clab-clos-host03 ip -6 route add fc00:0:fe03::/48 via fc00:0:f800:8009::1 dev eth3
docker exec -it clab-clos-host03 ip -6 route add fc00:0:f800:9::/64 encap seg6 mode encap segs fc00:0:fe03:fe02:fe03:fe02:: dev eth3

docker exec -it clab-clos-host03 ip addr add 10.10.29.2/24 dev eth4
docker exec -it clab-clos-host03 ip addr add fc00:0:f800:800d::2/64 dev eth4
docker exec -it clab-clos-host03 ip -6 route add fc00:0:fe00::/48 via fc00:0:f800:800d::1 dev eth4
docker exec -it clab-clos-host03 ip -6 route add fc00:0:f800:d::/64 encap seg6 mode encap segs fc00:0:fe00:fe03:fe00:fe03:: dev eth4

### Deletes

# ip addr del fc00:0:f800::2/64 dev eth1
# ip -6 route del fc00:0:fe00::/48 via fc00:0:f800::1 dev eth1
# ip route del 10.10.16.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth1
# ip -6 route del fc00:0:f800:8000::/64 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth1

# ip addr del 10.10.4.2/24 dev eth2
# ip addr del fc00:0:f800:4::2/64 dev eth2
# ip -6 route del fc00:0:fe01::/48 via fc00:0:f800:4::1 dev eth2
# ip route del 10.10.20.0/24 encap seg6 mode encap segs fc00:0:fe00:fe00:fe04:fe04:: dev eth2
# ip -6 route del fc00:0:f800:8004::/64 encap seg6 mode encap segs fc00:0:fe01:fe01:fe05:fe05:: dev eth2
