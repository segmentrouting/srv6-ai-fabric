## Quickstart 4-Plane Containerlab Topology

The 4-plane topology deploys 96 dockerized sonic-vs routers and 32 light Alpine containers simulating hosts attached to the network. The Alpine containers are divided into tenants *`green`* and *`yellow`*, with one *`green`* and one *`yellow`* attached to each leaf.

Example: *`green-host00`* has 4 uplinks, one to *`leaf00`* in each of the 4 planes.

The **docker-sonic-vs** is pretty lightweight and takes up only 160MB of memory. That said, the lab has been tested on Ubuntu 22.04 and 24.04 virtual machines with 32 vCPU and 96GB of memory, which appears to be more than sufficient.

1. Download the **docker-sonic-vs** image found in the [Oracle/Cisco fileshare](https://cisco.sharepoint.com/:u:/r/sites/OracleCiscoCollaboration/Shared%20Documents/AI-ML%20POC/AI%20Cluster%20Networking%20Project/Software%20(SONiC,%20SOLAR,%20binaries)/SRv6/docker-sonic-vs-grt.gz?csf=1&web=1&e=lRwWsU)


2. Install Containerlab: https://containerlab.dev/install/

3. Clone this repo and cd into the **02** directory
```bash
git clone https://github.com/segmentrouting/srv6-oci.git
```

```bash
cd ./srv6-oci/02-docker-sonic-vs/
```

4. Deploy the topology
```bash
clab deploy -t topology.clab.yaml
```

The topology will take a couple minutes to fully deploy. Once the containers have been up for 2+ minutes its safe to run the configuration script.

5. Check containers/nodes' status
```bash
docker ps
```

6. Run the **config.sh** script to apply sonic *`config_db.json`* and *`frr.conf`* [configs](./config) to each device
```bash
./config.sh
```

It will take a couple minutes for the **config.sh** script to run through all 96 routers.
Once the script has completed you should see output something like this:

```bash
============================================================
  sonic-docker-4p-8x16 — 4 planes x (8 spine x 16 leaf) SRv6 CLOS
============================================================
  Topology:     sonic-docker-4p-8x16 (from topology.clab.yaml)
  Config dir:   /home/cisco/srv6-oci/02-docker-sonic-vs/config
  Routing:      Controller-driven (no BGP, no IGP)
  Tenants:      green (uDT d000 -> Vrf-green on every leaf)
                yellow (host-based; uDT d001 seg6local on hosts)
============================================================

Deploy complete!
```

The **02** project directory includes a couple script utilities to create **host-based SRv6 routes** on the Alpine containers, and run *`docker exec -it <node name> tcpdump -ni <EthernetXY>`* to see SRv6 uSID forwarding in action

### Quick test - Tenant Green - Host SRv6 Encap, Egress Leaf SRv6 uDT

1. Add a test route from *`green-host00`* to *`green-host15`* thru *`fabric plane-0`*

`Path: green-host00 -> p0-leaf00 -> p0-spine00 -> p0-leaf15 -> green-host15`

```bash
docker exec -it green-host00 ip -6 route add 2001:db8:bbbb:f::/64 encap seg6 mode encap.red segs fc00:0:f000:e00f:d000:: dev eth1
docker exec -it green-host15 ip -6 route add 2001:db8:bbbb::/64 encap seg6 mode encap.red segs fc00:0:f000:e000:d000:: dev eth1
```

1. Run a ping from *`green-host00`* to *`green-host15`*
```bash
docker exec -it green-host00 ping 2001:db8:bbbb:f::2 -i .3
```

1. In another terminal session run tcpdump on the sonic nodes' interfaces along the path:

tcpdump Plane-0 Leaf00 (*`p0-leaf00`*) ingress from *`green-host00`*
```bash
docker exec -it p0-leaf00 tcpdump -ni Ethernet32
```

We expect to see encapsulated echo requests and plain ipv6 echo replies (post uDT decapsulation):
```bash
$ docker exec -it p0-leaf00 tcpdump -ni Ethernet32
tcpdump: verbose output suppressed, use -v[v]... for full protocol decode
listening on Ethernet32, link-type EN10MB (Ethernet), snapshot length 262144 bytes
16:02:00.574771 IP6 2001:db8:bbbb::2 > fc00:0:f000:e00f:d000::: IP6 2001:db8:bbbb::2 > 2001:db8:bbbb:f::2: ICMP6, echo request, id 68, seq 51, length 64
16:02:00.576097 IP6 2001:db8:bbbb:f::2 > 2001:db8:bbbb::2: ICMP6, echo reply, id 68, seq 51, length 64
16:02:00.874927 IP6 2001:db8:bbbb::2 > fc00:0:f000:e00f:d000::: IP6 2001:db8:bbbb::2 > 2001:db8:bbbb:f::2: ICMP6, echo request, id 68, seq 52, length 64
16:02:00.875812 IP6 2001:db8:bbbb:f::2 > 2001:db8:bbbb::2: ICMP6, echo reply, id 68, seq 52, length 64
```


tcpdump *`p0-leaf00`* egress to *`p0-spine00`*
```
docker exec -it p0-leaf00 tcpdump -ni Ethernet0
```

We expect to see encapsulated traffic in both directions:
```bash
$ docker exec -it p0-leaf00 tcpdump -ni Ethernet0
tcpdump: verbose output suppressed, use -v[v]... for full protocol decode
listening on Ethernet0, link-type EN10MB (Ethernet), snapshot length 262144 bytes
16:03:46.535405 IP6 2001:db8:bbbb::2 > fc00:0:e00f:d000::: IP6 2001:db8:bbbb::2 > 2001:db8:bbbb:f::2: ICMP6, echo request, id 68, seq 404, length 64
16:03:46.536145 IP6 2001:db8:bbbb:f::2 > fc00:0:d000::: IP6 2001:db8:bbbb:f::2 > 2001:db8:bbbb::2: ICMP6, echo reply, id 68, seq 404, length 64
16:03:46.835564 IP6 2001:db8:bbbb::2 > fc00:0:e00f:d000::: IP6 2001:db8:bbbb::2 > 2001:db8:bbbb:f::2: ICMP6, echo request, id 68, seq 405, length 64
16:03:46.836538 IP6 2001:db8:bbbb:f::2 > fc00:0:d000::: IP6 2001:db8:bbbb:f::2 > 2001:db8:bbbb::2: ICMP6, echo reply, id 68, seq 405, length 64
```

tcpdump *`p0-spine00`* egress to *`p0-leaf15`* - expect SRv6 encapsulated traffic in both directions
```bash
docker exec -it p0-spine00 tcpdump -ni Ethernet60
```

tcpdump *`p0-leaf15`* ingress from *`spine00`* - expect SRv6 encapsulated traffic in both directions
```bash
docker exec -it p0-leaf15 tcpdump -ni Ethernet0
```

tcpdump *`p0-leaf15`* egress to *`green-host15`* - expect decapsulated echo requests and SRv6 encapsulated echo replies
```bash
docker exec -it p0-leaf15 tcpdump -ni Ethernet32
```

### Quick test - Tenant Yellow - Host SRv6 Encap and Decap

1. Add a test route from *`yellow-host01`* to *`yellow-host14`* via *`fabric plane-1`*

`Path: yellow-host01 -> p1-leaf01 -> p1-spine01 -> p1-leaf14 -> yellow-host14`

```bash
docker exec -it yellow-host01 ip -6 route add 2001:db8:cccd:e::1/128 encap seg6 mode encap.red segs fc00:1:f001:e00e:e009:d001:: dev eth1
docker exec -it yellow-host14 ip -6 route add 2001:db8:cccd:1::1/128 encap seg6 mode encap.red segs fc00:1:f001:e001:e009:d001:: dev eth1
```

2. Run a ping from *`yellow-host01`* to *`yellow-host14`* 
 

Note the ping will need to be sourced from *`yellow-host01's`* loopback address: **-I 2001:db8:cccd:1::1**
```bash
docker exec -it yellow-host01 ping 2001:db8:cccd:e::1 -i .3 -I 2001:db8:cccd:1::1
```

3. tcpdump sequence:
```bash
docker exec -it p1-leaf01 tcpdump -ni Ethernet36
```
```bash
docker exec -it p1-leaf01 tcpdump -ni Ethernet4
```
```bash
docker exec -it p1-spine01 tcpdump -ni Ethernet4
```
```bash
docker exec -it p1-spine01 tcpdump -ni Ethernet56
```
```bash
docker exec -it p1-leaf14 tcpdump -ni Ethernet4
```
```bash
docker exec -it p1-leaf14 tcpdump -ni Ethernet36
```
```bash
docker exec -it yellow-host14 tcpdump -ni eth2
```

### Install Green and Yellow Tenant Test Routes 

1. Run the *`test-routes.sh`* script to install test routes
```bash
./test-routes.sh routes
```

### Spray a flow across all 4 planes (MRC demo)

`tools/spray.py` is a small userspace SRv6/uSID packet generator that splits a single logical flow round-robin across all 4 fabric planes — the **MRC/SRv6** model published [Here](https://cdn.openai.com/pdf/resilient-ai-supercomputer-networking-using-mrc-and-srv6.pdf). 

The `spray.py` tool runs inside the Alpine host containers using a scapy-equipped image (`alpine-srv6-scapy:1.0`, built from `tools/Dockerfile`). The `tools/` directory is bind-mounted read-only into every host at `/tools`, so script edits show up without redeploying.

Build the image once (the topology references it by tag):
```bash
docker build -t alpine-srv6-scapy:1.0 tools/
```

Start the receiver on the destination host (sniffs all 4 NICs):
```bash
docker exec -it green-host15 python3 /tools/spray.py --role recv
```

In another terminal, send 5 seconds of traffic from the source host:
```bash
docker exec -it green-host00 python3 /tools/spray.py --role send \
    --dst-id 15 --rate 100pps --duration 5s
```

The receiver prints per-NIC and per-plane arrival counts. In a healthy fabric you should see ≈25% on each NIC and the per-plane counts matching exactly (plane *P* arrives on `eth(P+1)`).

To watch the wire while spraying, drop the rate and tap any hop — the outer is a uSID-compressed SID list (no SRH), `ip6 proto 41`:
```bash
docker exec -it green-host00 python3 /tools/spray.py --role send \
    --dst-id 15 --rate 5pps --duration 60s &

docker exec -it p0-leaf00 tcpdump -ni Ethernet32 'ip6 proto 41'   # ingress leaf
docker exec -it p0-leaf00 tcpdump -ni Ethernet0  'ip6 proto 41'   # leaf -> spine (one uSID consumed)
docker exec -it p0-leaf15 tcpdump -ni Ethernet32 'udp port 9999'  # post-uDT6 decap
```

Yellow is not supported by spray.py v1 (decap happens in the receiver host kernel, so the sniffer needs to peel the outer before reading the payload — planned for v2). See [`spray.md`](./spray.md) for full details, packet diagram, and limitations.


