# MRC layer

The static SRv6 substrate in the parent directory is the *fabric*. This
subdirectory adds the **MRC behaviors** that ride on top of it: spray policy,
reorder measurement, multi-pair flow generation, plane failure injection, and
plane-health signaling back to the senders.

Read this in the context of:

- `../README.md` — the 4-plane Clos and uSID address scheme
- `../design-appendix.md` §10 — the plane-independent inner addressing
  invariant MRC relies on
- `./spray-protocol.md` — the round-robin sprayer this layer extends
- The OpenAI MRC + SRv6 paper:
  https://cdn.openai.com/pdf/resilient-ai-supercomputer-networking-using-mrc-and-srv6.pdf

## What MRC needs that the fabric doesn't provide

| Requirement | Where it lives |
|---|---|
| One logical flow → many planes (spray) | `srv6_fabric/cli/spray.py` (CLI: `spray`) |
| Plane choice per packet by **policy** (hash / weighted / health-aware) | `srv6_fabric/policy.py` |
| Many concurrent flows in one test run | `srv6_fabric/runner.py` |
| Per-flow reorder distance measurement at receiver | `srv6_fabric/reorder.py` |
| Plane failure injection (loss, delay, blackhole) | `topologies/<name>/scenarios/*.yaml` driving `tc netem` via `srv6_fabric/netem.py` |
| Plane-health signal from fabric → host | `srv6_fabric/health.py` — minimal: ICMPv6 probe per plane |
| Run orchestration (start recv on N hosts, drive senders, collect) | `srv6_fabric/mrc/run.py` (CLI: `run-scenario`) |
| Result collection / per-scenario reports | `srv6_fabric/report.py` |

## Module layout

```
srv6_fabric/
├── topo.py              # fabric constants + addressing (reads topo.yaml)
├── policy.py            # SprayPolicy: round_robin | hash5tuple | weighted | health_aware
├── runner.py            # multi-flow send/recv core (engine behind `spray`)
├── reorder.py           # per-flow reorder distance histograms
├── health.py            # per-plane reachability probe (ICMPv6 to leaf gateway)
├── netem.py             # tc/netem injection helpers (runs on the docker host)
├── report.py            # write JSON + ascii summary
├── cli/
│   ├── spray.py         # userspace SRv6 spray CLI
│   └── routes.py        # kubectl-style static SRv6 route manager
└── mrc/
    ├── run.py           # orchestrator: parse scenario, drive senders, merge
    └── scenario.py      # YAML schema + executor

topologies/<name>/scenarios/
├── baseline.yaml        # 16 flows, round-robin, no failure
├── hash5tuple.yaml      # 16 flows, 5-tuple hash policy, no failure
├── plane-loss.yaml      # 16 flows + 5% loss on plane 2
├── plane-blackhole.yaml # 16 flows + plane 2 blackhole
└── plane-latency.yaml   # 16 flows + 50ms added latency on plane 2
```

## Data model

### Scenario (YAML, consumed by `run-scenario`)

```yaml
name: plane-loss-5pct
description: 16 concurrent green flows, round-robin spray, 5% loss on plane 2

flows:
  - pairs: green-pairs-8       # named set in topo.py (8 pairs, 16 hosts)
    policy: round_robin
    rate: 1000pps
    duration: 30s

faults:                        # optional; applied before flows start
  - kind: netem
    target: plane 2
    spec: loss 5%

report:
  out: results/plane-loss-5pct.json
```

### Per-flow record (in result JSON)

```json
{
  "src": "green-host00",
  "dst": "green-host15",
  "policy": "round_robin",
  "sent": 30000,
  "received": 28500,
  "loss": 1500,
  "per_plane_sent":  {"0": 7500, "1": 7500, "2": 7500, "3": 7500},
  "per_plane_recv":  {"0": 7500, "1": 7500, "2": 6000, "3": 7500},
  "per_plane_loss":  {"0": 0,    "1": 0,    "2": 1500, "3": 0},
  "reorder_hist": {"0": 26880, "1": 1200, "2": 320, "3": 80, "...": "..."},
  "reorder_max": 41,
  "reorder_mean": 0.62
}
```

`reorder_hist[k]` = number of packets whose sequence number arrived `k`
positions earlier than the maximum seq seen so far in this flow. `k=0` =
in-order. The aggregate `reorder_max` and `reorder_mean` summarize the same
distribution for quick eyeballing.

## Spray policies

