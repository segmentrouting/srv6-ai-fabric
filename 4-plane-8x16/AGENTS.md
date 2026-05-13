# AGENTS.md — context for AI coding assistants

Read this first when picking up work in `4-plane-8x16/`. It captures
the non-obvious invariants and gotchas that aren't visible from a single
file or from the README alone.

For the human-facing tour: see `README.md`, `quickstart.md`, `spray.md`,
`design-appendix.md`.

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

## Source of truth

`generate_fabric.py` is the single SOT for the whole lab. It emits
`topology.clab.yaml` and the per-node `config/*` SONiC config snippets.
**Never hand-edit `topology.clab.yaml`**; regenerate.

Files that must stay in sync because they share addressing/SID-list shape:

- `generate_fabric.py` (writes routes into SONiC + host configs)
- `routes.py` (parses + writes host-side `ip -6 route` SRv6 routes)
- `tools/spray.py` (CLI; delegates encoding to `mrc/lib/runner.py`)
- `mrc/lib/topo.py` (fabric constants + `usid_outer_dst()` — the SID-list
  builder the runner uses)
- `mrc/lib/runner.py` (wire format: `!QB` seq+plane, 32B pad, sport=
  dport=SPRAY_PORT)

If you change the SID-list shape, the per-plane block layout, or any
tenant naming/addressing, all of these must be updated. The
`test_reference_pairs_match_spray` test in `mrc/tests/test_topo.py`
locks the topo.py ↔ spray.py reference-pairs map in sync.

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
   `tools/spray.py` uses a raw IPv6 socket per plane with
   `SO_BINDTODEVICE`. Kernel ECMP would defeat plane spray since green's
   inner dst is anycast. Don't replace this with kernel routing.
9. **`encap seg6` route shape** (what `routes.py` and `delete --all`
   match on) is what defines "an SRv6 pair route". Yellow's per-NIC
   `seg6local End.DT6` rules are *decap* policies, intentionally not
   touched by `routes.py` apply/delete (they're installed by
   `generate_fabric.py`).

## Naming conventions

- Containers: `p<P>-spine<NN>`, `p<P>-leaf<NN>`, `<tenant>-host<NN>`.
- Host N attaches to `leafN` on every plane. (`hostNN` ↔ `leafNN`.)
- User-facing term is **"tenant"**, never "color".
- Reference lab: a smaller 3×3 SRv6 reference for 

## Tooling specifics

### `routes.py`

Declarative kubectl-style route manager. Requires PyYAML.
Spec format: `apiVersion: srv6-lab/v1`, `kind: RouteSet`, with `pairs`
and/or `mesh` entries only. **There is intentionally no low-level
`routes:` escape hatch** — keep specs high-level.

`spine: auto` resolves via `REFERENCE_PAIRS_SPINES` lookup, falling back
to `(a*16+b) % 8` hash.

Subcommands:

```
routes.py apply  -f spec.yaml
routes.py delete -f spec.yaml
routes.py delete --all
routes.py list   [--host h1,h2] [--tenant green|yellow] [-o wide|raw]
```

`list` modes:

- default — collapsed: `-> <tenant>-host<NN>  via spine<NN>  planes [...]`
- `-o wide` — full per-plane path: `p<P>-leaf<src> -> p<P>-spine<NN> -> p<P>-leaf<dst>  (eth<P+1> metric 10<P>)`
- `-o raw` / `--raw` — literal `ip -6 route` lines

### `tools/spray.py`

Userspace SRv6 sprayer, image `alpine-srv6-scapy:1.0`. Mounted read-only
at `/tools` in every host (`binds: ["tools:/tools:ro"]`). As of the MRC
refactor this is now a thin CLI shim over `mrc/lib/runner.py`; the `mrc/`
package is also bind-mounted into every host at `/mrc:ro`. The shim adds
`/mrc` to `sys.path` and imports `from lib.runner import ...`.

