#! /bin/bash

# Create namespace and veth pair for 09-leaf Ethernet8 "client" connection
sudo ip netns add 09-host
sudo ip link add veth09 type veth peer name veth20

# Move veth09 to namespace 09-host
sudo ip link set veth09 netns 09-host

# Connect the other side veth20 to bridge br20, which is connected to 09-leaf Ethernet8
sudo brctl addif br20 veth20

# Bring up the interfaces
sudo ip netns exec 09-host ip link set veth09 up
sudo ip link set veth20 up

# Spin up an alpine container to connet to 09-leaf Ethernet8
docker run -i -t --name 09-leaf-08 iejalapeno/alpine:latest /bin/sh 
PID1=$(sudo docker inspect -f '{{.State.Pid}}' 09-leaf-08)
sudo ln -s /proc/$PID1/ns/net /var/run/netns/$PID1
sudo ip link add 09h08 type veth peer name 09l08
sudo ip link set 09h08 netns $PID1
sudo brctl addif br20 09l08
sudo ip netns exec $PID1 ip addr add fc00:0:f800:4::2/64 dev 09h08
sudo ip netns exec $PID1 ip link set 09h08 up
sudo ip link set 09l08 up
sudo ip netns exec $PID1 ping fc00:0:f800:4::1 -c 2
sudo ip netns exec $PID1 ip -6 route add fc00:0:f800::/48 via fc00:0:f800:4::1 dev 09h08
sudo ip netns exec $PID1 ip -6 route
