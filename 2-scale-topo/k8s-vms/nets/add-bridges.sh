#!/bin/bash

sudo brctl addbr cp-node00-0
sudo brctl addbr cp-node00-1
sudo brctl addbr cp-node00-2
sudo brctl addbr cp-node00-3
sudo brctl addbr wkr-node01-0
sudo brctl addbr wkr-node01-1
sudo brctl addbr wkr-node01-2
sudo brctl addbr wkr-node01-3
sudo brctl addbr wkr-node02-0
sudo brctl addbr wkr-node02-1
sudo brctl addbr wkr-node02-2
sudo brctl addbr wkr-node02-3
sudo brctl addbr wkr-node03-0
sudo brctl addbr wkr-node03-1
sudo brctl addbr wkr-node03-2
sudo brctl addbr wkr-node03-3

sudo ip link set dev cp-node00-0 up
sudo ip link set dev cp-node00-1 up
sudo ip link set dev cp-node00-2 up
sudo ip link set dev cp-node00-3 up
sudo ip link set dev wkr-node01-0 up
sudo ip link set dev wkr-node01-1 up
sudo ip link set dev wkr-node01-2 up
sudo ip link set dev wkr-node01-3 up
sudo ip link set dev wkr-node02-0 up
sudo ip link set dev wkr-node02-1 up
sudo ip link set dev wkr-node02-2 up
sudo ip link set dev wkr-node02-3 up
sudo ip link set dev wkr-node03-0 up
sudo ip link set dev wkr-node03-1 up
sudo ip link set dev wkr-node03-2 up
sudo ip link set dev wkr-node03-3 up
