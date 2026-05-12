# spray.py — userspace SRv6/uSID packet spray

A small Python tool that demonstrates the SRv6 packet spray model published [Here](https://cdn.openai.com/pdf/resilient-ai-supercomputer-networking-using-mrc-and-srv6.pdf): one logical flow is split across all 4 fabric planes by varying only the **outer** SID list, while the **inner** tenant address stays plane-independent.

The tool has two roles, sender and receiver. Run the receiver first, then the sender in a separate terminal.

```
docker exec -it green-host15 python3 /tools/spray.py --role recv
docker exec -it green-host00 python3 /tools/spray.py --role send --dst-id 15 --rate 1000pps --duration 5s
```

The lab's `tools/` directory bind-mounted read-only into every host [example](./topology.clab.yaml#L496), so script edits show up immediately — only `tools/Dockerfile` changes require an image rebuild.

---

## What it sends

For each packet `i` the sender picks plane `P = i mod 4` and emits:

```
+----------------------------------------------------------------+
| IPv6  src = host-underlay[P]  dst = uSID[P]   nh = 41          |   outer
|   +------------------------------------------------------------+
|   | IPv6  src = inner          dst = inner-anycast   nh = 17   |   inner
|   |   +--------------------------------------------------------+
|   |   | UDP  sport = dport = 9999                              |
|   |   |   +-----------+----------+--------------+              |
|   |   |   | seq (8B)  | plane(1B)| pad ('X' x32)|              |
+---+---+---+-----------+----------+--------------+--------------+
```

Key MRC invariants this enforces:

- **Inner dst is identical for all 4 planes** — `2001:db8:bbbb:<dst-id>::2` for green. The plane lives ONLY in the outer SID list.
- **Outer is an SRv6 uSID carrier** `nh = 41` (IPv6-in-IPv6); the SID list is encoded in the destination address itself and shifts left at each hop. encap.red semantics, no extension headers.
- **Egress NIC = plane.** The sender opens one raw socket per plane and pins it with `SO_BINDTODEVICE` to `eth1..eth4`. Without this, the kernel would route all 4 planes out the same NIC (the inner anycast dst is the same on all of them).

Example outer destination per plane - green, dst-id=15 (green-host15), transit spine=0:

| plane | egress NIC | outer dst                                |
| ----- | ---------- | ---------------------------------------- |
| 0     | eth1       | `fc00:0000:f000:e00f:d000::`             |
| 1     | eth2       | `fc00:0001:f000:e00f:d000::`             |
| 2     | eth3       | `fc00:0002:f000:e00f:d000::`             |
| 3     | eth4       | `fc00:0003:f000:e00f:d000::`             |

Each hop consumes one uSID by shifting the address left:

```
fc00:000P:f000:e00f:d000::      sender emits
fc00:000P:e00f:d000::            after p<P>-leaf00 (consumed f000)
fc00:000P:d000::                 after p<P>-spine00 (consumed e00f)
(plain inner)                    after p<P>-leaf15 uDT6 decap (d000 in Vrf-green)
```

---

## What it counts

Receiver opens one scapy `AsyncSniffer` per NIC with BPF `udp port 9999`. Because the egress leaf's `End.DT6` already stripped the outer SRv6 by the time the packet reaches the receiver host's NIC, the sniffer sees a plain inner IPv6/UDP frame and reads the `(seq, plane)` from the payload.

After Ctrl-C (or send-side `--duration` expiry), the receiver prints:

```
  received N packets
  per NIC:
    eth1: ...
    eth2: ...
    eth3: ...
    eth4: ...
  per plane (from payload):
    plane 0: ...
    plane 1: ...
    plane 2: ...
    plane 3: ...
  seq range: first..last  (E expected, missing=L)
```

A healthy lab gives:
- per-NIC counts roughly equal (≈ N/4 each),
- per-plane counts exactly equal to per-NIC counts (plane P arrives on `eth(P+1)` — anything else means a routing surprise),
- `missing = 0`.

---

## Spot-checking the wire

Run `spray.py` at a low rate so you can read tcpdump in another terminal:

```bash
docker exec -it green-host00 python3 /tools/spray.py --role send \
    --dst-id 15 --rate 5pps --duration 60s
```

Then tap any hop along the path. The `(spine0, dst-leaf 15)` example below uses plane 0; substitute `p<P>-...` and `Ethernet<...>` for the other planes.

**Ingress leaf, leaf-side (sees host's outer SID list):**
```bash
docker exec -it p0-leaf00 tcpdump -ni Ethernet32 'ip6 proto 41'
# IP6 ...:bbbb:0000::2 > fc00:0:f000:e00f:d000:: : IP6 ...:bbbb::2 > ...:bbbb:f::2: UDP, length 41
```

**Ingress leaf → spine (one uSID consumed):**
```bash
docker exec -it p0-leaf00 tcpdump -ni Ethernet0 'ip6 proto 41'
# dst = fc00:0:e00f:d000::
```

**Spine → egress leaf (another uSID consumed):**
```bash
docker exec -it p0-spine00 tcpdump -ni Ethernet60 'ip6 proto 41'
# dst = fc00:0:d000::
```

**Egress leaf → receiver host (decapped — no outer SRv6 anymore):**
```bash
docker exec -it p0-leaf15 tcpdump -ni Ethernet32 'udp port 9999'
# IP6 ...:bbbb::2 > ...:bbbb:f::2: UDP, length 41
```

---

## Limitations (v1)

- **Green Tenant only.** For yellow tenant, the egress leaf only consumes `e009` and leaves the final `d001` uSID on the wire — decap happens in the receiver host's kernel `seg6local End.DT6`. The current sniffer's BPF (`udp port 9999`) won't match that outer-still-present frame. Yellow support requires `ip6 proto 41 or (udp port 9999)` and a parse-time outer peel; planned for v2.
- **No reorder metric yet.** v1 only counts arrivals and the seq range (gives loss). True per-plane reorder histograms come with v2.
- **No flow hashing.** v1 is pure round-robin; the paper's hash-based variants (5-tuple, MRC-hash) are a v3 extension.
- **One pair per run.** Multi-pair concurrent spray (the realistic AI-cluster pattern) is also v3.

---

## Arguments

```
--role send|recv              required
--dst-id N                    (send) destination host id 0..15
--rate Npps | N               (send) packets/sec, default 1000pps
--duration Ns | Nms | 0       (send) default 5s; 0 = run until ^C
```

The sender infers its own tenant + id from the container hostname (`green-host00` → tenant=green, id=0). It will refuse to spray to itself.


