# Running MRC

Practical guide for running MRC scenarios and tests against a deployed
4-plane fabric. For *what* MRC is and the design rationale, see
`design-mrc.md`. For *how to read* the resulting JSON / ASCII reports,
see `results-format.md`.

All commands below assume you are at the repo root with the fabric
already deployed (`make deploy`).

## Unit tests

Pure-Python, stdlib unittest, no network or container access needed.
Run from the repo root:

```bash
make test
# or directly:
PYTHONPATH=. python3 -m unittest discover -s tests -t .
```

Expected: 165+ tests pass in well under a second. Use this as a
fast smoke check after any edit to `srv6_fabric/*` or
`srv6_fabric/cli/spray.py`.

To run a single test module or case:

```bash
PYTHONPATH=. python3 -m unittest tests.test_report
PYTHONPATH=. python3 -m unittest tests.test_report.TestMatchingHappyPath
PYTHONPATH=. python3 -m unittest tests.test_report.TestMatchingHappyPath.test_single_flow_matched
```

## Manual two-host spray (no orchestrator)

Useful for ad-hoc debugging or sanity-checking a single host pair. Two
terminals on the lab host.

**Receiver** (start first; self-exits after `--idle-timeout`):

```bash
docker exec green-host15 spray --role recv --idle-timeout 6s
```

**Sender** (start after the receiver banner appears, ~1s settle):

```bash
docker exec green-host00 spray --role send \
    --dst-id 15 --rate 1000pps --duration 5s
```

Add `--json` to either side for machine-readable output (pipe through
`jq .` to validate).

Pick a different spray policy with `--policy`:

```bash
docker exec green-host00 spray --role send \
    --dst-id 15 --rate 1000pps --duration 5s --policy hash5tuple
```

With `hash5tuple` a single flow pins to a single plane — per-plane
sent counts will be unbalanced and `reord` should be 0.

## Scenario orchestrator

`run-scenario` (`srv6_fabric/mrc/run.py`) reads a YAML scenario, spawns
receivers, runs senders in parallel via `docker exec`, applies/reverts
`tc netem` faults, merges results, and renders an ASCII + JSON report.

**Dry run** (parses YAML, prints planned flows and netem argvs, no
containers touched):

```bash
run-scenario topologies/4p-8x16/scenarios/baseline.yaml --dry-run
# or via make:
make scenario SCEN=baseline   # add --dry-run by editing the Makefile target
```

**Real run**:

```bash
run-scenario topologies/4p-8x16/scenarios/baseline.yaml \
    --report results/baseline.json
# or:
make scenario SCEN=baseline
```

Scenarios that inject faults need `sudo` because `tc netem` is applied
via `nsenter` into container network namespaces:

```bash
sudo run-scenario topologies/4p-8x16/scenarios/plane-loss.yaml \
    --report results/plane-loss.json
```

The orchestrator always reverts netem in a `finally` block. Verify
between runs:

```bash
for h in green-host00 green-host15; do
  echo "=== $h ==="
  for nic in eth1 eth2 eth3 eth4; do
    docker exec $h tc qdisc show dev $nic
  done
done
```

Every line should be `qdisc noqueue` or `qdisc pfifo_fast` — no
`netem`. If any `netem` qdisc is left behind, clear it with:

```bash
docker exec <host> tc qdisc del dev <nic> root
```

## Bundled scenarios

Under `topologies/4p-8x16/scenarios/`:

| Scenario | Purpose | Expect |
|---|---|---|
| `baseline.yaml` | All planes healthy, 8 well-known pairs | loss% ≈ 0, modest reord from per-plane jitter |
| `hash5tuple.yaml` | Single-plane pinning per flow | per-plane unbalanced, reord = 0 |
| `plane-latency.yaml` | +N ms on one plane | loss% ≈ 0, reord and reord_max ↑↑ |
| `plane-loss.yaml` | 5% random loss on one plane | loss% ≈ (plane_loss% / 4), modest reord |
| `plane-blackhole.yaml` | 100% drop on one plane | loss% ≈ 25%, target plane shows rx=0, reord ↓ |

See each `.yaml` for the exact flow list, rates, and netem specs.

## Writing a new scenario

Minimum shape (see existing files for full options):

```yaml
name: my-scenario
description: |
  One sentence about what this proves.

flows:
  - src: green-host00
    dst: green-host15
    rate: 1000pps
    duration: 5s
    policy: round_robin

faults:
  - target: { host: green-host00, plane: 2 }
    spec: loss 5%

report:
  out: results/my-scenario.json
```

Drop it in `topologies/<name>/scenarios/` and invoke with:

```bash
make scenario SCEN=my-scenario
```

Fault `target` accepts `plane: N` (all hosts), `host: NAME` (all four
NICs on that host), or both together (one specific NIC).

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `KeyError: 'src_addr'` or similar in `spray` | Schema drift between `srv6_fabric/reorder.py` and consumers | Re-read `design-mrc.md` per-flow schema; both sides must use `src/dst/sport/dport/received/...` |
| Every flow shows "no flow at receiver" + matching "orphan flow" warning | IPv6 string-form mismatch (zero-padded vs RFC 5952 compressed) | Already fixed in `srv6_fabric/report.py:_canon_addr()`; if it returns, check that both sides go through `_canon_addr` |
| Sender PPS far below requested (e.g., 780 of 1000) | scapy packet build in the hot loop, host load | Not a correctness issue. Future optimization: precompute per-plane packet bytes, patch only seq+plane offsets |
| `policy_to_cli: NotImplementedError` for `health_aware` | health-aware policy not yet wired through CLI | Use `round_robin` or `hash5tuple` until `srv6_fabric/health.py` is connected to the runner |
| `tc qdisc show` shows leftover `netem` after a run | orchestrator crashed before revert (rare) | `docker exec <host> tc qdisc del dev <nic> root` |

## Post-redeploy validation

After any `make teardown && make deploy && make config` cycle, run
through these in order — each step gates the next:

1. `docker exec green-host00 which spray` — package installed in image
2. `docker exec green-host00 spray --help` — CLI loads cleanly
3. `docker exec green-host00 sh -c 'echo $SRV6_TOPO && cat $SRV6_TOPO | head'`
   — topo.yaml baked into image
4. Manual two-host spray (above) — fabric carries packets
5. Same with `--json` — receiver schema parses
6. Same with `--policy hash5tuple` — policy plumbing works
7. `run-scenario topologies/4p-8x16/scenarios/baseline.yaml --dry-run`
   — scenario parses
8. `make scenario SCEN=baseline` — orchestrator + merge + render pipeline works
9. Three fault scenarios in order — netem inject + revert works
10. Final `tc qdisc show` sweep — clean state

If a step fails, stop and troubleshoot before proceeding.