`--role send|recv`, auto-detects tenant from hostname. Sender uses one
raw socket per plane bound via `SO_BINDTODEVICE`. Receiver sniffs at NIC
pre-decap (yellow can't sniff post-decap on `lo` per-NIC).

New flags since the refactor:
- `--policy {round_robin,hash5tuple,weighted:0.4,0.3,0.2,0.1}` — default
  `round_robin` matches the pre-refactor behavior.
- `--json` — emit machine-readable result instead of human-readable
  output; used by the orchestrator (`mrc/run.py`).

### `mrc/` — Multi-plane Reliable Connectivity layer

Static-SRv6 simulation of the OpenAI MRC model on top of the fabric.
Pure Python, stdlib unittest, no external deps in tests. Layout:

```
mrc/
  lib/
    topo.py       fabric constants, address builders, FlowKey + hash5
    policy.py     RoundRobin, Hash5Tuple, Weighted, HealthAware wrapper
    reorder.py    per-flow reorder histogram + loss/dup tracker
    netem.py      nsenter+tc/netem fault injection
    scenario.py   strict YAML validator → Scenario dataclass tree
    runner.py     send/recv library (engine behind tools/spray.py)
    health.py     ICMPv6 probe + K-of-N down/recovery tracker
    report.py     merge sender+receiver JSON → ScenarioReport + ASCII
  run.py          docker-host-side orchestrator
  scenarios/      baseline, hash5tuple, plane-{loss,blackhole,latency}
  tests/          163 tests, ~0.25s, no lab needed
```

Orchestrator usage:

```
python3 -m mrc.run mrc/scenarios/baseline.yaml --verbose
python3 -m mrc.run mrc/scenarios/plane-loss.yaml --dry-run
```

`--dry-run` prints the plan plus the exact `nsenter ... tc qdisc add ...`
argvs that would be invoked — useful for verifying fault targeting
without touching the lab.

Test command (run from `4-plane-8x16/`):
```
python3 -m unittest discover -s mrc/tests -t .
```

### Things `mrc/` does NOT do yet

- **Orchestrator-driven health-aware policy.** `lib/health.HealthMonitor`
  is built and unit-tested, and `lib/policy.HealthAware` wraps any inner
  policy. But `mrc/run.py:policy_to_cli()` raises `NotImplementedError`
  on `{health_aware: ...}` specs because the shim's `--policy` flag
  doesn't yet accept the wrapped form. Next step is to either (a) extend
  the shim CLI to take `--health-aware --probe-target ...`, or (b) keep
  the health probe entirely orchestrator-side and pass a precomputed
  `down` set into senders.

- **Per-NIC RX in `ScenarioReport`.** Receiver reports per-NIC totals
  aggregated across flows. The merge attaches them to the first matched
  flow per receiver; multi-sender-to-one-receiver loses per-NIC fidelity.
  `FlowStats` would need a per-NIC counter to fix this.

### `validate.sh`

Renamed from `test-routes.sh`. Only `demo` and `test` subcommands remain
(route-management code was moved into `routes.py`).

## Gotchas (caught the hard way)

- **Alpine iproute2 output**: addresses print in canonical-collapsed form
  (`fc00:0:f000:e00f:d000::`, not `fc00:0000:...`). Parsers must match
  both forms. `segs` prints in bracketed form: `segs 1 [ <addr> ]`.
- **iproute2 omits `/128`** from natural-width host-route dst even when
  installed as `/128`. Parsers should not require it.
- **Container short names work directly** (`green-host00`) — no
  `clab-<topo>-` prefix needed when invoking `docker exec`.
- **CAP_NET_RAW**: spray.py needs it; clab privileged containers have it.
- **SIGPIPE**: `routes.py` installs `SIG_DFL` so `list | head` doesn't
  traceback. Keep this if you refactor `main()`.
- **Tenant container suffix is the leaf id in hex** (`...:f::2` is
  host15), and SID f-hextet `f00<N>` is spine N. `routes.py`'s
  `_decode_*` helpers depend on this.

## Things to avoid

- Renaming subcommands or files without sweeping `README.md`,
  `quickstart.md`, `spray.md`, and `design-appendix.md`.
- Adding kernel-ECMP / multipath as a sender plane-selection mechanism.
- Putting plane identity anywhere other than the outer SID list.
- Reusing `e<port>` numbering instead of `e<NIC ordinal>`.
- Committing into `topology.clab.yaml` directly (it's generated).
- Creating `CLAUDE.md` alongside this file. One file, this one.

## Style

- Terse, technical commit messages and PR bodies.
- Code comments: explain *why*, not *what*. Mention invariants that
  would otherwise look arbitrary.
- Don't add emojis to files or output unless asked.

## Quick-start verification

After any change touching addressing / SID shape / routing:

```
./generate_fabric.py
containerlab deploy -t topology.clab.yaml
./config.sh
./routes.py apply -f routes/reference-pairs.yaml
./validate.sh test          # expect 64/64 OK for green+yellow ping mesh
docker exec -d yellow-host15 python3 /tools/spray.py --role recv
docker exec yellow-host00 python3 /tools/spray.py --role send \
    --dst-id 15 --rate 1000pps --duration 4s
```

`spray.py` recv (foreground variant) should show roughly balanced counts
across 4 planes.

For MRC end-to-end (assumes `mrc:/mrc:ro` bind-mount present — requires
`clab destroy && clab deploy` if you've just pulled the bind-mount edit):

```
python3 -m mrc.run mrc/scenarios/baseline.yaml --verbose
```

Expect ~0 loss, balanced per-plane counts, low max_reorder_distance.
