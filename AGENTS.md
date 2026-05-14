# AGENTS.md — context for AI coding assistants

Read this first when picking up work in this repository. It captures
the non-obvious invariants and gotchas that aren't visible from a single
file or from the README alone.

For the human-facing tour: see `README.md` (overview), `docs/quickstart.md`
(deploy/run), `docs/design-fabric.md`, `docs/design-mrc.md`,
`docs/spray-protocol.md`, and `docs/design-appendix.md`.

---

## What this lab is

A 4-plane SRv6 fabric (8 spines × 16 leaves per plane = 128 fabric nodes)
on docker-sonic-vs + Containerlab, plus 32 alpine hosts (16 green + 16
yellow). It demonstrates the MRC + SRv6-spray model: one logical flow
fans out across all 4 planes by varying *only* the outer SID list.

Tenants:

- **green** — hybrid: leaf does encap+uDT6 decap in `Vrf-green`. Anycast
  inner dst `2001:db8:bbbb:<NN>::2` on all 4 NICs (`nodad`).
- **yellow** — host-based: 4 `seg6local End.DT6` per host (one per plane
  NIC). Loopback `2001:db8:cccd:<NN>::1` on host `lo`.

## Repo layout

```
srv6_fabric/           Python package
  topo.py              fabric constants + addressing helpers (reads topo.yaml)
  runner.py, policy.py, reorder.py, netem.py, report.py, health.py
  cli/spray.py         userspace SRv6 packet generator (CLI: `spray`)
  cli/routes.py        static SRv6 route management   (CLI: `routes`)
  mrc/run.py           scenario orchestrator           (CLI: `run-scenario`)
  mrc/scenario.py      scenario YAML schema + executor

generators/fabric.py   parameterized generator (reads topo.yaml)

topologies/<name>/
  topo.yaml            declarative single source of truth for one variant
  topology.clab.yaml   containerlab topology (generated)
  config/              per-node SONiC + FRR configs   (generated)
  scenarios/           MRC scenario YAMLs
  routes/              route-spec YAMLs for `routes apply`

host-image/Dockerfile  alpine + scapy + pip-installed srv6_fabric
scripts/config.sh      push configs to running containers
tests/                 165 unit tests mirroring srv6_fabric/ layout
docs/                  consolidated design + runbook docs
results/               scenario output JSON (gitignored)
Makefile               operator workflow entry point
```

## Source of truth

`topologies/<name>/topo.yaml` declares the topology: planes, spines,
leaves, container images, clab topology name. The generator
(`generators/fabric.py --topo <path>`) reads it and emits
`topology.clab.yaml` + the per-node `config/*` SONiC config snippets in
the same directory. The `srv6_fabric.topo` runtime module also reads
it at import time (via the `SRV6_TOPO` env var, defaulting to
`topologies/4p-8x16/topo.yaml`).

**Never hand-edit generated files**:
- `topologies/<name>/topology.clab.yaml`
- `topologies/<name>/config/<node>/{config_db.json,frr.conf}`

Files that must stay in sync because they share addressing/SID-list shape:

- `generators/fabric.py` (writes routes into SONiC + host configs)
- `srv6_fabric/cli/routes.py` (parses + writes host-side `ip -6 route`
  SRv6 routes)
- `srv6_fabric/cli/spray.py` (CLI; delegates encoding to
  `srv6_fabric.runner`)
- `srv6_fabric/topo.py` (fabric constants + `usid_outer_dst()` — the
  SID-list builder the runner uses)
- `srv6_fabric/runner.py` (wire format: `!QB` seq+plane, 32B pad,
  sport=dport=SPRAY_PORT)

If you change the SID-list shape, the per-plane block layout, or any
tenant naming/addressing, all of these must be updated. The
`test_reference_pairs_match_spray` test in `tests/test_topo.py` locks
the `srv6_fabric.topo` ↔ `spray` reference-pairs map in sync.

## Hard invariants (do not violate)

1. **uSID = no SRH.** Outer is plain IPv6 with `nh = 41` (IPv6-in-IPv6);
   the SID list is the destination address itself and shifts left at each
   hop. `encap.red` semantics. Do not add SRH.
2. **Plane identity lives ONLY in the outer SID list** (the `<P>` hextet
   at index 1 of the SID). Never in the inner/tenant address. Putting
   plane in the inner address breaks the MRC invariant — the whole point
   of the demo.
3. **Per-plane uSID `/32` blocks** under the cluster `fc00:0000::/30`:
   plane P uses `fc00:<P>::/32`.
