### sonic-vpp base topology

![Sonic VPP Test Topology](topology.png)


1. Install Containerlab (or use VXR, etc.)
https://github.com/segmentrouting/srv6-labs/blob/main/1-starter-topologies/README-clab.md

2. Acquire sonic-vpp image (Docker, qcow2, etc.)

3. If qcow2 use vrnetlab to convert to docker image: https://containerlab.dev/manual/vrnetlab/#vrnetlab
   
4. Deploy topology

```
sudo clab deploy -t sonic-vpp-topo.yml
```

