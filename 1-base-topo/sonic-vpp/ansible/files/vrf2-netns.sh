#! /bin/bash

# Spin up an alpine container to connect to 10-leaf Ethernet8
docker run -itd --name netns10-Eth12 iejalapeno/alpine:latest /bin/sh 
PID10=$(sudo docker inspect -f '{{.State.Pid}}' netns10-Eth12)
sudo ln -s /proc/$PID10/ns/net /var/run/netns/$PID10
sudo ip link add 10netns type veth peer name 10leaf
sudo ip link set 10netns netns $PID10
sudo brctl addif br23 10leaf
sudo ip netns exec $PID10 ip addr add fc00:0:f800:7::2/64 dev 10netns
sudo ip netns exec $PID10 ip link set 10netns up
sudo ip link set 10leaf up
sudo ip netns exec $PID10 ip a
echo "Sleeping 3 seconds while the container is starting..."
sleep 3 
sudo ip netns exec $PID10 ping fc00:0:f800:7::1 -c 2
sudo ip netns exec $PID10 ip -6 route add fc00:0:f800::/48 via fc00:0:f800:7::1 dev 10netns
sudo ip netns exec $PID10 ip -6 route
sudo ip netns exec $PID10 ping fc00:0:f800:3::2 -c 2