| Name | Behavior | Notes |
|---|---|---|
| `round_robin` | `plane = seq % 4` | Current `spray.py` behavior; trivially balanced; ignores plane health. |
| `hash5tuple` | `plane = hash(src, dst, sport, dport, proto) % 4` | Per-flow plane affinity; mimics ECMP. Single flow → single plane unless tuple varies. |
| `weighted` | Plane probabilities from scenario YAML | E.g. `[0.4, 0.3, 0.2, 0.1]`; used to model TE / congestion bias. |
| `health_aware` | `round_robin` minus planes currently flagged unhealthy | `health.py` runs an ICMPv6 probe per plane every `--probe-interval`; sets a shared bitmap the policy reads. |

Policies share a tiny interface in `policy.py`:

```python
class SprayPolicy:
    def pick(self, seq: int, flow: FlowKey) -> int: ...
```

## Reorder metric

Per-flow at the receiver. For each (src, dst, sport, dport) tuple:

1. Maintain `max_seq_seen`.
2. For each arriving packet with seq `s`:
   - if `s > max_seq_seen`: bin 0 (in-order); update max.
   - else: bin `max_seq_seen - s` (how far behind it arrived).
3. At end of flow, emit histogram + `max` + `mean` + `p99`.

This matches the OpenAI paper's reorder definition closely enough for
comparison. The metric is computed entirely in the receiver process — no
extra protocol additions, no time sync needed.

## Failure injection — `tc/netem` on host veths

Choice rationale (from the scoping discussion): `tc netem` on the host-side
veth is the cleanest place because

1. SONiC's view of the fabric is untouched (no need to reload configs).
2. Failures are scoped to one host's view of one plane — closer to a real
   NIC/optic failure than a fabric link drop.
3. Easy to compose: loss + latency + reorder are all `tc qdisc` options.
4. Trivially reversible: `tc qdisc del`.

Injection runs on the **container host**, not inside the container, because
`tc` on a veth peer needs root in the host netns. `srv6_fabric/netem.py` shells
out to `ip netns` and `tc` via `docker inspect`-resolved netns paths, with
the same per-plane mapping the senders already use.

Targets supported:

```
target: plane N                 # all 32 host-side NICs in plane N
target: host green-host00       # all 4 NICs of one host
target: host green-host00 plane 2   # one (host, plane) pair
```

## Plane-health signal

A minimal substitute for BFD-on-uplink: every sender spawns a probe thread
that pings `2001:db8:bbbb:<NN>::1` (green) or
`2001:db8:cccc:<P><NN>::1` (yellow) on each plane every `--probe-interval`
(default 1s, RTT-budgeted). If `K` consecutive probes fail (default 3),
the plane is flagged down for that sender; `health_aware` policy stops
choosing it. A successful probe reinstates immediately.

This is intentionally crude — it's enough to demonstrate "fast withdrawal"
behavior under `tc netem ... loss 100%` without bringing in BFD. A real
deployment would wire actual BFD sessions per uplink.

## Orchestration

`run.py` is a thin orchestrator:

1. Parse scenario YAML.
2. Apply `faults:` (calls `lib/netem.py`).
3. For each unique receiver, `docker exec` a `runner.py --role recv` in
   background; pipe its result JSON back over the exec stream.
4. For each flow, `docker exec` `runner.py --role send` with the matching
   policy / rate / duration.
5. Wait for sends to finish + per-recv idle timeout.
6. Reverse `faults:`.
7. Merge per-flow JSON into one report and write to `report.out`.

It does **not** speak to SONiC at all. Everything MRC-level is host-side.

## What this is still not

- **Not a controller.** No PCEP/BGP-LS/SR-policy programming. The MRC
  layer assumes the static SRv6 substrate from the parent dir is up and
  uses it as-is.
- **Not BFD.** The plane-health probe is good enough for "did the plane
  go dark?", not for sub-100ms convergence claims.
- **Not at scale.** We're testing correctness and qualitative behavior on
  docker-sonic-vs. Throughput numbers are meaningless; reorder/loss
  patterns are not.

## Roadmap and status

| Item | Status |
|---|---|
| Design doc (this file) | done |
| `srv6_fabric/topo.py`, `policy.py`, `reorder.py` | done |
| `srv6_fabric/runner.py` (send/recv core; `spray` is a thin CLI shim) | done |
| `srv6_fabric/netem.py` | done |
| `srv6_fabric/health.py` (HealthMonitor + HealthAware policy wrapper) | done, not yet wired through `spray` CLI |
| `srv6_fabric/mrc/run.py` orchestrator (CLI: `run-scenario`) | done |
| `topologies/4p-8x16/scenarios/baseline.yaml` (smoke test) | done |
| `scenarios/plane-loss.yaml` + `plane-blackhole.yaml` + `plane-latency.yaml` | done |
| Compare round-robin vs hash5tuple vs health_aware under fault | TODO |
