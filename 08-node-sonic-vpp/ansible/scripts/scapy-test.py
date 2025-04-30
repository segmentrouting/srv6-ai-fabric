#! /usr/bin python3

# pip3 install scapy
# sudo python3 srv6-probe-fabric.py

# Run this script on host00, it will send SRv6/IPv6 encapsulated pings to host24
# Set log level to benefit from Scapy warnings
import logging
logging.getLogger("scapy").setLevel(0)

from scapy.all import *

p0 = Ether(src = "aa:c1:ab:4a:22:01", dst = "22:1f:1b:31:d4:83") \
/ IPv6(src = "2001:db8:1000::2", dst = "fc00:0:1000:1203:fe00::") \
/ IP(src = "200.0.100.2", dst = "200.24.100.2") / ICMP()
p0.show()
sendp(p0, iface="eth1", count=64)