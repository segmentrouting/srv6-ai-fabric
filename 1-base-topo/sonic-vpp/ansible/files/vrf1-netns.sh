#! /bin/bash

# Spin up an alpine container to connect to 09-leaf Ethernet8
docker run -itd --name netns09-Eth08 iejalapeno/alpine:latest /bin/sh 
PID09=$(sudo docker inspect -f '{{.State.Pid}}' netns09-Eth08)
sudo ln -s /proc/$PID09/ns/net /var/run/netns/$PID09
sudo ip link add 09netns type veth peer name 09leaf
sudo ip link set 09netns netns $PID09
sudo brctl addif br20 09leaf
sudo ip netns exec $PID09 ip addr add fc00:0:f800:4::2/64 dev 09netns
sudo ip netns exec $PID09 ip link set 09netns up
sudo ip link set 09leaf up
sudo ip netns exec $PID09 ip a
echo "Waiting 3 seconds for the container to be ready..."
wait 3 
sudo ip netns exec $PID09 ping fc00:0:f800:4::1 -c 2
sudo ip netns exec $PID09 ip -6 route add fc00:0:f800::/48 via fc00:0:f800:4::1 dev 09netns
sudo ip netns exec $PID09 ip -6 route
sudo ip netns exec $PID09 ping fc00:0:f800::2 -c 2
