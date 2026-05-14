# 4p-8x16 topology

Static SRv6 fabric with 4 planes × 8 spines × 16 leaves, 16 green + 16
yellow hosts. See `docs/topologies/4p-8x16.md` for the address scheme
and SID-list shape. See `docs/design-fabric.md` for the general design.

This directory contains:

- `topo.yaml` — declarative parameters (counts, address blocks); source
  of truth consumed by the generator and srv6_fabric runtime.
- `topology.clab.yaml` — generated containerlab file. Regenerate via
  `make regen-topology TOPO=4p-8x16`.
- `config/` — generated per-node SONiC `config_db.json` + `frr.conf`.
  Regenerate via `make regen-configs TOPO=4p-8x16`. Committed.
- `routes/` — route-set YAMLs (input to `routes` CLI).
- `scenarios/` — MRC scenarios for this topology.
