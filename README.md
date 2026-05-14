# srv6-ai-fabric

A research-grade simulator for Cisco's Multi-plane Reliable Connectivity
(MRC) approach to AI-fabric networking, layered on top of a static SRv6
uSID dataplane in [SONiC](https://sonic-net.github.io/SONiC/) +
[containerlab](https://containerlab.dev/) + Linux kernel SRv6.

The reference topology is a **4-plane × 8-spine × 16-leaf Clos** carrying
two tenants (`green`, `yellow`) with anycast hosts. The generator is
parameterized via `topo.yaml`, so additional Clos variants are
straightforward to add under `topologies/<name>/`.

## What's interesting here

- **Pure-static control plane.** No BGP, no IGP. Every SID is preloaded
  via SONiC ConfigDB; route lifecycle is managed from a controller-side
  Python CLI (`routes`) that emits idempotent ConfigDB patches.
- **Userspace MRC sender.** `spray` builds per-plane uSID-encapsulated
  UDP probes in scapy, sends one packet per plane in a round, and the
  receiver computes per-flow reorder-distance histograms (the OpenAI
  MRC paper's reorder metric) plus loss, latency, and PPS.
- **Fault injection.** Scenarios under `topologies/<name>/scenarios/`
  drive `tc netem` against host veths via `nsenter`, exercising
  plane-loss, plane-latency, and plane-blackhole failure modes.
- **Two tenants, two SRv6 patterns.** Green is leaf-decapped (uDT6 in
  `Vrf-green` on every leaf); yellow is host-decapped via per-plane
  `seg6local End.DT6` on the destination NIC. Both work end-to-end.

For the why behind each design choice, see `docs/design-fabric.md`,
`docs/design-mrc.md`, and `docs/design-appendix.md`.

## Layout

```
srv6_fabric/           Python package: topology constants, runtime libs
  topo.py              fabric dimensions + addressing helpers (reads topo.yaml)
  runner.py            spray sender/receiver core
  policy.py            per-plane scheduling policies (round-robin, hash, etc.)
  reorder.py           reorder-distance histogram + FlowStats schema
  netem.py             tc netem helpers (run via nsenter)
  cli/
    spray.py           userspace SRv6 packet generator (CLI: `spray`)
    routes.py          static SRv6 route management   (CLI: `routes`)
  mrc/
    run.py             scenario orchestrator           (CLI: `run-scenario`)
    scenario.py        scenario YAML schema + executor
    health.py          plane health-aware policy (not yet CLI-wired)

generators/
  fabric.py            parameterized generator: reads topo.yaml,
                       writes topology.clab.yaml + config/

topologies/
  4p-8x16/
    topo.yaml          single source of truth for this variant
    topology.clab.yaml containerlab topology (generated)
    config/            per-node SONiC + FRR configs   (generated)
    scenarios/         MRC scenario YAMLs
    routes/            route-spec YAMLs for `routes apply`
    README.md          per-topology design notes

host-image/
  Dockerfile           alpine + scapy + pip-installed srv6_fabric

scripts/
  config.sh            push config_db.json + frr.conf into containers
  validate.sh          ping/tcpdump validation harness

tests/                 unittest mirror of srv6_fabric/ layout (165 tests)
docs/                  consolidated design + runbook documentation
results/               scenario JSON output (gitignored)
```

## Quickstart

```bash
# 0. install Python deps for the controller side
pip install -e '.[dev]'

# 1. build the host image (alpine + scapy + srv6_fabric)
make image

# 2. (re)generate topology + SONiC configs from topo.yaml
make regen

# 3. deploy the lab
make deploy

# 4. push SONiC configs into the running containers
make config

# 5. install per-tenant SRv6 routes
make routes

# 6. validate (16 ping pairs × 4 planes + tcpdump)
make validate

# 7. run an MRC scenario
make scenario SCEN=baseline
make scenario SCEN=plane-loss
make scenario SCEN=plane-blackhole
```

The CLIs (`spray`, `routes`, `run-scenario`) work both on the lab host
(after `pip install -e .`) and inside the host containers (baked into
the image at build time).

## Run a different topology

Each variant lives under `topologies/<name>/` with its own `topo.yaml`
declaring planes / spines / leaves / images / clab name. Drop one in,
then:

```bash
make TOPO=<name> regen image deploy config validate
```

The `srv6_fabric` runtime picks up the right dimensions from the
`SRV6_TOPO` env var (baked into the image at `/etc/srv6_fabric/topo.yaml`),
or from `topologies/<name>/topo.yaml` relative to the repo root when
running outside a container.

## Testing

```bash
make test     # 165 unit tests, ~0.3s, no external deps
```

Tests cover address derivation, SID-list construction, the spray wire
format, reorder-distance computation, scenario YAML parsing, route-spec
patch generation, and the MRC orchestrator's argv plumbing.

## Status

| Phase | What | Status |
|------:|------|--------|
| 1 | scaffold `srv6_fabric` package + top-level dirs | done |
| 2 | move files; rewrite imports; preserve git history | done |
| 3 | parameterize generator via `topo.yaml` | done |
| 4 | rebuild host image around pip-installed package | done |
| 5 | Makefile + top-level README | done |
| 6 | rewrite docs to match new paths | pending |

Active branch during the reorg: `reorg/srv6_fabric`.

## License

Apache-2.0. See `LICENSE`.
