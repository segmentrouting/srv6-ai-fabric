#!/bin/bash

virsh net-define cp-node00-0.xml
virsh net-define cp-node00-1.xml
virsh net-define cp-node00-2.xml
virsh net-define cp-node00-3.xml
virsh net-define wkr-node01-0.xml
virsh net-define wkr-node01-1.xml
virsh net-define wkr-node01-2.xml
virsh net-define wkr-node01-3.xml
virsh net-define wkr-node02-0.xml
virsh net-define wkr-node02-1.xml
virsh net-define wkr-node02-2.xml
virsh net-define wkr-node02-3.xml
virsh net-define wkr-node03-0.xml
virsh net-define wkr-node03-1.xml
virsh net-define wkr-node03-2.xml
virsh net-define wkr-node03-3.xml

virsh net-start cp-node00-0
virsh net-start cp-node00-1
virsh net-start cp-node00-2
virsh net-start cp-node00-3
virsh net-start wkr-node01-0
virsh net-start wkr-node01-1
virsh net-start wkr-node01-2
virsh net-start wkr-node01-3
virsh net-start wkr-node02-0
virsh net-start wkr-node02-1
virsh net-start wkr-node02-2
virsh net-start wkr-node02-3
virsh net-start wkr-node03-0
virsh net-start wkr-node03-1
virsh net-start wkr-node03-2
virsh net-start wkr-node03-3

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