4. **Tier role hextets** are self-describing:
   - `f...` = leaf-up uA (spine→leaf)
   - `e...` = spine-down uA *and* leaf→host uA (overloaded; tier is
     disambiguated by position in the SID list)
   - `d...` = tenant uDT6 (decap into VRF / End.DT6)
5. **`e<NIC ordinal>` rule**, not `e<port number>`:
   - `Ethernet0`  → `e000`
   - `Ethernet32` → `e008`
   - `Ethernet36` → `e009`
   This bit us before. Don't "fix" it back to using port numbers.
6. **Green leaf VRF**: `Ethernet32` lives in `Vrf-green`; leaf `d000`
   uDT6 decaps into it.
7. **Yellow decap on host, not leaf**: yellow's `End.DT6` runs on the
   host's NIC (one per plane), not on the leaf. The sender's SID list
   includes the extra `e009` hop (leaf→host) that green omits.
8. **Sender plane selection is NIC-bound, not route-metric-bound.**
   `spray` uses a raw IPv6 socket per plane with `SO_BINDTODEVICE`.
   Kernel ECMP would defeat plane spray since green's inner dst is
   anycast. Don't replace this with kernel routing.
9. **`encap seg6` route shape** (what `routes` and `delete --all`
   match on) is what defines "an SRv6 pair route". Yellow's per-NIC
   `seg6local End.DT6` rules are *decap* policies, intentionally not
   touched by `routes` apply/delete (they're installed by the
   generator).

## Naming conventions

- Containers: `p<P>-spine<NN>`, `p<P>-leaf<NN>`, `<tenant>-host<NN>`.
- Host N attaches to `leafN` on every plane. (`hostNN` ↔ `leafNN`.)
- User-facing term is **"tenant"**, never "color".

## Tooling specifics

### `routes` (`srv6_fabric/cli/routes.py`)

Declarative kubectl-style route manager. Requires PyYAML.
Spec format: `apiVersion: srv6-lab/v1`, `kind: RouteSet`, with `pairs`
and/or `mesh` entries only. **There is intentionally no low-level
`routes:` escape hatch** — keep specs high-level.

`spine: auto` resolves via `REFERENCE_PAIRS_SPINES` lookup, falling back
to `(a*16+b) % 8` hash.

Subcommands:

```
routes apply  -f spec.yaml
routes delete -f spec.yaml
routes delete --all
routes list   [--host h1,h2] [--tenant green|yellow] [-o wide|raw]
```

`list` modes:

- default — collapsed: `-> <tenant>-host<NN>  via spine<NN>  planes [...]`
- `-o wide` — full per-plane path: `p<P>-leaf<src> -> p<P>-spine<NN> -> p<P>-leaf<dst>  (eth<P+1> metric 10<P>)`
- `-o raw` / `--raw` — literal `ip -6 route` lines

### `spray` (`srv6_fabric/cli/spray.py`)

Userspace SRv6 sprayer, image `alpine-srv6-scapy:1.0`. The image
pip-installs the `srv6_fabric` package at build time, so `spray` lives
at `/usr/local/bin/spray` inside every host container — no bind mounts
needed. The image also bakes `topologies/<name>/topo.yaml` at
`/etc/srv6_fabric/topo.yaml` and exports `SRV6_TOPO` pointing at it,
so the runtime `srv6_fabric.topo` constants match the deployed
topology.

