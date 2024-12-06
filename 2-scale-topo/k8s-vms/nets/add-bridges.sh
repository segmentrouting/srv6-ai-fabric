#!/bin/bash

sudo brctl addbr k8s-cp-node00
sudo brctl addbr k8s-wkr-node01
sudo brctl addbr k8s-wkr-node02
sudo brctl addbr k8s-wkr-node03

sudo ip link set dev k8s-cp-node00 up
sudo ip link set dev k8s-wkr-node01 up
sudo ip link set dev k8s-wkr-node02 up
sudo ip link set dev k8s-wkr-node03 up