`--role send|recv`, auto-detects tenant from hostname. Sender uses one
raw socket per plane bound via `SO_BINDTODEVICE`. Receiver sniffs at NIC
pre-decap (yellow can't sniff post-decap on `lo` per-NIC).

Notable flags:
- `--policy {round_robin,hash5tuple,weighted:0.4,0.3,0.2,0.1}` — default
  `round_robin`.
- `--json` — emit machine-readable result instead of human-readable
  output; used by the orchestrator (`run-scenario`).

### `run-scenario` (`srv6_fabric/mrc/run.py`)

Docker-host-side orchestrator for MRC scenarios. Loads a scenario YAML,
applies fault injection via `nsenter ... tc qdisc add ...` against host
veths, runs `spray` send/recv inside the relevant containers via
`docker exec`, and merges the JSON output into a `ScenarioReport`.

```
run-scenario topologies/4p-8x16/scenarios/baseline.yaml --verbose
run-scenario topologies/4p-8x16/scenarios/plane-loss.yaml --dry-run
```

`--dry-run` prints the plan plus the exact `nsenter ... tc qdisc add ...`
argvs that would be invoked — useful for verifying fault targeting
without touching the lab.

### Things the MRC layer does NOT do yet

- **Orchestrator-driven health-aware policy.** `srv6_fabric.health.HealthMonitor`
  is built and unit-tested, and `srv6_fabric.policy.HealthAware` wraps
  any inner policy. But `srv6_fabric.mrc.run.policy_to_cli()` raises
  `NotImplementedError` on `{health_aware: ...}` specs because the
  shim's `--policy` flag doesn't yet accept the wrapped form. Next step
  is to either (a) extend the spray CLI to take
  `--health-aware --probe-target ...`, or (b) keep the health probe
  entirely orchestrator-side and pass a precomputed `down` set into
  senders.

- **Per-NIC RX in `ScenarioReport`.** Receiver reports per-NIC totals
  aggregated across flows. The merge attaches them to the first matched
  flow per receiver; multi-sender-to-one-receiver loses per-NIC fidelity.
  `FlowStats` would need a per-NIC counter to fix this.

### Removed: `scripts/validate.sh`

Previously a ping+tcpdump per-plane verification harness. Removed because
its model — ping with `-I eth<N>` to force outbound plane — couldn't verify
the return path: ICMPv6 replies bypass any plane affinity (the kernel just
picks the lowest-metric route to the source's anycast address), so planes
1..N-1 always reported FAIL. End-to-end verification is now via
`make scenario SCEN=baseline`, which uses spray (sender-side plane
selection via SO_BINDTODEVICE) and measures per-plane stats at the receiver.

## Test command (run from repo root)

```
PYTHONPATH=. python3 -m unittest discover -s tests -t .
```

or:

```
make test
```

165 tests, ~0.25s, no lab needed.

## Gotchas (caught the hard way)

- **Alpine iproute2 output**: addresses print in canonical-collapsed form
  (`fc00:0:f000:e00f:d000::`, not `fc00:0000:...`). Parsers must match
  both forms. `segs` prints in bracketed form: `segs 1 [ <addr> ]`.
- **iproute2 omits `/128`** from natural-width host-route dst even when
  installed as `/128`. Parsers should not require it.
- **Container short names work directly** (`green-host00`) — no
  `clab-<topo>-` prefix needed when invoking `docker exec`.
- **CAP_NET_RAW**: `spray` needs it; clab privileged containers have it.
- **SIGPIPE**: `routes` installs `SIG_DFL` so `routes list | head`
  doesn't traceback. Keep this if you refactor `main()`.
- **Tenant container suffix is the leaf id in hex** (`...:f::2` is
  host15), and SID f-hextet `f00<N>` is spine N. `routes`'s
  `_decode_*` helpers depend on this.
- **IPv6 string canonicalization**: `fc00:0000:f000:0e00:d000::` and
  `fc00:0:f000:e00f:d000::` are equal but the strings differ. Anywhere
  that compares IPv6 addresses-as-strings, route them through
  `ipaddress.IPv6Address` first. See `_canon_addr()` in
  `srv6_fabric/report.py`.

## Things to avoid

- Renaming subcommands or files without sweeping `README.md`, `AGENTS.md`,
  and the relevant `docs/*.md`.
- Adding kernel-ECMP / multipath as a sender plane-selection mechanism.
- Putting plane identity anywhere other than the outer SID list.
- Reusing `e<port>` numbering instead of `e<NIC ordinal>`.
- Committing into `topologies/<name>/topology.clab.yaml` or
  `topologies/<name>/config/` directly (they're generated).
- Creating `CLAUDE.md` alongside this file. One file, this one.

## Style

- Terse, technical commit messages and PR bodies.
- Code comments: explain *why*, not *what*. Mention invariants that
  would otherwise look arbitrary.
- Don't add emojis to files or output unless asked.

## Quick-start verification

After any change touching addressing / SID shape / routing:

```
make regen                                                   # generate topo + configs
make deploy                                                  # containerlab deploy
make config                                                  # push SONiC configs
make host-routes                                             # full-mesh per-tenant host kernel routes
make scenario SCEN=baseline                                  # end-to-end spray + per-plane stats

docker exec -d yellow-host15 spray --role recv
docker exec yellow-host00 spray --role send \
    --dst-id 15 --rate 1000pps --duration 4s
```

`spray` recv (foreground variant) should show roughly balanced counts
across 4 planes.

For MRC end-to-end:

```
make scenario SCEN=baseline
```

Expect ~0 loss, balanced per-plane counts, low `max_reorder_distance`.
